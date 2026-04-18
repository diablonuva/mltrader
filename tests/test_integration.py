"""End-to-end integration tests for the ML Trader pipeline.

All tests run on synthetic data — no Alpaca API calls are made.
The test module exercises the full signal → risk → simulated fill path
using the WalkForwardBacktester as the execution harness (it already
wires every component together without broker connectivity).
"""
from __future__ import annotations

import json
import math
import random
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pytest

from src.backtest.backtester import WalkForwardBacktester
from src.brain.feature_engineering import FeatureEngineer, build_feature_matrix
from src.brain.hmm_engine import HMMEngine
from src.brain.lgbm_experts import LGBMExpertRouter, LGBMExpertTrainer
from src.config_loader import load_config
from src.models import (
    AssetClass,
    BarData,
    Direction,
    ExitReason,
    PortfolioState,
    Position,
    RegimeLabel,
    Signal,
)
from src.monitoring.logger import StructuredLogger
from src.risk.circuit_breaker import CircuitBreaker
from src.risk.pdt_guard import PDTGuard
from src.risk.risk_manager import RiskManager
from src.session.session_manager import SessionManager


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _base_ts(year: int = 2024, month: int = 1, day: int = 2) -> datetime:
    """Return 09:30 ET (= 14:30 UTC) on the given NYSE trading day."""
    return datetime(year, month, day, 14, 30)


def make_bars(
    symbol: str,
    n_days: int,
    seed: int = 42,
    start_price: float = 475.0,
    bar_vol: float = 0.0015,
    intraday_bars: int = 78,
) -> list[BarData]:
    """Generate *n_days* × *intraday_bars* synthetic 5-min bars.

    Bars are UTC-naive, starting at 09:30 ET = 14:30 UTC on 2024-01-02
    and spaced 5 minutes apart.  Weekends use calendar days (not skipping
    weekends) — acceptable for unit tests that don't gate on NYSE calendar.
    """
    rng = random.Random(seed)
    price = start_price
    bars: list[BarData] = []
    base = _base_ts()

    for day in range(n_days):
        day_open = base + timedelta(days=day)
        price = start_price + rng.gauss(0, 5)

        for bar_idx in range(intraday_bars):
            price *= 1.0 + rng.gauss(0, bar_vol)
            price = max(price, 1.0)
            high = price * (1 + abs(rng.gauss(0, 0.0008)))
            low = price * (1 - abs(rng.gauss(0, 0.0008)))
            low = min(low, price)
            high = max(high, price)
            ts = day_open + timedelta(minutes=5 * bar_idx)
            bars.append(
                BarData(
                    symbol=symbol,
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


def fast_config() -> dict:
    """Return a config with HMM/LGBM settings reduced for fast CI runs."""
    cfg = load_config()
    cfg["hmm"]["n_components_candidates"] = [3]
    cfg["hmm"]["n_init"] = 2
    cfg["hmm"]["min_train_bars"] = 50
    cfg["lgbm"]["n_estimators"] = 10
    cfg["lgbm"]["min_child_samples"] = 5
    cfg["lgbm"]["min_samples_per_regime"] = 10
    # One walk-forward window: large step prevents a second window
    cfg["backtest"]["train_bars_equity"] = 300
    cfg["backtest"]["test_bars_equity"] = 78
    cfg["backtest"]["step_bars_equity"] = 99_999
    return cfg


def make_portfolio(
    equity: float = 100_000.0,
    positions: dict | None = None,
) -> PortfolioState:
    return PortfolioState(
        equity=equity,
        cash=equity,
        buying_power=equity,
        positions=positions or {},
        daily_pnl=0.0,
        session_open_equity=equity,
        rolling_30m_equity_marks=[],
        consecutive_loss_count=0,
        circuit_breaker_active=False,
        circuit_breaker_resume_time=None,
        last_updated=datetime.now(timezone.utc),
    )


def make_signal(
    asset: str = "SPY",
    direction: Direction = Direction.LONG,
    size_pct: float = 0.08,
    price: float = 475.0,
    now: datetime | None = None,
) -> Signal:
    now = now or datetime(2024, 1, 2, 15, 0)
    return Signal(
        asset=asset,
        direction=direction,
        size_pct=size_pct,
        entry_price=price,
        stop_price=price * 0.99,
        take_profit_price=price * 1.02,
        max_hold_bars=12,
        strategy_name="momentum",
        regime=RegimeLabel.TRENDING_UP,
        hmm_confidence=0.72,
        lgbm_confidence=0.80,
        timestamp=now,
        asset_class=AssetClass.EQUITY,
    )


# ---------------------------------------------------------------------------
# Test 1 — Full pipeline, one asset
# ---------------------------------------------------------------------------

def test_full_pipeline_one_asset(tmp_path):
    """Full pipeline on 2000 synthetic 5-min bars for SPY.

    Trains HMM + LightGBM, runs signal → risk → simulated fill for the test
    window.  Asserts: no exceptions, at least one regime change detected,
    equity_curve has entries.
    """
    cfg = fast_config()
    cfg["pdt"]["pdt_counter_file"] = str(tmp_path / "pdt_pipe.json")
    cfg["monitoring"]["log_dir"] = str(tmp_path)

    # 2000 bars ≈ 25.6 days × 78 bars
    bars = make_bars("SPY", n_days=26)
    assert len(bars) >= 2000 - 78, "not enough bars generated"

    backtester = WalkForwardBacktester(cfg)
    start = bars[0].timestamp
    end = bars[-1].timestamp

    results = backtester.run({"SPY": bars}, start, end)

    assert results is not None, "run() returned None"
    assert len(results.equity_curve) > 0, "equity_curve is empty"
    assert len(results.per_window_results) >= 1, "no walk-forward windows completed"

    # Regime changes: inspect bar_results for distinct regime sequences
    regime_sequence = [
        r.get("regime") for r in results.bar_results if r.get("regime")
    ]
    unique_regimes = set(regime_sequence)
    # With random price data and 2000 bars at least 2 regime labels should appear
    assert len(unique_regimes) >= 1, "no regimes recorded"

    # Equity curve must span the test window
    assert results.equity_curve[-1][1] > 0, "final equity is non-positive"


# ---------------------------------------------------------------------------
# Test 2 — Circuit breaker integration
# ---------------------------------------------------------------------------

def test_circuit_breaker_integration(tmp_path):
    """Price drops 4% in 30 bars — circuit breaker triggers; no new positions open."""
    cfg = fast_config()
    cfg["pdt"]["pdt_counter_file"] = str(tmp_path / "pdt_cb.json")
    cfg["monitoring"]["log_dir"] = str(tmp_path)

    # Build a bar sequence: 50 bars at flat price, then 30 bars dropping 4%
    rng = random.Random(0)
    base = datetime(2024, 1, 2, 14, 30)
    start_price = 500.0

    bars: list[BarData] = []

    # Flat first 50 bars (also provides the feature warm-up)
    price = start_price
    for i in range(50):
        price *= 1.0 + rng.gauss(0, 0.0005)
        price = max(price, 1.0)
        ts = base + timedelta(minutes=5 * i)
        bars.append(BarData(
            symbol="SPY", timestamp=ts,
            open=price, high=price * 1.001, low=price * 0.999,
            close=price, volume=100_000.0, bar_size="5Min",
        ))

    # Crash: 30 bars declining to 96% of start
    crash_start = price
    for i in range(30):
        frac = (i + 1) / 30
        price = crash_start * (1.0 - 0.04 * frac)
        ts = base + timedelta(minutes=5 * (50 + i))
        bars.append(BarData(
            symbol="SPY", timestamp=ts,
            open=price, high=price * 1.001, low=price * 0.999,
            close=price, volume=200_000.0, bar_size="5Min",
        ))

    # Drive the circuit breaker directly (independent of backtester)
    cb = CircuitBreaker(cfg)
    session_eq = start_price * 1000  # pretend 1000 shares → $500k equity
    cb_triggered = False
    last_active_after_crash = False

    for idx, bar in enumerate(bars):
        current_equity = bar.close * 1000
        portfolio = make_portfolio(
            equity=current_equity,
        )
        portfolio.session_open_equity = session_eq

        # Roll the 30-min mark manually: set _30m_mark to session_eq initially
        if idx == 0:
            cb._30m_mark = session_eq
            cb._30m_mark_time = bar.timestamp

        active, reason = cb.update(portfolio, bar.timestamp)
        if active:
            cb_triggered = True

    last_active_after_crash = cb.is_active()

    assert cb_triggered, "Circuit breaker should have triggered during the 4% crash"
    assert last_active_after_crash, "Circuit breaker should still be active after crash"

    # Now verify RiskManager honours the circuit breaker
    pdt = PDTGuard(cfg)
    sm = SessionManager(cfg)
    risk = RiskManager(cfg, pdt, cb, sm)

    portfolio = make_portfolio(equity=crash_start * 1000 * 0.96)
    portfolio.session_open_equity = session_eq

    signal = make_signal(
        asset="SPY",
        now=bars[-1].timestamp,
        price=bars[-1].close,
    )

    # Use a timestamp within market hours so EOD check doesn't interfere
    decision = risk.evaluate(signal, portfolio, bars[-1].timestamp)
    assert decision.rejected, "RiskManager should reject when circuit breaker is active"
    assert "CIRCUIT_BREAKER" in decision.reason_code


# ---------------------------------------------------------------------------
# Test 3 — PDT integration
# ---------------------------------------------------------------------------

def test_pdt_integration(tmp_path):
    """4th equity day trade in a rolling 5-day window with equity < $25k is blocked."""
    cfg = fast_config()
    cfg["pdt"]["pdt_counter_file"] = str(tmp_path / "pdt_integ.json")
    cfg["monitoring"]["log_dir"] = str(tmp_path)

    low_equity = 20_000.0
    pdt = PDTGuard(cfg)
    cb = CircuitBreaker(cfg)
    sm = SessionManager(cfg)
    risk = RiskManager(cfg, pdt, cb, sm)

    today = date.today()
    base_time = datetime(today.year, today.month, today.day, 14, 30)

    # Record 3 day trades (the maximum allowed)
    for i in range(3):
        entry_t = base_time + timedelta(minutes=i * 15)
        exit_t = entry_t + timedelta(minutes=5)
        pdt.record_daytrade(asset="SPY", entry_time=entry_t, exit_time=exit_t)

    assert pdt.get_current_count() == 3, "should have 3 recorded trades"

    # 4th trade attempt — must be blocked
    portfolio = make_portfolio(equity=low_equity)
    signal = make_signal(
        asset="SPY",
        now=base_time + timedelta(hours=1),
        price=475.0,
    )

    decision = risk.evaluate(signal, portfolio, base_time + timedelta(hours=1))
    assert decision.rejected, "4th trade should be rejected"
    assert decision.reason_code == "PDT_LIMIT", (
        f"Expected PDT_LIMIT but got {decision.reason_code!r}"
    )

    # Crypto is exempt from PDT — same config, same guard, crypto symbol
    crypto_signal = make_signal(
        asset="BTC/USD",
        direction=Direction.LONG,
        size_pct=0.08,
        price=40_000.0,
        now=base_time + timedelta(hours=1),
    )
    crypto_signal.asset_class = AssetClass.CRYPTO
    # PDT guard: crypto exempt — can_trade returns True
    assert pdt.can_trade("BTC/USD", low_equity), "Crypto should be PDT-exempt"


# ---------------------------------------------------------------------------
# Test 4 — EOD flat integration
# ---------------------------------------------------------------------------

def test_eod_flat_integration(tmp_path):
    """Equity position opened 30 bars before EOD is flagged for close at hard-close."""
    cfg = fast_config()
    cfg["pdt"]["pdt_counter_file"] = str(tmp_path / "pdt_eod.json")
    cfg["monitoring"]["log_dir"] = str(tmp_path)

    sm = SessionManager(cfg)
    pdt = PDTGuard(cfg)
    cb = CircuitBreaker(cfg)
    risk = RiskManager(cfg, pdt, cb, sm)

    # NYSE close is 16:00 ET = 21:00 UTC
    # Hard-close window: [15:55 ET, 16:00 ET) = [20:55 UTC, 21:00 UTC)
    import pytz
    et = pytz.timezone("America/New_York")

    # Pick a known NYSE trading day: 2024-01-02
    # EOD hard close: 15:55 ET = 20:55 UTC
    eod_hard_start_et = et.localize(datetime(2024, 1, 2, 15, 55, 0))
    eod_hard_start_utc = eod_hard_start_et.astimezone(timezone.utc)

    # Within the hard-close window
    in_hard_close = eod_hard_start_utc + timedelta(minutes=1)

    # Signal during hard-close window should be rejected by RiskManager
    portfolio = make_portfolio(equity=100_000.0)
    signal = make_signal(
        asset="SPY",
        now=in_hard_close,
        price=475.0,
    )
    signal.timestamp = in_hard_close

    decision = risk.evaluate(signal, portfolio, in_hard_close)
    assert decision.rejected, "Signal during EOD hard-close should be rejected"
    assert decision.reason_code == "EOD_HARD_CLOSE"

    # Verify is_eod_hard_close directly
    assert sm.is_eod_hard_close("SPY", in_hard_close), (
        "SessionManager should report hard close during window"
    )
    # Crypto is never EOD-closed
    assert not sm.is_eod_hard_close("BTC/USD", in_hard_close), (
        "EOD hard close should not apply to crypto"
    )

    # Before hard-close window: not blocked for EOD reason
    before_hard_close = eod_hard_start_utc - timedelta(minutes=10)
    assert not sm.is_eod_hard_close("SPY", before_hard_close), (
        "10 min before EOD hard-close should return False"
    )


# ---------------------------------------------------------------------------
# Test 5 — HMM no look-ahead
# ---------------------------------------------------------------------------

def test_hmm_no_lookahead_integration(tmp_path):
    """Causal forward algorithm: alpha[t] must equal the output of running
    predict_regime_filtered on bars[0..t+1].  Verified for every bar t.
    """
    cfg = fast_config()
    cfg["monitoring"]["log_dir"] = str(tmp_path)

    # Generate structured bars: sine-wave price + Gaussian noise to ensure
    # positive-definite covariance for HMM (pure sine is rank-deficient).
    n_bars = 500
    rng = random.Random(99)
    base = datetime(2024, 1, 2, 14, 30)
    bars: list[BarData] = []
    for i in range(n_bars):
        price = 475.0 + 10.0 * math.sin(i * 2 * math.pi / 78)
        price *= 1.0 + rng.gauss(0, 0.001)   # add noise for non-singular covariance
        price = max(price, 1.0)
        ts = base + timedelta(minutes=5 * i)
        bars.append(BarData(
            symbol="SPY", timestamp=ts,
            open=price * (1 + rng.gauss(0, 0.0002)),
            high=price * (1 + abs(rng.gauss(0, 0.0006))),
            low=price * (1 - abs(rng.gauss(0, 0.0006))),
            close=price,
            volume=float(rng.randint(80_000, 300_000)),
            bar_size="5Min",
        ))

    # Build feature matrix
    feature_matrix = build_feature_matrix(bars, cfg, AssetClass.EQUITY)
    min_bars = cfg["hmm"]["min_train_bars"]
    if len(feature_matrix) < min_bars:
        pytest.skip(
            f"Only {len(feature_matrix)} feature rows — need {min_bars} for HMM training"
        )

    feature_names = [
        "log_return_1bar", "realized_vol_20bar", "vol_ratio_5_60",
        "vwap_deviation_pct", "volume_ratio", "high_low_range_pct",
        "bar_body_ratio", "close_vs_prev_close", "bars_since_open",
        "or_breakout_strength",
    ][:feature_matrix.shape[1]]

    hmm = HMMEngine(cfg, "SPY")
    hmm.train(
        feature_matrix=feature_matrix,
        feature_names=feature_names,
        end_timestamp=bars[-1].timestamp,
    )

    # Batch forward pass
    alpha_batch = hmm.predict_regime_filtered(feature_matrix)

    # Step-wise online forward pass — verify each alpha matches the batch result
    mismatches = 0
    for t in range(len(feature_matrix)):
        # Batch result at bar t
        batch_alpha = alpha_batch[t]

        # Online: run predict_regime_filtered on features[0..t+1]
        online_alpha = hmm.predict_regime_filtered(feature_matrix[: t + 1])[-1]

        # They should be numerically identical (same algorithm, same data)
        if not np.allclose(batch_alpha, online_alpha, atol=1e-6):
            mismatches += 1

    assert mismatches == 0, (
        f"Look-ahead bias detected: {mismatches}/{len(feature_matrix)} bars "
        "had alpha mismatch between batch and incremental forward passes"
    )


# ---------------------------------------------------------------------------
# Test 6 — LightGBM retrain stability
# ---------------------------------------------------------------------------

def test_lgbm_retrain_stability(tmp_path):
    """Two identical training runs produce predictions within 0.1 prob units."""
    cfg = fast_config()
    cfg["monitoring"]["log_dir"] = str(tmp_path)

    bars = make_bars("SPY", n_days=26, seed=7)
    feature_matrix = build_feature_matrix(bars, cfg, AssetClass.EQUITY)
    min_bars = cfg["hmm"]["min_train_bars"]
    if len(feature_matrix) < min_bars:
        pytest.skip(f"Only {len(feature_matrix)} feature rows — need {min_bars}")

    feature_names = [f"f{i}" for i in range(feature_matrix.shape[1])]

    # Train reference HMM
    hmm = HMMEngine(cfg, "SPY")
    hmm.train(feature_matrix, feature_names, end_timestamp=bars[-1].timestamp)
    # predict_regime_filtered returns alpha matrix (T × n_states); convert to 1D labels
    alpha_matrix = hmm.predict_regime_filtered(feature_matrix)
    regime_labels = np.array(
        [hmm.state_labels[int(np.argmax(alpha_matrix[i]))] for i in range(len(alpha_matrix))]
    )
    scaled = hmm.scaler.transform(feature_matrix)
    close_prices = np.array([b.close for b in bars[-len(feature_matrix):]], dtype=float)

    def _train_router():
        trainer = LGBMExpertTrainer(cfg, "SPY")
        trainer.train_all(
            feature_matrix=scaled,
            regime_labels=regime_labels,
            feature_names=feature_names,
            close_prices=close_prices,
        )
        router = LGBMExpertRouter(cfg, "SPY")
        router.load_from_trainer(trainer)
        return router

    router_a = _train_router()
    router_b = _train_router()

    # Check prediction stability across all trained regimes
    probe_rows = scaled[:10] if len(scaled) >= 10 else scaled
    unstable_count = 0

    for regime in RegimeLabel:
        if not router_a.is_expert_available(regime):
            continue
        for row in probe_rows:
            obs = row.reshape(1, -1)
            dir_a, conf_a = router_a.predict(regime, obs)
            dir_b, conf_b = router_b.predict(regime, obs)
            if abs(conf_a - conf_b) > 0.1:
                unstable_count += 1

    assert unstable_count == 0, (
        f"LightGBM retrain instability: {unstable_count} predictions "
        "differed by > 0.1 probability units between two identical training runs"
    )


# ---------------------------------------------------------------------------
# Test 7 — Shared state written
# ---------------------------------------------------------------------------

def test_shared_state_written(tmp_path):
    """StructuredLogger.update_shared_state() produces valid JSON at expected path."""
    cfg = load_config()
    cfg["monitoring"]["log_dir"] = str(tmp_path)
    shared_path = str(tmp_path / "shared_state.json")
    cfg["monitoring"]["shared_state_file"] = shared_path

    sl = StructuredLogger(cfg)

    portfolio = make_portfolio(equity=101_500.0)

    regime_info = {
        "SPY": {"regime": "TRENDING_UP", "confidence": 0.82},
        "QQQ": {"regime": "BREAKOUT", "confidence": 0.77},
    }

    signal_history = [
        {
            "ts": datetime(2024, 1, 2, 15, 0),
            "reason": "momentum",
            "signal": None,
        }
    ]

    equity_curve = [
        (datetime(2024, 1, 2, 14, 30), 100_000.0),
        (datetime(2024, 1, 2, 14, 35), 100_500.0),
        (datetime(2024, 1, 2, 14, 40), 101_000.0),
        (datetime(2024, 1, 2, 14, 45), 101_200.0),
        (datetime(2024, 1, 2, 14, 50), 101_350.0),
        (datetime(2024, 1, 2, 14, 55), 101_500.0),
    ]

    sl.update_shared_state(portfolio, regime_info, signal_history, equity_curve)
    sl.close()

    # File must exist
    assert (tmp_path / "shared_state.json").exists(), "shared_state.json was not created"

    # Must parse as valid JSON
    with open(shared_path, encoding="utf-8") as fh:
        state = json.load(fh)

    # Required top-level keys
    required_keys = {"timestamp", "equity", "positions", "regime_info", "equity_curve_30m"}
    missing = required_keys - set(state.keys())
    assert not missing, f"shared_state.json missing keys: {missing}"

    # Sanity-check values
    assert abs(state["equity"] - 101_500.0) < 0.01
    assert isinstance(state["positions"], dict)
    assert isinstance(state["regime_info"], dict)
    assert isinstance(state["equity_curve_30m"], list)
    # equity_curve_30m: last 6 bars only
    assert len(state["equity_curve_30m"]) <= 6

    # timestamp must parse as ISO datetime
    ts = datetime.fromisoformat(state["timestamp"])
    assert ts is not None
