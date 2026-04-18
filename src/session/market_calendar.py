from __future__ import annotations

import datetime

import exchange_calendars as xcals
import pandas as pd
import pytz


class MarketCalendar:
    """NYSE calendar wrapper providing session open/close and trading-day checks."""

    def __init__(self, timezone: str = "America/New_York") -> None:
        self.tz = pytz.timezone(timezone)
        self._cal = xcals.get_calendar("NYSE")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_trading_day(self, date: datetime.date) -> bool:
        """Return True if *date* is a NYSE trading session."""
        ts = pd.Timestamp(str(date))
        return bool(self._cal.is_session(ts))

    def get_session_open(self, date: datetime.date) -> datetime.datetime:
        """Return timezone-aware session-open datetime for *date* (ET)."""
        ts = pd.Timestamp(str(date))
        open_utc: pd.Timestamp = self._cal.session_open(ts)
        return open_utc.to_pydatetime().astimezone(self.tz)

    def get_session_close(self, date: datetime.date) -> datetime.datetime:
        """Return timezone-aware session-close datetime for *date* (ET)."""
        ts = pd.Timestamp(str(date))
        close_utc: pd.Timestamp = self._cal.session_close(ts)
        return close_utc.to_pydatetime().astimezone(self.tz)

    def is_early_close(self, date: datetime.date) -> bool:
        """Return True if *date* is a trading day with an early (< 16:00 ET) close."""
        if not self.is_trading_day(date):
            return False
        close = self.get_session_close(date)
        return not (close.hour == 16 and close.minute == 0)

    def next_trading_day(self, date: datetime.date) -> datetime.date:
        """Return the next NYSE trading day strictly after *date*."""
        ts = pd.Timestamp(str(date))
        # _cal.sessions is a DatetimeIndex of all valid sessions; find the
        # first one that is strictly later than ts.
        future = self._cal.sessions[self._cal.sessions > ts]
        if len(future) == 0:
            raise ValueError(f"No trading day found after {date}")
        return future[0].date()
