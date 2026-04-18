from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from src.models import PortfolioState

logger = logging.getLogger(__name__)

_30M_SECONDS = 1800


class CircuitBreaker:
    """Intraday trading halt system with three independent triggers.

    1. Rolling 30-min drawdown  >= half_hour_dd_limit  → timed pause
    2. Daily drawdown           >= daily_dd_limit       → session-wide stop
    3. Consecutive losses       >= consecutive_loss_pause → timed pause
    """

    def __init__(self, config: dict) -> None:
        risk = config["risk"]
        self._half_hour_dd_limit: float = risk["half_hour_dd_limit"]
        self._daily_dd_limit: float = risk["daily_dd_limit"]
        self._consecutive_loss_pause: int = risk["consecutive_loss_pause"]
        self._pause_duration_minutes: int = risk["pause_duration_minutes"]

        self._is_active: bool = False
        self._resume_time: Optional[datetime] = None
        self._pause_reason: str = ""

        self._30m_mark: Optional[float] = None
        self._30m_mark_time: Optional[datetime] = None

        self._consecutive_losses: int = 0
        self._daily_loss_stopped: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update(
        self,
        portfolio_state: PortfolioState,
        now: datetime,
    ) -> tuple[bool, str]:
        """Evaluate all circuit-breaker conditions.

        Returns (is_active, pause_reason).  Call once per bar.
        """
        # Recover from a timed pause if the clock has passed resume_time
        if self._is_active and not self._daily_loss_stopped:
            if self._resume_time is not None and now >= self._resume_time:
                self._is_active = False
                self._pause_reason = ""
                self._resume_time = None
                logger.info("CIRCUIT_BREAKER_RESUMED at %s", now)

        # Daily hard-stop takes permanent precedence until reset_session()
        if self._daily_loss_stopped:
            return (True, "DAILY_LOSS_STOP")

        equity = portfolio_state.equity

        # ---- Rolling 30-min mark maintenance ----
        if self._30m_mark is None or self._30m_mark_time is None:
            self._30m_mark = equity
            self._30m_mark_time = now
        else:
            elapsed = (now - self._30m_mark_time).total_seconds()
            if elapsed >= _30M_SECONDS:
                self._30m_mark = equity
                self._30m_mark_time = now

        # ---- Check 30-min drawdown ----
        if (
            not self._is_active
            and self._30m_mark is not None
            and self._30m_mark > 0
        ):
            dd_30m = (self._30m_mark - equity) / self._30m_mark
            if dd_30m >= self._half_hour_dd_limit:
                self._activate("HALF_HOUR_DD", self._pause_duration_minutes, now)

        # ---- Check daily drawdown ----
        session_open_equity = portfolio_state.session_open_equity
        if session_open_equity > 0:
            daily_dd = (session_open_equity - equity) / session_open_equity
            if daily_dd >= self._daily_dd_limit:
                self._daily_loss_stopped = True
                self._activate("DAILY_LOSS_STOP", pause_minutes=0, now=now)
                logger.warning(
                    "CIRCUIT_BREAKER daily stop: DD=%.2f%% (open=%.2f, now=%.2f)",
                    daily_dd * 100,
                    session_open_equity,
                    equity,
                )

        return (self.is_active(), self._pause_reason)

    def record_trade_result(self, was_win: bool) -> None:
        """Update the consecutive-loss counter; trigger a pause when threshold hit."""
        if was_win:
            self._consecutive_losses = 0
        else:
            self._consecutive_losses += 1
            if self._consecutive_losses >= self._consecutive_loss_pause:
                # _activate needs a reference time; use utcnow as a fallback
                now = datetime.now()  # naive, consistent with rest of codebase
                self._activate("CONSECUTIVE_LOSSES", self._pause_duration_minutes, now)
                self._consecutive_losses = 0

    def is_active(self) -> bool:
        """Return True if any circuit-breaker condition is currently active."""
        return self._is_active or self._daily_loss_stopped

    def get_reason(self) -> str:
        """Return the human-readable reason for the current pause."""
        return self._pause_reason

    def reset_session(self) -> None:
        """Clear all state for a new trading session."""
        self._is_active = False
        self._resume_time = None
        self._pause_reason = ""
        self._30m_mark = None
        self._30m_mark_time = None
        self._consecutive_losses = 0
        self._daily_loss_stopped = False
        logger.info("CircuitBreaker: session reset")

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _activate(self, reason: str, pause_minutes: int, now: datetime) -> None:
        self._is_active = True
        self._pause_reason = reason
        if pause_minutes > 0:
            self._resume_time = now + timedelta(minutes=pause_minutes)
        else:
            self._resume_time = None  # indefinite — only reset_session can clear
        logger.warning(
            "CIRCUIT_BREAKER_ACTIVATED: %s, resumes %s",
            reason,
            self._resume_time,
        )
