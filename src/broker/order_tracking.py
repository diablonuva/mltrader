from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timezone
from typing import Optional

from src.models import (
    AssetClass,
    CompletedTrade,
    Direction,
    ExitReason,
    Position,
    RegimeLabel,
    Signal,
)

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class OrderTracker:
    """Records all order events and maintains authoritative in-memory position state.

    The single source of truth for what is open and what has been completed.
    """

    def __init__(self, config: dict) -> None:
        log_dir = config.get("monitoring", {}).get("log_dir", "logs")
        self._log_file = os.path.join(log_dir, "trades.log")

        self._open_orders: dict[str, dict] = {}          # order_id → order info
        self._positions: dict[str, Position] = {}         # symbol → Position
        self._stop_orders: dict[str, str] = {}            # symbol → stop_order_id
        self._trade_history: list[CompletedTrade] = []

    # ------------------------------------------------------------------
    # Fill events
    # ------------------------------------------------------------------

    def on_entry_filled(
        self,
        order_id: str,
        fill_price: float,
        fill_qty: float,
        signal: Signal,
    ) -> Position:
        """Create and register a Position from a confirmed entry fill."""
        position = Position(
            asset=signal.asset,
            direction=signal.direction,
            entry_price=fill_price,
            current_price=fill_price,
            shares=fill_qty,
            entry_time=_utcnow(),
            stop_price=signal.stop_price,
            take_profit_price=signal.take_profit_price,
            max_hold_bars=signal.max_hold_bars,
            bars_held=0,
            stop_order_id="",
            strategy_name=signal.strategy_name,
            regime_at_entry=signal.regime,
        )
        self._positions[signal.asset] = position
        self._open_orders[order_id] = {
            "order_id": order_id,
            "symbol": signal.asset,
            "fill_price": fill_price,
            "fill_qty": fill_qty,
            "side": "entry",
        }
        logger.info(
            "ENTRY_FILLED: symbol=%s dir=%s qty=%.4f price=%.4f strategy=%s",
            signal.asset,
            signal.direction.value,
            fill_qty,
            fill_price,
            signal.strategy_name,
        )
        return position

    def on_exit_filled(
        self,
        order_id: str,
        fill_price: float,
        fill_qty: float,
        exit_reason: ExitReason,
    ) -> CompletedTrade:
        """Close a position and record the completed trade.

        Looks up the position by symbol from the tracked order.  If the order
        isn't tracked (e.g. a manual close), it searches _positions for a
        unique open position — useful in tests.
        """
        # Resolve symbol from open order; fall back to single-position search
        symbol: Optional[str] = None
        order_info = self._open_orders.get(order_id)
        if order_info:
            symbol = order_info["symbol"]
        elif len(self._positions) == 1:
            symbol = next(iter(self._positions))

        if symbol is None or symbol not in self._positions:
            raise ValueError(
                f"on_exit_filled: no open position found for order_id={order_id!r}"
            )

        position = self._positions[symbol]

        # PnL
        if position.direction is Direction.LONG:
            pnl_pct = (fill_price - position.entry_price) / position.entry_price
        else:
            pnl_pct = (position.entry_price - fill_price) / position.entry_price
        pnl_dollar = pnl_pct * position.entry_price * fill_qty

        trade = CompletedTrade(
            asset=symbol,
            direction=position.direction,
            entry_price=position.entry_price,
            exit_price=fill_price,
            shares=fill_qty,
            entry_time=position.entry_time,
            exit_time=_utcnow(),
            pnl_pct=pnl_pct,
            pnl_dollar=pnl_dollar,
            regime_at_entry=position.regime_at_entry,
            strategy_name=position.strategy_name,
            hold_bars=position.bars_held,
            exit_reason=exit_reason,
        )

        # Clean up state
        del self._positions[symbol]
        if symbol in self._stop_orders:
            orphaned_stop = self._stop_orders.pop(symbol)
            logger.info("ORPHANED_STOP flagged for cancellation: %s", orphaned_stop)

        self._trade_history.append(trade)
        self._log_trade(trade)

        logger.info(
            "TRADE_COMPLETED: symbol=%s dir=%s pnl=%.2f%% ($%.2f) reason=%s",
            trade.asset,
            trade.direction.value,
            trade.pnl_pct * 100,
            trade.pnl_dollar,
            trade.exit_reason.value,
        )
        return trade

    # ------------------------------------------------------------------
    # Stop-order tracking
    # ------------------------------------------------------------------

    def register_stop_order(self, symbol: str, stop_order_id: str) -> None:
        """Link a stop order ID to a position symbol."""
        self._stop_orders[symbol] = stop_order_id
        if symbol in self._positions:
            self._positions[symbol].stop_order_id = stop_order_id

    def detect_orphaned_stops(self) -> list[str]:
        """Return stop order IDs whose parent position no longer exists."""
        return [
            stop_id
            for symbol, stop_id in self._stop_orders.items()
            if symbol not in self._positions
        ]

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get_open_positions(self) -> dict[str, Position]:
        return dict(self._positions)

    def get_trade_history(self) -> list[CompletedTrade]:
        return list(self._trade_history)

    def get_todays_trades(self) -> list[CompletedTrade]:
        today = date.today()
        return [t for t in self._trade_history if t.entry_time.date() == today]

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _log_trade(self, trade: CompletedTrade) -> None:
        """Append a JSON-lines record to trades.log."""
        try:
            os.makedirs(os.path.dirname(self._log_file) or ".", exist_ok=True)
            record = {
                "asset": trade.asset,
                "direction": trade.direction.value,
                "entry_price": trade.entry_price,
                "exit_price": trade.exit_price,
                "shares": trade.shares,
                "entry_time": trade.entry_time.isoformat(),
                "exit_time": trade.exit_time.isoformat(),
                "pnl_pct": round(trade.pnl_pct, 6),
                "pnl_dollar": round(trade.pnl_dollar, 4),
                "regime_at_entry": trade.regime_at_entry.value,
                "strategy_name": trade.strategy_name,
                "hold_bars": trade.hold_bars,
                "exit_reason": trade.exit_reason.value,
            }
            with open(self._log_file, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
        except OSError as exc:
            logger.warning("Could not write to trades.log: %s", exc)
