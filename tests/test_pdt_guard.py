"""Tests for PDTGuard.

Uses a temporary file for persistence tests so nothing bleeds into logs/.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, date, timedelta

import pytest

from src.risk.pdt_guard import PDTGuard, _business_days_ago


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_config(tmp_file: str) -> dict:
    return {
        "pdt": {
            "equity_threshold": 25_000.0,
            "max_daytrades_per_5d": 3,
            "rolling_window_days": 5,
            "pdt_counter_file": tmp_file,
        }
    }


def _trade_on(guard: PDTGuard, asset: str, trade_date: date) -> None:
    """Record a same-day round-trip on *trade_date*."""
    entry = datetime(trade_date.year, trade_date.month, trade_date.day, 10, 0)
    exit_ = datetime(trade_date.year, trade_date.month, trade_date.day, 11, 0)
    guard.record_daytrade(asset, entry, exit_)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_counter(tmp_path):
    return str(tmp_path / "pdt_counter.json")


@pytest.fixture()
def guard(tmp_counter):
    return PDTGuard(make_config(tmp_counter))


# ---------------------------------------------------------------------------
# Test 1 — Crypto is always allowed
# ---------------------------------------------------------------------------

def test_crypto_always_can_trade(guard: PDTGuard) -> None:
    """can_trade returns True for a crypto asset regardless of trade count."""
    today = date.today()
    for _ in range(5):
        _trade_on(guard, "BTC/USD", today)
    assert guard.can_trade("BTC/USD", 1_000.0) is True


# ---------------------------------------------------------------------------
# Test 2 — Equity with 0 trades → can_trade
# ---------------------------------------------------------------------------

def test_equity_zero_trades_can_trade(guard: PDTGuard) -> None:
    """can_trade returns True for equity when no trades have been recorded."""
    assert guard.can_trade("SPY", 10_000.0) is True


# ---------------------------------------------------------------------------
# Test 3 — Equity at limit (3 trades) → cannot trade
# ---------------------------------------------------------------------------

def test_equity_at_limit_cannot_trade(guard: PDTGuard) -> None:
    """can_trade returns False once max_daytrades have been used."""
    today = date.today()
    _trade_on(guard, "SPY", today)
    _trade_on(guard, "QQQ", today)
    _trade_on(guard, "SPY", today)
    assert guard.can_trade("SPY", 10_000.0) is False


# ---------------------------------------------------------------------------
# Test 4 — Account above threshold ($30,000) → always can_trade
# ---------------------------------------------------------------------------

def test_equity_above_threshold_always_can_trade(guard: PDTGuard) -> None:
    """can_trade returns True when account equity >= equity_threshold."""
    today = date.today()
    for _ in range(3):
        _trade_on(guard, "SPY", today)
    # Even though 3 trades are logged, equity >= 25 000 waives the restriction
    assert guard.can_trade("SPY", 30_000.0) is True


# ---------------------------------------------------------------------------
# Test 5 — Trades from 6 business days ago are evicted
# ---------------------------------------------------------------------------

def test_stale_trades_evicted(tmp_counter: str) -> None:
    """Trades older than rolling_window_days (5) business days are evicted."""
    guard = PDTGuard(make_config(tmp_counter))

    today = date.today()
    stale_date = _business_days_ago(6, today)   # 6 biz days ago → outside window

    # Manually inject a stale entry (bypass record_daytrade date check)
    guard._trade_log.append({
        "asset": "SPY",
        "entry_time": datetime(stale_date.year, stale_date.month, stale_date.day, 10, 0).isoformat(),
        "exit_time":  datetime(stale_date.year, stale_date.month, stale_date.day, 11, 0).isoformat(),
        "trade_date": stale_date.isoformat(),
    })

    assert guard.get_current_count() == 0   # eviction happens inside get_current_count


# ---------------------------------------------------------------------------
# Test 6 — Persistence: trade survives a new PDTGuard instance
# ---------------------------------------------------------------------------

def test_persistence_survives_reload(tmp_counter: str) -> None:
    """A recorded trade is present after loading a fresh PDTGuard from disk."""
    cfg = make_config(tmp_counter)
    guard1 = PDTGuard(cfg)
    _trade_on(guard1, "SPY", date.today())
    assert guard1.get_current_count() == 1

    # Fresh instance reads from the same file
    guard2 = PDTGuard(cfg)
    assert guard2.get_current_count() == 1
