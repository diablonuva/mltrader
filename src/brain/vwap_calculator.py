from __future__ import annotations

from datetime import datetime, time

import pytz

from src.models import AssetClass, BarData


class VWAPCalculator:
    """Session-resetting Volume-Weighted Average Price calculator.

    Equity resets at 09:30 ET each trading day.
    Crypto resets at midnight UTC each calendar day.
    """

    _EQUITY_SESSION_OPEN = time(9, 30)

    def __init__(
        self,
        asset_class: AssetClass,
        timezone: str = "America/New_York",
    ) -> None:
        self._asset_class = asset_class
        self._tz = pytz.timezone(timezone)
        self._cumulative_pv: float = 0.0
        self._cumulative_volume: float = 0.0
        self._current_vwap: float = 0.0
        self._last_bar_time: datetime | None = None
        self._session_bar_count: int = 0

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def update(self, bar: BarData) -> float:
        if self.is_new_session(bar.timestamp, self._last_bar_time):
            self.reset()

        self._cumulative_pv += bar.close * bar.volume
        self._cumulative_volume += bar.volume
        self._current_vwap = (
            self._cumulative_pv / self._cumulative_volume
            if self._cumulative_volume > 0
            else 0.0
        )
        self._last_bar_time = bar.timestamp
        self._session_bar_count += 1
        return self._current_vwap

    def get_vwap(self) -> float:
        return self._current_vwap

    def get_deviation_pct(self, price: float) -> float:
        if self._current_vwap == 0.0:
            return 0.0
        return (price - self._current_vwap) / self._current_vwap * 100.0

    def reset(self) -> None:
        self._cumulative_pv = 0.0
        self._cumulative_volume = 0.0
        self._current_vwap = 0.0
        self._session_bar_count = 0

    def is_new_session(
        self,
        current_bar_time: datetime,
        last_bar_time: datetime | None,
    ) -> bool:
        if last_bar_time is None:
            return True

        if self._asset_class is AssetClass.CRYPTO:
            # Crypto: midnight UTC boundary
            current_utc = current_bar_time.astimezone(pytz.utc).date()
            last_utc = last_bar_time.astimezone(pytz.utc).date()
            return current_utc != last_utc

        # Equity: new calendar date AND current local time >= 09:30
        current_local = current_bar_time.astimezone(self._tz)
        last_local = last_bar_time.astimezone(self._tz)
        new_day = current_local.date() != last_local.date()
        after_open = current_local.time() >= self._EQUITY_SESSION_OPEN
        return new_day and after_open
