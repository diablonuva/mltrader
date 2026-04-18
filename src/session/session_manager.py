from __future__ import annotations

from datetime import datetime, timedelta

import pytz

from src.session.market_calendar import MarketCalendar


class SessionManager:
    """Answers all session-related questions per asset and timestamp.

    Equity assets follow NYSE hours; crypto assets run 24/7.
    """

    def __init__(self, config: dict) -> None:
        cfg = config["session"]
        self._tz = pytz.timezone(cfg.get("timezone", "America/New_York"))
        self._calendar = MarketCalendar(cfg.get("timezone", "America/New_York"))

        self._entry_blackout_open_min: int = cfg.get("entry_blackout_open_minutes", 5)
        self._eod_soft_close_min: int = cfg.get("eod_soft_close_minutes", 10)
        self._eod_hard_close_min: int = cfg.get("eod_hard_close_minutes", 5)

        self._bars_since_open: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_equity(asset: str) -> bool:
        return "/" not in asset

    def _ensure_tz_aware(self, dt: datetime) -> datetime:
        """Attach ET timezone if *dt* is naive."""
        if dt.tzinfo is None:
            return self._tz.localize(dt)
        return dt

    # ------------------------------------------------------------------
    # Entry / close gates
    # ------------------------------------------------------------------

    def is_entry_allowed(self, asset: str, now: datetime) -> tuple[bool, str]:
        """Return (allowed, reason_code).

        Crypto is always allowed.  Equity is blocked:
        - Outside trading hours (MARKET_CLOSED / NOT_TRADING_DAY)
        - Within the opening blackout window (ENTRY_BLACKOUT_OPEN)
        - Within the EOD soft-close window (EOD_BLACKOUT)
        """
        if not self._is_equity(asset):
            return (True, "")

        now = self._ensure_tz_aware(now)
        date = now.date()

        if not self._calendar.is_trading_day(date):
            return (False, "NOT_TRADING_DAY")

        session_open = self._calendar.get_session_open(date)
        session_close = self._calendar.get_session_close(date)

        if now < session_open:
            return (False, "MARKET_CLOSED")

        if now >= session_close:
            return (False, "MARKET_CLOSED")

        blackout_end = session_open + timedelta(minutes=self._entry_blackout_open_min)
        if now < blackout_end:
            return (False, "ENTRY_BLACKOUT_OPEN")

        soft_close_start = session_close - timedelta(minutes=self._eod_soft_close_min)
        if now >= soft_close_start:
            return (False, "EOD_BLACKOUT")

        return (True, "")

    def is_eod_hard_close(self, asset: str, now: datetime) -> bool:
        """True when equity is within the hard-close window; always False for crypto."""
        if not self._is_equity(asset):
            return False

        now = self._ensure_tz_aware(now)
        date = now.date()

        if not self._calendar.is_trading_day(date):
            return False

        session_close = self._calendar.get_session_close(date)
        hard_close_start = session_close - timedelta(minutes=self._eod_hard_close_min)
        return now >= hard_close_start

    def is_eod_soft_close(self, asset: str, now: datetime) -> bool:
        """True when equity is in the soft-close window but outside the hard-close window."""
        if not self._is_equity(asset):
            return False

        now = self._ensure_tz_aware(now)
        date = now.date()

        if not self._calendar.is_trading_day(date):
            return False

        session_close = self._calendar.get_session_close(date)
        soft_close_start = session_close - timedelta(minutes=self._eod_soft_close_min)
        hard_close_start = session_close - timedelta(minutes=self._eod_hard_close_min)
        return soft_close_start <= now < hard_close_start

    def is_market_open(self, asset: str, now: datetime) -> bool:
        """True when the market for *asset* is currently open."""
        if not self._is_equity(asset):
            return True

        now = self._ensure_tz_aware(now)
        date = now.date()

        if not self._calendar.is_trading_day(date):
            return False

        session_open = self._calendar.get_session_open(date)
        session_close = self._calendar.get_session_close(date)
        return session_open <= now < session_close

    # ------------------------------------------------------------------
    # Bar / session tracking
    # ------------------------------------------------------------------

    def get_bars_since_open(self, asset: str) -> int:
        """Return the number of bars counted since session open for *asset*."""
        return self._bars_since_open.get(asset, 0)

    def increment_bar(self, asset: str) -> None:
        """Increment the bars-since-open counter for *asset*."""
        self._bars_since_open[asset] = self._bars_since_open.get(asset, 0) + 1

    def reset_session(self, asset: str) -> None:
        """Reset the bars-since-open counter to 0 for *asset*."""
        self._bars_since_open[asset] = 0

    def is_new_session(
        self,
        asset: str,
        current_bar_time: datetime,
        last_bar_time: datetime | None,
    ) -> bool:
        """Return True if *current_bar_time* belongs to a different session than *last_bar_time*.

        A session boundary is a calendar-date change for both equity and crypto.
        """
        if last_bar_time is None:
            return True
        return current_bar_time.date() != last_bar_time.date()
