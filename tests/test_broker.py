"""Tests for OrderTracker fill events."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from src.broker.order_tracking import OrderTracker
from src.models import (
    AssetClass,
    Direction,
    ExitReason,
    RegimeLabel,
    Signal,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def make_config(tmp_path) -> dict:
    return {
        "monitoring": {"log_dir": str(tmp_path)},
    }


def make_signal(asset: str = "SPY", direction: Direction = Direction.LONG) -> Signal:
    return Signal(
        asset=asset,
        direction=direction,
        size_pct=0.10,
        entry_price=500.0,
        stop_price=490.0,
        take_profit_price=520.0,
        max_hold_bars=12,
        strategy_name="HMM_TRENDING_UP+LGBM",
        regime=RegimeLabel.TRENDING_UP,
        hmm_confidence=0.80,
        lgbm_confidence=0.80,
        timestamp=datetime(2024, 1, 2, 10, 0),
        asset_class=AssetClass.EQUITY,
    )


# ---------------------------------------------------------------------------
# Test: entry fill → position created; stop fill → trade completed
# ---------------------------------------------------------------------------

def test_entry_then_stop_fill(tmp_path) -> None:
    """Full round-trip: entry fill creates Position; exit fill creates CompletedTrade
    and removes the Position from open positions."""

    tracker = OrderTracker(make_config(tmp_path))
    signal = make_signal()

    # ---- Entry fill ----
    position = tracker.on_entry_filled(
        order_id="entry-001",
        fill_price=501.0,      # small slippage from signal.entry_price
        fill_qty=20.0,
        signal=signal,
    )

    assert position.asset == "SPY"
    assert position.direction is Direction.LONG
    assert position.entry_price == pytest.approx(501.0)
    assert position.shares == pytest.approx(20.0)

    # Position is now tracked
    open_positions = tracker.get_open_positions()
    assert "SPY" in open_positions

    # Register the corresponding stop order so we can verify it's cleaned up
    tracker.register_stop_order("SPY", "stop-001")
    assert tracker._stop_orders.get("SPY") == "stop-001"

    # ---- Stop (exit) fill ----
    trade = tracker.on_exit_filled(
        order_id="stop-001",
        fill_price=490.0,      # hit the stop
        fill_qty=20.0,
        exit_reason=ExitReason.STOP_LOSS,
    )

    # CompletedTrade should reflect the loss
    assert trade.asset == "SPY"
    assert trade.direction is Direction.LONG
    assert trade.exit_reason is ExitReason.STOP_LOSS
    assert trade.pnl_dollar == pytest.approx((490.0 - 501.0) * 20.0)
    assert trade.pnl_pct == pytest.approx((490.0 - 501.0) / 501.0)

    # Position removed from open state
    assert "SPY" not in tracker.get_open_positions()

    # Stop order cleaned out of _stop_orders
    assert "SPY" not in tracker._stop_orders

    # Trade appears in history
    history = tracker.get_trade_history()
    assert len(history) == 1
    assert history[0] is trade
