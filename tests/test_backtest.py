"""Smoke test for WalkForwardBacktester.

Generates 60 days × 78 bars of synthetic random BarData for SPY, then runs
one walk-forward window with a fast config override.  The test just asserts
that the engine completes without error and produces an equity curve.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta

import pytest

from src.backtest.backtester import WalkForwardBacktester
from src.backtest.stress_test import StressTester
from src.config_loader import load_config
from src.models import BarData


# ---------------------------------------------------------------------------
# Synthetic bar generator
# ---------------------------------------------------------------------------

def make_spy_bars(n_days: int = 60, seed: int = 42) -> list[BarData]:
    """Generate *n_days* × 78 synthetic 5-min bars for SPY.

    Bars start at 2024-01-02 09:30 ET and are spaced 5 minutes apart so each
    trading day has exactly 78 bars.  Timestamps are UTC-naive for simplicity.
    """
    rng = random.Random(seed)
    price = 475.0
    bars: list[BarData] = []

    # 2024-01-02 09:30 ET = 14:30 UTC
    base_ts = datetime(2024, 1, 2, 14, 30)

    for day in range(n_days):
        day_open = base_ts + timedelta(days=day)
        price = 475.0 + rng.gauss(0, 5)  # fresh open each day

        for bar_idx in range(78):
            price *= 1.0 + rng.gauss(0, 0.0015)
            price = max(price, 1.0)
            high = price * (1 + abs(rng.gauss(0, 0.0008)))
            low = price * (1 - abs(rng.gauss(0, 0.0008)))
            low = min(low, price)
            high = max(high, price)
            ts = day_open + timedelta(minutes=5 * bar_idx)
            bars.append(
                BarData(
                    symbol="SPY",
                    timestamp=ts,
                    open=round(price * (1 + rng.gauss(0, 0.0003)), 4),
                    high=round(high, 4),
                    low=round(low, 4),
                    close=round(price, 4),
                    volume=float(rng.randint(50_000, 500_000)),
                    bar_size="5Min",
                )
            )

    return bars


# ---------------------------------------------------------------------------
# Fast config override
# ---------------------------------------------------------------------------

def fast_config() -> dict:
    """Return a config with HMM/backtest settings reduced for fast testing."""
    cfg = load_config()

    # HMM: single small model, two seeds
    cfg["hmm"]["n_components_candidates"] = [3]
    cfg["hmm"]["n_init"] = 2
    cfg["hmm"]["min_train_bars"] = 50   # well below our ~240 feature rows

    # LGBM: small trees
    cfg["lgbm"]["n_estimators"] = 10
    cfg["lgbm"]["min_child_samples"] = 5
    cfg["lgbm"]["min_samples_per_regime"] = 10

    # Walk-forward: one window only (step > total bars)
    cfg["backtest"]["train_bars_equity"] = 300   # ~240 feature rows after warmup
    cfg["backtest"]["test_bars_equity"] = 78     # one session of bars
    cfg["backtest"]["step_bars_equity"] = 99999  # ensure only one window

    return cfg


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

def test_smoke_run(tmp_path) -> None:
    """Backtester runs end-to-end on synthetic data without raising."""
    cfg = fast_config()
    cfg["pdt"]["pdt_counter_file"] = str(tmp_path / "pdt_bt.json")
    cfg["monitoring"]["log_dir"] = str(tmp_path)

    bars = make_spy_bars(n_days=60)
    bar_data = {"SPY": bars}

    start = bars[0].timestamp
    end = bars[-1].timestamp

    backtester = WalkForwardBacktester(cfg)
    results = backtester.run(bar_data, start, end)

    assert results is not None, "run() returned None"
    assert len(results.equity_curve) > 0, "equity_curve is empty"
    assert len(results.per_window_results) >= 1, "no windows completed"


# ---------------------------------------------------------------------------
# Stress test: PDT guard never allows a 4th trade in any 5-business-day window
# ---------------------------------------------------------------------------

def test_pdt_stress(tmp_path) -> None:
    """StressTester.pdt_stress verifies PDTGuard never allows a 4th equity
    day trade in any rolling 5-business-day window when equity < $25k."""
    cfg = fast_config()
    cfg["pdt"]["pdt_counter_file"] = str(tmp_path / "pdt_stress.json")
    cfg["monitoring"]["log_dir"] = str(tmp_path)

    bars = make_spy_bars(n_days=60)
    bar_data = {"SPY": bars}

    backtester = WalkForwardBacktester(cfg)
    tester = StressTester(backtester, cfg)

    outcome = tester.pdt_stress(bar_data, low_equity=15_000.0)

    assert outcome["pdt_guard_passed"], (
        f"PDT violation detected: max trades in window = "
        f"{outcome['max_trades_in_any_5d_window']}"
    )
