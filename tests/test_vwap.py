from __future__ import annotations

from datetime import datetime, timezone

import pytz
import pytest

from src.brain.opening_range import OpeningRange
from src.brain.vwap_calculator import VWAPCalculator
from src.models import AssetClass, BarData


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ET = pytz.timezone("America/New_York")


def make_bar(
    symbol: str,
    dt: datetime,
    close: float,
    volume: float,
    high: float | None = None,
    low: float | None = None,
) -> BarData:
    return BarData(
        symbol=symbol,
        timestamp=dt,
        open=close,
        high=high if high is not None else close,
        low=low if low is not None else close,
        close=close,
        volume=volume,
        bar_size="5Min",
    )


# ---------------------------------------------------------------------------
# VWAP tests
# ---------------------------------------------------------------------------

def test_vwap_resets_on_new_equity_session():
    """VWAP resets when a new calendar day starts at or after 09:30 ET."""
    calc = VWAPCalculator(AssetClass.EQUITY)

    day1_bar = make_bar("SPY", ET.localize(datetime(2024, 1, 2, 10, 0)), close=100.0, volume=1000.0)
    vwap_day1 = calc.update(day1_bar)
    assert vwap_day1 == pytest.approx(100.0)

    # Second bar, same session — VWAP stays above 100
    day1_bar2 = make_bar("SPY", ET.localize(datetime(2024, 1, 2, 10, 5)), close=110.0, volume=1000.0)
    calc.update(day1_bar2)
    assert calc.get_vwap() == pytest.approx(105.0)

    # New day at 09:30 — should reset; only the new bar contributes
    day2_bar = make_bar("SPY", ET.localize(datetime(2024, 1, 3, 9, 30)), close=200.0, volume=500.0)
    vwap_day2 = calc.update(day2_bar)
    assert vwap_day2 == pytest.approx(200.0)


def test_vwap_accumulates_correctly():
    """VWAP is the volume-weighted mean of closes within a session."""
    calc = VWAPCalculator(AssetClass.EQUITY)

    bars = [
        make_bar("SPY", ET.localize(datetime(2024, 1, 2, 9, 30)), close=100.0, volume=200.0),
        make_bar("SPY", ET.localize(datetime(2024, 1, 2, 9, 35)), close=110.0, volume=100.0),
        make_bar("SPY", ET.localize(datetime(2024, 1, 2, 9, 40)), close=90.0, volume=100.0),
    ]
    for bar in bars:
        calc.update(bar)

    # Expected: (100*200 + 110*100 + 90*100) / (200+100+100) = 40000/400 = 100.0
    assert calc.get_vwap() == pytest.approx(100.0)


def test_vwap_deviation_zero_when_price_equals_vwap():
    """get_deviation_pct returns 0.0 when price matches the current VWAP."""
    calc = VWAPCalculator(AssetClass.EQUITY)
    bar = make_bar("SPY", ET.localize(datetime(2024, 1, 2, 9, 30)), close=150.0, volume=1000.0)
    calc.update(bar)
    assert calc.get_deviation_pct(calc.get_vwap()) == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# OpeningRange tests
# ---------------------------------------------------------------------------

def test_or_position_returns_half_when_incomplete():
    """get_or_position returns 0.5 before the range is complete."""
    or_tracker = OpeningRange(n_bars=6)
    bar = make_bar("SPY", ET.localize(datetime(2024, 1, 2, 9, 30)), close=100.0, volume=1000.0, high=102.0, low=98.0)
    or_tracker.update(bar, bars_since_open=0)
    assert not or_tracker.is_complete()
    assert or_tracker.get_or_position(105.0) == pytest.approx(0.5)


def test_or_position_above_range():
    """get_or_position > 1.0 when price is above or_high."""
    or_tracker = OpeningRange(n_bars=2)
    bars = [
        make_bar("SPY", ET.localize(datetime(2024, 1, 2, 9, 30)), close=100.0, volume=500.0, high=105.0, low=95.0),
        make_bar("SPY", ET.localize(datetime(2024, 1, 2, 9, 35)), close=102.0, volume=500.0, high=103.0, low=99.0),
    ]
    for i, bar in enumerate(bars):
        or_tracker.update(bar, bars_since_open=i)
    or_tracker.update(bars[-1], bars_since_open=2)  # trigger completion

    assert or_tracker.is_complete()
    assert or_tracker.get_or_position(200.0) > 1.0


def test_or_position_below_range():
    """get_or_position < 0.0 when price is below or_low."""
    or_tracker = OpeningRange(n_bars=2)
    bars = [
        make_bar("SPY", ET.localize(datetime(2024, 1, 2, 9, 30)), close=100.0, volume=500.0, high=105.0, low=95.0),
        make_bar("SPY", ET.localize(datetime(2024, 1, 2, 9, 35)), close=102.0, volume=500.0, high=103.0, low=99.0),
    ]
    for i, bar in enumerate(bars):
        or_tracker.update(bar, bars_since_open=i)
    or_tracker.update(bars[-1], bars_since_open=2)  # trigger completion

    assert or_tracker.is_complete()
    assert or_tracker.get_or_position(1.0) < 0.0
