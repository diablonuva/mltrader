from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone

import numpy as np
import pytest

from src.brain.hmm_engine import HMMEngine
from src.models import RegimeLabel


# Minimal config that matches the real schema
_CONFIG = {
    "hmm": {
        "n_components_candidates": [3, 4, 5],
        "n_init": 3,
        "covariance_type": "full",
        "n_iter": 50,
        "tol": 1e-3,
        "confidence_threshold": 0.55,
        "min_train_bars": 300,
    }
}

_FEATURE_NAMES = [
    "log_return_1bar",
    "realized_vol_20bar",
    "vol_ratio_5_60",
    "vwap_deviation_pct",
    "volume_ratio",
    "high_low_range_pct",
    "bar_body_ratio",
    "time_sin",
    "time_cos",
]


def _make_synthetic_matrix(n_rows: int = 300, n_features: int = 9) -> np.ndarray:
    """Three distinct blocks to give the HMM clear structure to latch onto."""
    rng = np.random.default_rng(42)
    block = n_rows // 3

    # Low-vol block
    b1 = rng.normal(loc=[0.0001, 0.05, 0.6, 0.01, 1.0, 0.1, 0.3, 0.5, 0.5], scale=0.01, size=(block, n_features))
    # High-vol block
    b2 = rng.normal(loc=[0.002,  0.25, 1.4, 0.50, 2.0, 0.5, 0.6, 0.0, 1.0], scale=0.05, size=(block, n_features))
    # Medium-vol block
    b3 = rng.normal(loc=[0.0005, 0.12, 1.0, 0.10, 1.2, 0.2, 0.5, 0.8, 0.2], scale=0.02, size=(n_rows - 2 * block, n_features))

    return np.vstack([b1, b2, b3])


def test_training():
    engine = HMMEngine(_CONFIG, asset="SPY")
    X = _make_synthetic_matrix(300)
    ts = datetime(2024, 1, 2, 16, 0, tzinfo=timezone.utc)

    engine.train(X, _FEATURE_NAMES, end_timestamp=ts)

    # Core post-training assertions
    assert engine.is_trained is True
    assert engine.n_states in [3, 4, 5, 6, 7]
    assert len(engine.state_labels) == engine.n_states
    assert all(isinstance(lbl, RegimeLabel) for lbl in engine.state_labels)
    assert engine.training_bars == 300
    assert engine.training_end_timestamp == ts

    # Save → load round-trip
    with tempfile.NamedTemporaryFile(suffix=".pkl", delete=False) as f:
        tmp_path = f.name
    try:
        engine.save(tmp_path)

        engine2 = HMMEngine(_CONFIG, asset="SPY")
        engine2.load(tmp_path)

        assert engine2.is_trained is True
        assert engine2.n_states == engine.n_states
        assert engine2.state_labels == engine.state_labels
        assert engine2.training_bars == engine.training_bars
    finally:
        os.unlink(tmp_path)


def test_no_look_ahead():
    """Forward algorithm is strictly causal: alpha[t] must not depend on x[t+1..]."""
    engine = HMMEngine(_CONFIG, asset="SPY")
    X = _make_synthetic_matrix(300)
    ts = datetime(2024, 1, 2, 16, 0, tzinfo=timezone.utc)
    engine.train(X, _FEATURE_NAMES, end_timestamp=ts)

    full_alpha = engine.predict_regime_filtered(X)  # (300, n_states)

    for t in range(1, 300):
        partial_alpha = engine.predict_regime_filtered(X[: t + 1])
        np.testing.assert_allclose(
            full_alpha[t],
            partial_alpha[t],
            atol=1e-6,
            err_msg=f"Look-ahead bias detected at t={t}",
        )


def test_online_matches_batch():
    """step() one-by-one must reproduce predict_regime_filtered() exactly."""
    engine = HMMEngine(_CONFIG, asset="SPY")
    X = _make_synthetic_matrix(300)
    ts = datetime(2024, 1, 2, 16, 0, tzinfo=timezone.utc)
    engine.train(X, _FEATURE_NAMES, end_timestamp=ts)

    # Batch reference on first 100 rows
    batch_alpha = engine.predict_regime_filtered(X[:100])  # (100, n_states)

    # Online replay — inference state starts fresh (alpha_current is None after training)
    for t in range(100):
        engine.step(X[t])
        online_alpha = engine.get_regime_probability()
        np.testing.assert_allclose(
            online_alpha,
            batch_alpha[t],
            atol=1e-6,
            err_msg=f"Online/batch mismatch at t={t}",
        )
