from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import IO, Optional

from src.models import (
    CompletedTrade,
    PortfolioState,
    RiskDecision,
    Signal,
)

# ---------------------------------------------------------------------------
# Root logger bootstrap — called once on import
# ---------------------------------------------------------------------------

def _bootstrap_root_logger(log_dir: str) -> None:
    """Configure the Python root logger with a Rich console handler (DEBUG)
    and a rotating file handler (INFO) if not already configured."""
    root = logging.getLogger()
    if root.handlers:
        return  # already configured — don't double-add

    root.setLevel(logging.DEBUG)

    # Console: Rich if available, else standard StreamHandler
    try:
        from rich.logging import RichHandler
        console_handler = RichHandler(
            level=logging.DEBUG,
            show_time=True,
            rich_tracebacks=True,
        )
    except ImportError:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.DEBUG)
        console_handler.setFormatter(
            logging.Formatter("%(asctime)s  %(levelname)-8s  %(name)s  %(message)s")
        )

    root.addHandler(console_handler)

    # File: INFO+ to logs/app.log
    app_log_path = os.path.join(log_dir, "app.log")
    try:
        file_handler = logging.FileHandler(app_log_path, encoding="utf-8")
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(
            logging.Formatter(
                "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s"
            )
        )
        root.addHandler(file_handler)
    except OSError as exc:
        root.warning("Could not open log file %s: %s", app_log_path, exc)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write(fh: IO, record: dict) -> None:
    fh.write(json.dumps(record) + "\n")
    fh.flush()


logger = logging.getLogger(__name__)


class StructuredLogger:
    """Writes structured JSON Lines to discrete log files per event type.

    Also maintains shared_state.json for real-time dashboard consumption.
    """

    def __init__(self, config: dict) -> None:
        mon = config["monitoring"]
        self._log_dir: str = mon["log_dir"]
        self._shared_state_path: str = mon["shared_state_file"]

        os.makedirs(self._log_dir, exist_ok=True)

        def _open(name: str) -> IO:
            return open(
                os.path.join(self._log_dir, name), "a", encoding="utf-8"
            )

        self._trades_fh = _open("trades.log")
        self._orders_fh = _open("orders.log")
        self._regime_fh = _open("regime.log")
        self._session_fh = _open("session.log")
        self._pnl_fh = _open("pnl.log")

        _bootstrap_root_logger(self._log_dir)

    # ------------------------------------------------------------------
    # Trade events
    # ------------------------------------------------------------------

    def log_trade(self, trade: CompletedTrade) -> None:
        record = {
            "event": "TRADE_COMPLETED",
            "ts": _now_iso(),
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
        _write(self._trades_fh, record)
        logger.info(
            "TRADE  %s %s  pnl=%.2f%%  ($%.2f)  reason=%s",
            trade.direction.value, trade.asset,
            trade.pnl_pct * 100, trade.pnl_dollar,
            trade.exit_reason.value,
        )

    # ------------------------------------------------------------------
    # Order events
    # ------------------------------------------------------------------

    def log_order(
        self,
        order_id: str,
        asset: str,
        action: str,
        qty: float,
        price: float,
        status: str,
        reason: str = "",
    ) -> None:
        record = {
            "event": "ORDER",
            "ts": _now_iso(),
            "order_id": order_id,
            "asset": asset,
            "action": action,
            "qty": qty,
            "price": price,
            "status": status,
            "reason": reason,
        }
        _write(self._orders_fh, record)
        logger.info(
            "ORDER  %s %s  qty=%s  price=%s  status=%s",
            action, asset, qty, price, status,
        )

    # ------------------------------------------------------------------
    # Regime events
    # ------------------------------------------------------------------

    def log_regime_change(
        self,
        asset: str,
        old_regime: str,
        new_regime: str,
        confidence: float,
        timestamp: datetime,
    ) -> None:
        record = {
            "event": "REGIME_CHANGE",
            "ts": _now_iso(),
            "asset": asset,
            "old_regime": str(old_regime),
            "new_regime": str(new_regime),
            "confidence": round(confidence, 4),
            "bar_timestamp": timestamp.isoformat() if hasattr(timestamp, "isoformat") else str(timestamp),
        }
        _write(self._regime_fh, record)
        logger.info(
            "REGIME  %s  %s → %s  conf=%.3f",
            asset, old_regime, new_regime, confidence,
        )

    # ------------------------------------------------------------------
    # Session events
    # ------------------------------------------------------------------

    def log_session_open(
        self,
        assets: list,
        equity: float,
        timestamp: datetime,
    ) -> None:
        record = {
            "event": "SESSION_OPEN",
            "ts": _now_iso(),
            "assets": assets,
            "equity": round(equity, 2),
            "bar_timestamp": timestamp.isoformat() if hasattr(timestamp, "isoformat") else str(timestamp),
        }
        _write(self._session_fh, record)
        logger.info("SESSION_OPEN  equity=$%.2f  assets=%s", equity, assets)

    def log_session_close(
        self,
        trades_today: int,
        pnl: float,
        equity: float,
        timestamp: datetime,
    ) -> None:
        record = {
            "event": "SESSION_CLOSE",
            "ts": _now_iso(),
            "trades_today": trades_today,
            "pnl_dollar": round(pnl, 4),
            "equity": round(equity, 2),
            "bar_timestamp": timestamp.isoformat() if hasattr(timestamp, "isoformat") else str(timestamp),
        }
        _write(self._session_fh, record)
        logger.info(
            "SESSION_CLOSE  trades=%d  pnl=$%.2f  equity=$%.2f",
            trades_today, pnl, equity,
        )

    # ------------------------------------------------------------------
    # PnL events
    # ------------------------------------------------------------------

    def log_daily_pnl(
        self,
        date: object,
        pnl_dollar: float,
        pnl_pct: float,
        equity: float,
        timestamp: datetime,
    ) -> None:
        record = {
            "event": "DAILY_PNL",
            "ts": _now_iso(),
            "date": str(date),
            "pnl_dollar": round(pnl_dollar, 4),
            "pnl_pct": round(pnl_pct, 6),
            "equity": round(equity, 2),
            "bar_timestamp": timestamp.isoformat() if hasattr(timestamp, "isoformat") else str(timestamp),
        }
        _write(self._pnl_fh, record)
        logger.info(
            "DAILY_PNL  %s  $%.2f (%.2f%%)  equity=$%.2f",
            date, pnl_dollar, pnl_pct * 100, equity,
        )

    # ------------------------------------------------------------------
    # Risk events
    # ------------------------------------------------------------------

    def log_risk_decision(
        self,
        signal: Signal,
        decision: RiskDecision,
        timestamp: datetime,
    ) -> None:
        record = {
            "event": "RISK_DECISION",
            "ts": _now_iso(),
            "asset": signal.asset,
            "direction": signal.direction.value,
            "size_pct": round(signal.size_pct, 4),
            "approved": decision.approved,
            "modified": decision.modified,
            "rejected": decision.rejected,
            "reason_code": decision.reason_code,
            "modifications": decision.modifications,
            "bar_timestamp": timestamp.isoformat() if hasattr(timestamp, "isoformat") else str(timestamp),
        }
        _write(self._orders_fh, record)
        if decision.rejected:
            logger.debug(
                "RISK_REJECT  %s %s  reason=%s",
                signal.direction.value, signal.asset, decision.reason_code,
            )

    def log_circuit_breaker(
        self,
        reason: str,
        timestamp: datetime,
        equity: float,
    ) -> None:
        record = {
            "event": "CIRCUIT_BREAKER",
            "ts": _now_iso(),
            "reason": reason,
            "equity": round(equity, 2),
            "bar_timestamp": timestamp.isoformat() if hasattr(timestamp, "isoformat") else str(timestamp),
        }
        _write(self._session_fh, record)
        logger.warning("CIRCUIT_BREAKER  reason=%s  equity=$%.2f", reason, equity)

    # ------------------------------------------------------------------
    # Shared state for dashboard
    # ------------------------------------------------------------------

    def update_shared_state(
        self,
        portfolio_state: PortfolioState,
        regime_info: dict,
        signal_history: list,
        equity_curve: list,
        training_bars: int = 0,
        training_needed: int = 0,
        hmm_trained: Optional[bool] = None,
    ) -> None:
        """Write dashboard snapshot JSON to shared_state_path (atomic write)."""
        positions_serialised = {}
        for sym, pos in portfolio_state.positions.items():
            positions_serialised[sym] = {
                "direction": pos.direction.value,
                "entry_price": pos.entry_price,
                "current_price": pos.current_price,
                "shares": pos.shares,
                "entry_time": pos.entry_time.isoformat() if hasattr(pos.entry_time, "isoformat") else str(pos.entry_time),
                "stop_price": pos.stop_price,
                "take_profit_price": pos.take_profit_price,
                "bars_held": pos.bars_held,
                "unrealised_pnl_pct": round(pos.unrealised_pnl_pct, 6),
            }

        # Last 30 minutes of equity curve
        equity_curve_30m = []
        if equity_curve:
            cutoff = equity_curve[-1][1] if equity_curve else 0
            # Use last 6 bars (6 × 5 min = 30 min)
            for ts, eq in equity_curve[-6:]:
                equity_curve_30m.append(
                    (ts.isoformat() if hasattr(ts, "isoformat") else str(ts), eq)
                )

        # Last 10 signals
        last_10: list[dict] = []
        for entry in signal_history[-10:]:
            sig = entry.get("signal")
            last_10.append({
                "ts": entry.get("ts").isoformat() if hasattr(entry.get("ts"), "isoformat") else str(entry.get("ts")),
                "reason": entry.get("reason", ""),
                "asset": sig.asset if sig else None,
                "direction": sig.direction.value if sig else None,
                "size_pct": sig.size_pct if sig else None,
            })

        state = {
            "timestamp": _now_iso(),
            "equity": round(portfolio_state.equity, 2),
            "cash": round(portfolio_state.cash, 2),
            "buying_power": round(portfolio_state.buying_power, 2),
            "daily_pnl": round(portfolio_state.daily_pnl, 4),
            "circuit_breaker_active": portfolio_state.circuit_breaker_active,
            "positions": positions_serialised,
            "regime_info": regime_info,
            "last_10_signals": last_10,
            "equity_curve_30m": equity_curve_30m,
            "training_bars": training_bars,
            "training_needed": training_needed,
            "training_pct": round(min(training_bars / training_needed * 100, 100), 1) if training_needed else 100.0,
            # Source-of-truth: explicit flag from caller. Fall back to
            # regime-derivation only when caller didn't supply it (back-compat).
            "hmm_trained": (
                hmm_trained
                if hmm_trained is not None
                else any(r.get("regime", "UNKNOWN") != "UNKNOWN" for r in regime_info.values())
            ),
        }

        # Atomic write: temp file then rename
        tmp_path = self._shared_state_path + ".tmp"
        try:
            os.makedirs(
                os.path.dirname(self._shared_state_path) or ".", exist_ok=True
            )
            with open(tmp_path, "w", encoding="utf-8") as fh:
                json.dump(state, fh, indent=2)
            os.replace(tmp_path, self._shared_state_path)
        except OSError as exc:
            logger.warning("update_shared_state failed: %s", exc)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def flush(self) -> None:
        for fh in (
            self._trades_fh, self._orders_fh, self._regime_fh,
            self._session_fh, self._pnl_fh,
        ):
            try:
                fh.flush()
            except OSError:
                pass

    def close(self) -> None:
        self.flush()
        for fh in (
            self._trades_fh, self._orders_fh, self._regime_fh,
            self._session_fh, self._pnl_fh,
        ):
            try:
                fh.close()
            except OSError:
                pass
