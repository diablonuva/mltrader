from __future__ import annotations

from src.models import BarData


class OpeningRange:
    """Tracks the high/low of the first N bars of an equity session.

    Not meaningful for crypto — returns 0.5 (neutral) as a placeholder
    until the range is complete.
    """

    def __init__(self, n_bars: int = 6) -> None:
        self._n_bars = n_bars
        self._or_high: float | None = None
        self._or_low: float | None = None
        self._is_complete: bool = False

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def update(self, bar: BarData, bars_since_open: int) -> None:
        if self._is_complete:
            return

        if bars_since_open < self._n_bars:
            if self._or_high is None or bar.high > self._or_high:
                self._or_high = bar.high
            if self._or_low is None or bar.low < self._or_low:
                self._or_low = bar.low

        if bars_since_open == self._n_bars:
            self._is_complete = True

    def get_or_position(self, price: float) -> float:
        """Normalised position within the opening range.

        Returns 0.5 if range not yet complete.
        >1.0 means price is above the range; <0.0 means below.
        """
        if not self._is_complete or self._or_high is None or self._or_low is None:
            return 0.5
        return (price - self._or_low) / (self._or_high - self._or_low + 1e-8)

    def get_or_high(self) -> float | None:
        return self._or_high

    def get_or_low(self) -> float | None:
        return self._or_low

    def get_or_midpoint(self) -> float | None:
        if self._or_high is None or self._or_low is None:
            return None
        return (self._or_high + self._or_low) / 2.0

    def get_or_width(self) -> float | None:
        if self._or_high is None or self._or_low is None:
            return None
        return self._or_high - self._or_low

    def is_complete(self) -> bool:
        return self._is_complete

    def reset(self) -> None:
        self._or_high = None
        self._or_low = None
        self._is_complete = False
