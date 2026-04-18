"""Tests for RiskManager."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytz
import pytest

from src.models import (
    AssetClass,
    Direction,
    PortfolioState,
    Position,
    RegimeLabel,
    Signal,
)
from src.risk.circuit_breaker import CircuitBreaker
from src.risk.pdt_guard import PDTGuard
from src.risk.risk_manager import RiskManager
from src.session.session_manager import SessionManager


# ---------------------------------------------------------------------------
# Shared config
# ---------------------------------------------------------------------------

ET = pytz.timezone("America/New_York")


def make_config() -> dict:
    return {
        "pdt": {
            "equity_threshold": 25_000.0,
            "max_daytrades_per_5d": 3,
            "rolling_window_days": 5,
            "pdt_counter_file": "logs/pdt_test.json",
        },
        "risk": {
            "max_portfolio_leverage": 1.25,
            "max_single_position_pct": 0.10,
            "max_total_exposure_pct": 1.25,
            "max_simultaneous_positions": 2,
            "half_hour_dd_limit": 0.010,
            "daily_dd_limit": 0.030,
            "consecutive_loss_pause": 3,
            "pause_duration_minutes": 120,
            "max_trades_per_day_crypto": 6,
            "max_trades_per_day_equity": 3,
            "correlation_block_threshold": 0.7,
        },
        "session": {
            "equity_session_start": "09:30",
            "equity_session_end": "16:00",
            "entry_blackout_open_minutes": 5,
            "eod_soft_close_minutes": 10,
            "eod_hard_close_minutes": 5,
            "timezone": "America/New_York",
        },
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def cfg() -> dict:
    return make_config()


def make_portfolio(
    equity: float = 100_000.0,
    session_open_equity: float = 100_000.0,
    positions: dict | None = None,
) -> PortfolioState:
    return PortfolioState(
        equity=equity,
        cash=equity,
        buying_power=equity,
        positions=positions or {},
        daily_pnl=0.0,
        session_open_equity=session_open_equity,
        rolling_30m_equity_marks=[],
        consecutive_loss_count=0,
        circuit_breaker_active=False,
        circuit_breaker_resume_time=None,
        last_updated=datetime(2024, 1, 2, 10, 0),
    )


def make_signal(
    asset: str = "SPY",
    size_pct: float = 0.10,
    direction: Direction = Direction.LONG,
    asset_class: AssetClass = AssetClass.EQUITY,
    ts: datetime | None = None,
) -> Signal:
    return Signal(
        asset=asset,
        direction=direction,
        size_pct=size_pct,
        entry_price=500.0,
        stop_price=495.0,
        take_profit_price=515.0,
        max_hold_bars=12,
        strategy_name="TEST",
        regime=RegimeLabel.TRENDING_UP,
        hmm_confidence=0.80,
        lgbm_confidence=0.80,
        timestamp=ts or datetime(2024, 1, 2, 10, 0),
        asset_class=asset_class,
    )


def make_position(asset: str, shares: float = 10.0, price: float = 500.0) -> Position:
    return Position(
        asset=asset,
        direction=Direction.LONG,
        entry_price=price,
        current_price=price,
        shares=shares,
        entry_time=datetime(2024, 1, 2, 9, 35),
        stop_price=495.0,
        take_profit_price=515.0,
        max_hold_bars=12,
        bars_held=1,
        stop_order_id="",
        strategy_name="TEST",
        regime_at_entry=RegimeLabel.TRENDING_UP,
    )


def make_rm(cfg: dict, cb: CircuitBreaker | None = None,
            pdt: PDTGuard | None = None) -> RiskManager:
    if cb is None:
        cb = CircuitBreaker(cfg)
    if pdt is None:
        pdt = PDTGuard({"pdt": cfg["pdt"]})
    sm = SessionManager(cfg)
    return RiskManager(cfg, pdt, cb, sm)


# ---------------------------------------------------------------------------
# Test 1 — Active circuit breaker → rejected
# ---------------------------------------------------------------------------

def test_active_circuit_breaker_rejects(cfg: dict) -> None:
    """evaluate() returns a rejection when the circuit breaker is active."""
    cb = CircuitBreaker(cfg)
    # Force three consecutive losses to trip the breaker
    cb.record_trade_result(was_win=False)
    cb.record_trade_result(was_win=False)
    cb.record_trade_result(was_win=False)
    assert cb.is_active()

    rm = make_rm(cfg, cb=cb)
    decision = rm.evaluate(make_signal(), make_portfolio(), datetime(2024, 1, 2, 10, 0))

    assert decision.rejected
    assert "CIRCUIT_BREAKER" in decision.reason_code


# ---------------------------------------------------------------------------
# Test 2 — PDT limit → rejected "PDT_LIMIT"
# ---------------------------------------------------------------------------

def test_pdt_limit_rejects(cfg: dict, tmp_path) -> None:
    """evaluate() rejects when the PDT guard denies the trade."""
    from datetime import date as date_cls
    cfg["pdt"]["pdt_counter_file"] = str(tmp_path / "pdt.json")
    pdt = PDTGuard({"pdt": cfg["pdt"]})

    # Use today so the trades fall inside the rolling 5-business-day window
    today = date_cls.today()
    t_entry = datetime(today.year, today.month, today.day, 10, 0)
    t_exit = datetime(today.year, today.month, today.day, 10, 30)
    for _ in range(3):
        pdt.record_daytrade("SPY", t_entry, t_exit)

    rm = make_rm(cfg, pdt=pdt)
    decision = rm.evaluate(
        make_signal("SPY"),
        # equity matches session_open so the daily-DD circuit-breaker doesn't fire
        make_portfolio(equity=10_000.0, session_open_equity=10_000.0),
        datetime(today.year, today.month, today.day, 10, 0),
    )

    assert decision.rejected
    assert decision.reason_code == "PDT_LIMIT"


# ---------------------------------------------------------------------------
# Test 3 — Oversized signal → modified with smaller size_pct
# ---------------------------------------------------------------------------

def test_oversized_signal_is_reduced(cfg: dict, tmp_path) -> None:
    """evaluate() returns modified=True and a smaller size_pct when the
    proposed position would exceed max_total_exposure_pct."""
    cfg["pdt"]["pdt_counter_file"] = str(tmp_path / "pdt.json")
    # Lower the cap so a valid signal (0.80) still triggers a reduction
    cfg["risk"]["max_total_exposure_pct"] = 0.50

    sig = make_signal(size_pct=0.80)  # 80% > 50% cap → must be reduced
    rm = make_rm(cfg)
    decision = rm.evaluate(sig, make_portfolio(), datetime(2024, 1, 2, 10, 0))

    assert decision.approved
    assert decision.modified
    assert decision.modifications["size_pct"] < 0.80


# ---------------------------------------------------------------------------
# Test 4 — 2 positions open → 3rd rejected "MAX_POSITIONS_OPEN"
# ---------------------------------------------------------------------------

def test_max_positions_rejects_third(cfg: dict, tmp_path) -> None:
    """evaluate() rejects a new signal when max_simultaneous_positions (2) is reached."""
    cfg["pdt"]["pdt_counter_file"] = str(tmp_path / "pdt.json")

    positions = {
        "SPY": make_position("SPY"),
        "QQQ": make_position("QQQ"),
    }
    portfolio = make_portfolio(positions=positions)

    rm = make_rm(cfg)
    decision = rm.evaluate(
        make_signal("NVDA"),
        portfolio,
        datetime(2024, 1, 2, 10, 0),
    )

    assert decision.rejected
    assert decision.reason_code == "MAX_POSITIONS_OPEN"


# ---------------------------------------------------------------------------
# Test 5 — Equity signal at 15:56 ET → rejected "EOD_HARD_CLOSE"
# ---------------------------------------------------------------------------

def test_eod_hard_close_rejects(cfg: dict, tmp_path) -> None:
    """evaluate() rejects an equity entry during the EOD hard-close window."""
    cfg["pdt"]["pdt_counter_file"] = str(tmp_path / "pdt.json")

    now = ET.localize(datetime(2024, 1, 2, 15, 56))
    sig = make_signal("SPY", ts=now)

    rm = make_rm(cfg)
    decision = rm.evaluate(sig, make_portfolio(), now)

    assert decision.rejected
    assert decision.reason_code == "EOD_HARD_CLOSE"


# ---------------------------------------------------------------------------
# Test 6 — Valid signal → approved
# ---------------------------------------------------------------------------

def test_valid_signal_approved(cfg: dict, tmp_path) -> None:
    """evaluate() returns approved=True for a clean signal with no issues."""
    cfg["pdt"]["pdt_counter_file"] = str(tmp_path / "pdt.json")

    now = ET.localize(datetime(2024, 1, 2, 10, 30))
    sig = make_signal("SPY", size_pct=0.08, ts=now)

    rm = make_rm(cfg)
    decision = rm.evaluate(sig, make_portfolio(), now)

    assert decision.approved
    assert not decision.rejected
    assert not decision.modified
