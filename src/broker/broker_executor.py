from __future__ import annotations

import logging
import math
import traceback
from datetime import datetime, timezone
from typing import Optional

from alpaca.trading.enums import OrderSide, OrderType, TimeInForce
from alpaca.trading.requests import (
    LimitOrderRequest,
    MarketOrderRequest,
    StopLimitOrderRequest,
    StopOrderRequest,
)

from src.broker.alpaca_client import AlpacaClient
from src.models import AssetClass, Direction, ExitReason, Position, Signal

logger = logging.getLogger(__name__)

_BREAKOUT_SLIP = 0.0005    # limit offset for breakout stop-limit entries
_MR_SLIP = 0.001           # limit offset for mean-reversion limit entries
_MIN_NOTIONAL_CRYPTO = 1.0  # $1 minimum for crypto
_MIN_QTY_EQUITY = 1        # 1 share minimum for equity


class BrokerExecutor:
    """Translates Signal / Position objects into Alpaca REST order calls."""

    def __init__(self, alpaca_client: AlpacaClient, config: dict) -> None:
        self._client = alpaca_client
        self._rest = alpaca_client._rest
        self._config = config
        # order_id → submission time, used by handle_partial_fill
        self._order_times: dict[str, datetime] = {}

    # ------------------------------------------------------------------
    # Entry orders
    # ------------------------------------------------------------------

    def submit_entry_order(
        self,
        signal: Signal,
        account_equity: float,
    ) -> dict | None:
        """Build and submit an entry order.  Returns order dict or None."""

        side = (
            OrderSide.BUY if signal.direction is Direction.LONG else OrderSide.SELL
        )
        qty = math.floor(signal.size_pct * account_equity / signal.entry_price)

        # Minimum-size guard
        is_crypto = signal.asset_class is AssetClass.CRYPTO
        if is_crypto:
            if qty * signal.entry_price < _MIN_NOTIONAL_CRYPTO:
                logger.warning(
                    "submit_entry_order(%s): qty %d below min notional — skipping",
                    signal.asset, qty,
                )
                return None
        else:
            if qty < _MIN_QTY_EQUITY:
                logger.warning(
                    "submit_entry_order(%s): qty %d below 1 share — skipping",
                    signal.asset, qty,
                )
                return None

        try:
            order_req = self._build_entry_request(signal, side, qty)
            order = self._rest.submit_order(order_req)
            order_dict = self._order_to_dict(order)
            self._order_times[order_dict["id"]] = datetime.now(timezone.utc)
            logger.info(
                "ENTRY_ORDER submitted: %s %s %s qty=%d type=%s",
                signal.direction.value, signal.asset,
                signal.strategy_name, qty, order_dict["type"],
            )
            return order_dict
        except Exception:
            logger.error(
                "submit_entry_order(%s) failed:\n%s", signal.asset, traceback.format_exc()
            )
            return None

    # ------------------------------------------------------------------
    # Stop / protective orders
    # ------------------------------------------------------------------

    def submit_stop_order(self, position: Position) -> str | None:
        """Submit a GTC stop-market order for the full position.

        CRITICAL: call immediately on fill confirmation, never defer.
        """
        # Protective stop is on the opposite side from the position direction
        stop_side = (
            OrderSide.SELL if position.direction is Direction.LONG else OrderSide.BUY
        )
        qty = abs(position.shares)
        try:
            req = StopOrderRequest(
                symbol=position.asset,
                qty=qty,
                side=stop_side,
                stop_price=round(position.stop_price, 2),
                time_in_force=TimeInForce.GTC,
            )
            order = self._rest.submit_order(req)
            order_id = str(order.id)
            logger.info(
                "STOP_ORDER submitted: %s stop=%.4f id=%s",
                position.asset, position.stop_price, order_id,
            )
            return order_id
        except Exception:
            logger.error(
                "submit_stop_order(%s) failed:\n%s", position.asset, traceback.format_exc()
            )
            return None

    # ------------------------------------------------------------------
    # Cancel / close helpers
    # ------------------------------------------------------------------

    def cancel_order(self, order_id: str) -> bool:
        """Cancel a single order.  Returns True on success, False if already filled."""
        try:
            self._rest.cancel_order_by_id(order_id)
            logger.info("ORDER_CANCELLED: %s", order_id)
            return True
        except Exception as exc:
            msg = str(exc).lower()
            if "already filled" in msg or "not cancelable" in msg or "404" in msg:
                logger.debug("cancel_order(%s): already filled/gone — %s", order_id, exc)
                return False
            logger.error(
                "cancel_order(%s) unexpected error:\n%s", order_id, traceback.format_exc()
            )
            return False

    def cancel_all_open_orders(self, asset: str | None = None) -> int:
        """Cancel all non-stop open orders.  Preserve protective stops.

        Args:
            asset: if given, restrict to this symbol only.
        Returns:
            Number of orders actually cancelled.
        """
        try:
            open_orders = self._client.get_open_orders()
        except Exception:
            logger.error("cancel_all_open_orders: could not fetch open orders\n%s",
                         traceback.format_exc())
            return 0

        _STOP_TYPES = {"stop", "stop_limit", "trailing_stop"}
        cancelled = 0
        for o in open_orders:
            if asset is not None and o.get("symbol") != asset:
                continue
            if o.get("type", "").lower() in _STOP_TYPES:
                continue  # preserve protective stops
            if self.cancel_order(o["id"]):
                cancelled += 1
        return cancelled

    def close_position(self, asset: str, reason: ExitReason) -> dict | None:
        """Submit a market order to close the full position for *asset*."""
        try:
            order = self._rest.close_position(asset)
            order_dict = self._order_to_dict(order)
            logger.info(
                "POSITION_CLOSED: %s reason=%s order_id=%s",
                asset, reason.value, order_dict["id"],
            )
            return order_dict
        except Exception:
            logger.error(
                "close_position(%s) failed:\n%s", asset, traceback.format_exc()
            )
            return None

    def close_all_positions(self, reason: ExitReason) -> list[dict]:
        """Close every open position.  Used by EOD-flat and SIGTERM handlers."""
        try:
            positions = self._client.get_positions()
        except Exception:
            logger.error(
                "close_all_positions: could not fetch positions\n%s", traceback.format_exc()
            )
            return []

        closed: list[dict] = []
        for pos in positions:
            result = self.close_position(pos["symbol"], reason)
            if result:
                closed.append(result)
        return closed

    # ------------------------------------------------------------------
    # Partial-fill handling
    # ------------------------------------------------------------------

    def handle_partial_fill(
        self,
        order_id: str,
        filled_qty: float,
        target_qty: float,
        timeout_seconds: int = 30,
    ) -> bool:
        """Cancel the unfilled remainder when a partial fill stalls.

        Returns:
            True  — remainder cancelled, partial fill accepted.
            False — fill ratio acceptable (≥80%) or timeout not yet elapsed.
        """
        if target_qty <= 0:
            return False

        fill_ratio = filled_qty / target_qty
        if fill_ratio >= 0.80:
            return False  # fill is adequate — no action needed

        # Check whether the timeout has elapsed since submission
        submit_time = self._order_times.get(order_id)
        if submit_time is not None:
            now = datetime.now(timezone.utc)
            if submit_time.tzinfo is None:
                submit_time = submit_time.replace(tzinfo=timezone.utc)
            elapsed = (now - submit_time).total_seconds()
            if elapsed < timeout_seconds:
                return False  # still within the wait window

        cancelled = self.cancel_order(order_id)
        if cancelled:
            logger.warning(
                "PARTIAL_FILL_ACCEPTED: order=%s filled=%.2f/%.2f (%.0f%%)",
                order_id, filled_qty, target_qty, fill_ratio * 100,
            )
        return True

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _build_entry_request(self, signal: Signal, side: OrderSide, qty: float):
        """Select order type from strategy name and build the request object."""
        name = signal.strategy_name.lower()
        price = signal.entry_price
        is_long = signal.direction is Direction.LONG

        if "momentum" in name:
            return MarketOrderRequest(
                symbol=signal.asset,
                qty=qty,
                side=side,
                time_in_force=TimeInForce.DAY,
            )

        if "breakout" in name:
            # Enter on a stop-limit: chases the breakout with a tight limit
            if is_long:
                stop_p = round(price * (1 + _BREAKOUT_SLIP), 4)
                limit_p = round(price * (1 + _BREAKOUT_SLIP * 2), 4)
            else:
                stop_p = round(price * (1 - _BREAKOUT_SLIP), 4)
                limit_p = round(price * (1 - _BREAKOUT_SLIP * 2), 4)
            return StopLimitOrderRequest(
                symbol=signal.asset,
                qty=qty,
                side=side,
                stop_price=stop_p,
                limit_price=limit_p,
                time_in_force=TimeInForce.DAY,
            )

        if "mean_reversion" in name:
            # Limit below market for LONG (buy dip), above for SHORT (sell bounce)
            if is_long:
                limit_p = round(price * (1 - _MR_SLIP), 4)
            else:
                limit_p = round(price * (1 + _MR_SLIP), 4)
            return LimitOrderRequest(
                symbol=signal.asset,
                qty=qty,
                side=side,
                limit_price=limit_p,
                time_in_force=TimeInForce.DAY,
            )

        # Default: limit at current close
        return LimitOrderRequest(
            symbol=signal.asset,
            qty=qty,
            side=side,
            limit_price=round(price, 4),
            time_in_force=TimeInForce.DAY,
        )

    @staticmethod
    def _order_to_dict(order) -> dict:
        return {
            "id": str(order.id),
            "symbol": order.symbol,
            "qty": float(order.qty) if order.qty else None,
            "side": order.side.value if hasattr(order.side, "value") else str(order.side),
            "type": order.type.value if hasattr(order.type, "value") else str(order.type),
            "status": order.status.value if hasattr(order.status, "value") else str(order.status),
            "limit_price": float(order.limit_price) if order.limit_price else None,
            "stop_price": float(order.stop_price) if order.stop_price else None,
            "created_at": order.created_at,
        }
