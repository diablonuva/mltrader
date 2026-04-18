from __future__ import annotations

import os
import tempfile

import numpy as np
import pytest

from src.brain.lgbm_experts import LGBMExpertRouter, LGBMExpertTrainer
from src.models import Direction, RegimeLabel

# ---------------------------------------------------------------------------
# Shared config — fast settings for unit tests
# ---------------------------------------------------------------------------

_CONFIG = {
    "lgbm": {
        "n_estimators": 50,
        "max_depth": 3,
        "num_leaves": 8,
        "learning_rate": 0.1,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "min_child_samples": 5,
        "class_weight": "balanced",
        "confidence_threshold": 0.40,   # lower threshold so tests get non-FLAT output
        "label_lookahead_bars": 3,
        "label_threshold_pct": 0.003,
        "min_samples_per_regime": 50,
    }
}

_VALID_DIRECTIONS = {Direction.LONG, Direction.SHORT, Direction.FLAT}

N_FEATURES = 9


def _make_synthetic_data(
    n_bars: int = 500,
    n_features: int = N_FEATURES,
    seed: int = 42,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (feature_matrix, regime_labels, close_prices).

    Three regimes of equal size:
      - SQUEEZE:       low-vol, mean-reverting prices
      - TRENDING_UP:   strong uptrend  (> 0.3% per bar on avg)
      - TRENDING_DOWN: strong downtrend
    """
    rng = np.random.default_rng(seed)
    block = n_bars // 3

    # --- feature blocks ---
    b_squeeze = rng.normal(
        loc=[0.0, 0.05, 0.6, 0.0, 1.0, 0.1, 0.3, 0.4, 0.4],
        scale=0.01, size=(block, n_features),
    )
    b_up = rng.normal(
        loc=[0.006, 0.15, 1.2, 0.3, 1.8, 0.4, 0.7, 0.0, 1.0],
        scale=0.02, size=(block, n_features),
    )
    b_down = rng.normal(
        loc=[-0.006, 0.15, 1.2, -0.3, 1.8, 0.4, 0.7, 0.8, 0.2],
        scale=0.02, size=(n_bars - 2 * block, n_features),
    )
    X = np.vstack([b_squeeze, b_up, b_down])

    # --- regime labels ---
    labels = np.empty(n_bars, dtype=object)
    labels[:block] = RegimeLabel.SQUEEZE
    labels[block : 2 * block] = RegimeLabel.TRENDING_UP
    labels[2 * block :] = RegimeLabel.TRENDING_DOWN

    # --- close prices ---
    # Squeeze: ±0.05% per bar  |  Up: +0.6% per bar  |  Down: -0.6% per bar
    daily_drifts = np.concatenate([
        rng.normal(0.0000, 0.0005, block),
        rng.normal(0.006, 0.001, block),
        rng.normal(-0.006, 0.001, n_bars - 2 * block),
    ])
    prices = 100.0 * np.cumprod(1.0 + daily_drifts)

    return X, labels, prices


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_training_produces_experts():
    X, labels, prices = _make_synthetic_data()
    feature_names = [
        "log_return_1bar", "realized_vol_20bar", "vol_ratio_5_60",
        "vwap_deviation_pct", "volume_ratio", "high_low_range_pct",
        "bar_body_ratio", "time_sin", "time_cos",
    ]

    trainer = LGBMExpertTrainer(_CONFIG, asset="SPY")
    trainer.train_all(X, labels, feature_names, close_prices=prices)

    assert len(trainer.experts) >= 1, "Expected at least one expert to be trained"
    assert trainer.trained_regimes == set(trainer.experts.keys())

    # Build a router and verify predict returns a valid Direction
    router = LGBMExpertRouter(_CONFIG, asset="SPY")
    router.load_from_trainer(trainer)

    for regime in trainer.trained_regimes:
        direction, confidence = router.predict(regime, X[0])
        assert direction in _VALID_DIRECTIONS, f"Invalid direction {direction} for {regime}"
        assert 0.0 <= confidence <= 1.0, f"Confidence {confidence} out of [0, 1]"


def test_no_expert_returns_flat():
    """Router with no experts must always return (FLAT, 0.0)."""
    router = LGBMExpertRouter(_CONFIG, asset="SPY")
    features = np.random.default_rng(0).normal(size=N_FEATURES)

    direction, confidence = router.predict(RegimeLabel.TRENDING_UP, features)

    assert direction is Direction.FLAT
    assert confidence == 0.0


def test_save_load_roundtrip():
    """Saved experts must produce identical predictions when loaded back."""
    X, labels, prices = _make_synthetic_data(seed=7)
    feature_names = [
        "log_return_1bar", "realized_vol_20bar", "vol_ratio_5_60",
        "vwap_deviation_pct", "volume_ratio", "high_low_range_pct",
        "bar_body_ratio", "time_sin", "time_cos",
    ]

    # Train and save
    trainer = LGBMExpertTrainer(_CONFIG, asset="SPY")
    trainer.train_all(X, labels, feature_names, close_prices=prices)
    assert len(trainer.experts) >= 1, "No experts trained — check synthetic data generation"

    with tempfile.TemporaryDirectory() as tmpdir:
        trainer.save_all(tmpdir)

        # Load into a fresh trainer
        trainer2 = LGBMExpertTrainer(_CONFIG, asset="SPY")
        trainer2.load_all(tmpdir, list(trainer.trained_regimes))

        assert set(trainer2.experts.keys()) == set(trainer.experts.keys())

        # Compare predictions from original vs loaded router
        router_orig = LGBMExpertRouter(_CONFIG, asset="SPY")
        router_orig.load_from_trainer(trainer)

        router_loaded = LGBMExpertRouter(_CONFIG, asset="SPY")
        router_loaded.load_from_trainer(trainer2)

        sample = X[10]
        for regime in trainer.trained_regimes:
            dir_orig, conf_orig = router_orig.predict(regime, sample)
            dir_load, conf_load = router_loaded.predict(regime, sample)

            assert dir_orig == dir_load, (
                f"Direction mismatch for {regime}: {dir_orig} vs {dir_load}"
            )
            assert abs(conf_orig - conf_load) < 1e-9, (
                f"Confidence mismatch for {regime}: {conf_orig} vs {conf_load}"
            )
