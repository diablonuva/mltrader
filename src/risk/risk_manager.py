from __future__ import annotations

import logging
from datetime import datetime

from src.models import AssetClass, PortfolioState, RiskDecision, Signal
from src.risk.circuit_breaker import CircuitBreaker
from src.risk.pdt_guard import PDTGuard
from src.session.session_manager import SessionManager

logger = logging.getLogger(__name__)

_MIN_SIZE_PCT = 0.05   # positions smaller than this are not worth opening


class RiskManager:
    """Master risk veto layer.  Every Signal must pass through evaluate()
    before any order reaches the broker.  Cannot be bypassed.
    """

    def __init__(
        self,
        config: dict,
        pdt_guard: PDTGuard,
        circuit_breaker: CircuitBreaker,
        session_manager: SessionManager,
    ) -> None:
        self._cfg = config
        self._risk = config["risk"]
        self._pdt_guard = pdt_guard
        self._circuit_breaker = circuit_breaker
        self._session_manager = session_manager

        self._daily_trade_counts: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evaluate(
        self,
        signal: Signal,
        portfolio_state: PortfolioState,
        now: datetime,
    ) -> RiskDecision:
        """Evaluate *signal* against all risk rules.

        Checks are ordered so the most critical (circuit-breaker, PDT) run
        first.  The first failure immediately returns a rejection so we never
        partially process a bad signal.
        """

        # 1. Circuit-breaker check
        cb_active, cb_reason = self._circuit_breaker.update(portfolio_state, now)
        if cb_active:
            return RiskDecision.reject(f"CIRCUIT_BREAKER_{cb_reason}")

        # 2. PDT check
        if not self._pdt_guard.can_trade(signal.asset, portfolio_state.equity):
            return RiskDecision.reject("PDT_LIMIT")

        # 3. Daily trade-count cap
        asset_class = AssetClass.from_symbol(signal.asset)
        max_daily = (
            self._risk.get("max_trades_per_day_crypto", 0)  # 0 = block crypto if key absent
            if asset_class is AssetClass.CRYPTO
            else self._risk["max_trades_per_day_equity"]
        )
        count_today = self._daily_trade_counts.get(signal.asset, 0)
        if count_today >= max_daily:
            return RiskDecision.reject("DAILY_TRADE_LIMIT")

        # 4. Exposure / size cap
        proposed_exp = self._compute_proposed_exposure(signal, portfolio_state)
        max_exp = self._risk["max_total_exposure_pct"] * portfolio_state.equity

        if proposed_exp > max_exp:
            safe_size = self._compute_max_safe_size(signal, portfolio_state)
            if safe_size < _MIN_SIZE_PCT:
                return RiskDecision.reject("EXPOSURE_LIMIT")
            return RiskDecision(
                approved=True,
                modified=True,
                rejected=False,
                reason_code="SIZE_REDUCED",
                modifications={"size_pct": safe_size},
            )

        # 5. Max simultaneous positions
        open_positions = len(portfolio_state.positions)
        if open_positions >= self._risk["max_simultaneous_positions"]:
            return RiskDecision.reject("MAX_POSITIONS_OPEN")

        # 6. EOD hard-close gate
        if self._session_manager.is_eod_hard_close(signal.asset, now):
            return RiskDecision.reject("EOD_HARD_CLOSE")

        return RiskDecision.approve()

    def evaluate_eod_close(
        self,
        portfolio_state: PortfolioState,
        now: datetime,
    ) -> list[str]:
        """Return equity asset symbols that should be closed at EOD soft-close."""
        return [
            asset
            for asset in portfolio_state.positions
            if (
                AssetClass.from_symbol(asset) is AssetClass.EQUITY
                and self._session_manager.is_eod_soft_close(asset, now)
            )
        ]

    def record_trade_open(self, asset: str) -> None:
        """Increment the daily trade counter for *asset*.

        Call this after receiving a fill confirmation, not before.
        """
        self._daily_trade_counts[asset] = (
            self._daily_trade_counts.get(asset, 0) + 1
        )

    def reset_daily_counts(self) -> None:
        """Reset all per-asset trade counters.  Call at each session open."""
        self._daily_trade_counts.clear()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _compute_proposed_exposure(
        self,
        signal: Signal,
        portfolio_state: PortfolioState,
    ) -> float:
        """Dollar exposure of the proposed new position."""
        return signal.size_pct * portfolio_state.equity

    def _compute_max_safe_size(
        self,
        signal: Signal,
        portfolio_state: PortfolioState,
    ) -> float:
        """Largest size_pct that keeps total portfolio exposure within limits.

        Uses remaining capacity: max_exposure - current_open_exposure.
        """
        max_exp = self._risk["max_total_exposure_pct"] * portfolio_state.equity
        current_exp = sum(
            pos.shares * pos.current_price
            for pos in portfolio_state.positions.values()
        )
        remaining = max_exp - current_exp
        if portfolio_state.equity <= 0:
            return 0.0
        return max(0.0, remaining / portfolio_state.equity)
