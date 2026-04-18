"""Tests for SessionManager and MarketCalendar.

All equity tests use 2024-01-02 (Tuesday — first trading day of 2024,
since NYSE was closed 2024-01-01 for New Year's Day).
NYSE hours that day: 09:30–16:00 ET.

Config mirrors config/settings.yaml session block:
  entry_blackout_open_minutes : 5
  eod_soft_close_minutes      : 10
  eod_hard_close_minutes      : 5
"""
from __future__ import annotations

from datetime import datetime

import pytz
import pytest

from src.session.session_manager import SessionManager

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

ET = pytz.timezone("America/New_York")

TRADING_DAY = datetime(2024, 1, 2).date()   # confirmed NYSE session


@pytest.fixture()
def mgr() -> SessionManager:
    config = {
        "session": {
            "equity_session_start": "09:30",
            "equity_session_end": "16:00",
            "entry_blackout_open_minutes": 5,
            "eod_soft_close_minutes": 10,
            "eod_hard_close_minutes": 5,
            "timezone": "America/New_York",
        }
    }
    return SessionManager(config)


def et(hour: int, minute: int, date: datetime = datetime(2024, 1, 2)) -> datetime:
    """Return a timezone-aware ET datetime on the given date."""
    return ET.localize(datetime(date.year, date.month, date.day, hour, minute))


# ---------------------------------------------------------------------------
# Test 1 — equity before open (09:00 ET)
# ---------------------------------------------------------------------------

def test_equity_before_open_returns_market_closed(mgr: SessionManager) -> None:
    """is_entry_allowed is False for equity at 09:00 ET (before session open)."""
    allowed, reason = mgr.is_entry_allowed("SPY", et(9, 0))
    assert not allowed
    assert reason == "MARKET_CLOSED"


# ---------------------------------------------------------------------------
# Test 2 — equity within opening blackout (09:31 ET, 5-min window)
# ---------------------------------------------------------------------------

def test_equity_opening_blackout_returns_blackout(mgr: SessionManager) -> None:
    """is_entry_allowed is False for equity at 09:31 ET (within 5-min opening blackout)."""
    allowed, reason = mgr.is_entry_allowed("SPY", et(9, 31))
    assert not allowed
    assert reason == "ENTRY_BLACKOUT_OPEN"


# ---------------------------------------------------------------------------
# Test 3 — equity mid-session (10:00 ET)
# ---------------------------------------------------------------------------

def test_equity_mid_session_allowed(mgr: SessionManager) -> None:
    """is_entry_allowed is True for equity at 10:00 ET on a trading day."""
    allowed, reason = mgr.is_entry_allowed("SPY", et(10, 0))
    assert allowed
    assert reason == ""


# ---------------------------------------------------------------------------
# Test 4 — equity within EOD soft-close (15:52 ET, 10-min window → starts 15:50)
# ---------------------------------------------------------------------------

def test_equity_eod_soft_close_returns_eod_blackout(mgr: SessionManager) -> None:
    """is_entry_allowed is False for equity at 15:52 ET (within eod_soft_close window)."""
    allowed, reason = mgr.is_entry_allowed("SPY", et(15, 52))
    assert not allowed
    assert reason == "EOD_BLACKOUT"


# ---------------------------------------------------------------------------
# Test 5 — crypto always allowed
# ---------------------------------------------------------------------------

def test_crypto_always_allowed(mgr: SessionManager) -> None:
    """is_entry_allowed always returns (True, '') for crypto assets."""
    times = [et(0, 0), et(9, 0), et(9, 31), et(15, 52), et(23, 59)]
    for now in times:
        allowed, reason = mgr.is_entry_allowed("BTC/USD", now)
        assert allowed, f"Expected allowed for crypto at {now}"
        assert reason == ""


# ---------------------------------------------------------------------------
# Test 6 — equity EOD hard-close (15:56 ET, 5-min window → starts 15:55)
# ---------------------------------------------------------------------------

def test_equity_eod_hard_close_true_at_15_56(mgr: SessionManager) -> None:
    """is_eod_hard_close returns True for equity at 15:56 ET."""
    assert mgr.is_eod_hard_close("SPY", et(15, 56)) is True


# ---------------------------------------------------------------------------
# Test 7 — crypto EOD hard-close always False
# ---------------------------------------------------------------------------

def test_crypto_eod_hard_close_always_false(mgr: SessionManager) -> None:
    """is_eod_hard_close returns False for crypto regardless of time."""
    times = [et(0, 0), et(15, 56), et(23, 59)]
    for now in times:
        assert mgr.is_eod_hard_close("BTC/USD", now) is False, (
            f"Expected False for crypto at {now}"
        )
