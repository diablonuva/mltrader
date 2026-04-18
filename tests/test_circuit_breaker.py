"""Tests for CircuitBreaker."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from src.risk.circuit_breaker import CircuitBreaker
from src.models import PortfolioState, Direction


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_config() -> dict:
    return {
        "risk": {
            "half_hour_dd_limit": 0.010,
            "daily_dd_limit": 0.030,
            "consecutive_loss_pause": 3,
            "pause_duration_minutes": 120,
            # unused by CircuitBreaker but present for completeness
            "max_portfolio_leverage": 1.25,
            "max_single_position_pct": 0.10,
            "max_total_exposure_pct": 1.25,
            "max_simultaneous_positions": 2,
            "max_trades_per_day_crypto": 6,
            "max_trades_per_day_equity": 3,
            "correlation_block_threshold": 0.7,
        }
    }


def make_portfolio(equity: float, session_open_equity: float) -> PortfolioState:
    now = datetime(2024, 1, 2, 10, 0)
    return PortfolioState(
        equity=equity,
        cash=equity,
        buying_power=equity,
        positions={},
        daily_pnl=session_open_equity - equity,
        session_open_equity=session_open_equity,
        rolling_30m_equity_marks=[],
        consecutive_loss_count=0,
        circuit_breaker_active=False,
        circuit_breaker_resume_time=None,
        last_updated=now,
    )


@pytest.fixture()
def cb() -> CircuitBreaker:
    return CircuitBreaker(make_config())


T0 = datetime(2024, 1, 2, 10, 0)


# ---------------------------------------------------------------------------
# Test 1 — 30-min equity drop >1% triggers is_active
# ---------------------------------------------------------------------------

def test_30min_drawdown_triggers_pause(cb: CircuitBreaker) -> None:
    """A >1% equity drop within 30 min activates the circuit breaker."""
    # Seed the 30-min mark at 100 000
    state_open = make_portfolio(equity=100_000.0, session_open_equity=100_000.0)
    cb.update(state_open, T0)

    # 20 min later equity has dropped 1.1% — still within the 30-min window
    state_drop = make_portfolio(equity=98_900.0, session_open_equity=100_000.0)
    active, reason = cb.update(state_drop, T0 + timedelta(minutes=20))

    assert active is True
    assert reason == "HALF_HOUR_DD"


# ---------------------------------------------------------------------------
# Test 2 — Daily drawdown >3% → _daily_loss_stopped
# ---------------------------------------------------------------------------

def test_daily_drawdown_triggers_daily_stop(cb: CircuitBreaker) -> None:
    """A >3% daily drop sets _daily_loss_stopped and returns DAILY_LOSS_STOP."""
    # Session opened at 100 000; equity is now 96 900 (3.1% down)
    state = make_portfolio(equity=96_900.0, session_open_equity=100_000.0)
    active, reason = cb.update(state, T0)

    assert active is True
    assert reason == "DAILY_LOSS_STOP"
    assert cb._daily_loss_stopped is True

    # Subsequent update still reports daily stop
    active2, reason2 = cb.update(state, T0 + timedelta(minutes=1))
    assert active2 is True
    assert reason2 == "DAILY_LOSS_STOP"


# ---------------------------------------------------------------------------
# Test 3 — 3 consecutive losses → 120-min pause
# ---------------------------------------------------------------------------

def test_consecutive_losses_trigger_pause(cb: CircuitBreaker) -> None:
    """Three consecutive losses activate a 120-min pause."""
    cb.record_trade_result(was_win=False)
    cb.record_trade_result(was_win=False)
    assert cb.is_active() is False   # only 2 losses so far

    cb.record_trade_result(was_win=False)  # 3rd loss
    assert cb.is_active() is True
    assert cb.get_reason() == "CONSECUTIVE_LOSSES"


# ---------------------------------------------------------------------------
# Test 4 — Clock advances 31 min past resume_time → pause expires
# ---------------------------------------------------------------------------

def test_timed_pause_expires_after_resume_time(cb: CircuitBreaker) -> None:
    """A timed pause clears automatically when the clock passes resume_time."""
    # Trigger the 30-min DD pause
    state_open = make_portfolio(equity=100_000.0, session_open_equity=100_000.0)
    cb.update(state_open, T0)

    state_drop = make_portfolio(equity=98_900.0, session_open_equity=100_000.0)
    cb.update(state_drop, T0 + timedelta(minutes=20))
    assert cb.is_active() is True

    # Advance clock by 121 minutes (past the 120-min pause_duration)
    state_recover = make_portfolio(equity=98_900.0, session_open_equity=100_000.0)
    active, _ = cb.update(state_recover, T0 + timedelta(minutes=141))

    assert active is False


# ---------------------------------------------------------------------------
# Test 5 — Daily stop + reset_session → is_active=False
# ---------------------------------------------------------------------------

def test_reset_session_clears_daily_stop(cb: CircuitBreaker) -> None:
    """reset_session clears the daily stop flag and deactivates the breaker."""
    state = make_portfolio(equity=96_900.0, session_open_equity=100_000.0)
    cb.update(state, T0)
    assert cb.is_active() is True

    cb.reset_session()

    assert cb.is_active() is False
    assert cb._daily_loss_stopped is False
