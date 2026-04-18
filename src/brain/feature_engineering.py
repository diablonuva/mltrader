from __future__ import annotations

import math
import os
import sys
from collections import deque
from datetime import datetime, timezone
from typing import Optional

# Allow running as `python src/brain/feature_engineering.py` from project root
if __name__ == "__main__":
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import numpy as np
from sklearn.preprocessing import StandardScaler

from src.brain.opening_range import OpeningRange
from src.brain.vwap_calculator import VWAPCalculator
from src.models import AssetClass, BarData


# Annualisation factors — recalculated at runtime from config bar_size.
# These module-level defaults are for 5-min bars; FeatureEngineer overrides them.
_BARS_PER_YEAR_EQUITY = 252 * 78   # 5-min bars
_BARS_PER_YEAR_CRYPTO = 365 * 288  # 5-min bars (24 h × 12)

_EQUITY_FEATURE_NAMES = [
    "log_return_1bar",
    "realized_vol_20bar",
    "vol_ratio_5_60",
    "vwap_deviation_pct",
    "volume_ratio",
    "high_low_range_pct",
    "bar_body_ratio",
    "time_sin",
    "time_cos",
    "or_position",
]

_CRYPTO_FEATURE_NAMES = [
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

_BARS_PER_SESSION_EQUITY = 78
_BARS_PER_SESSION_CRYPTO = 288


def _bars_per_session_from_config(config: dict, asset_class: AssetClass) -> int:
    """Derive bars-per-session from the bar_size setting (e.g. '15Min' → 26)."""
    bar_size_str: str = config.get("features", {}).get("bar_size", "5Min")
    try:
        minutes_per_bar = int(bar_size_str.replace("Min", "").strip())
    except ValueError:
        minutes_per_bar = 5
    if asset_class is AssetClass.EQUITY:
        return max(1, 390 // minutes_per_bar)   # 6.5 h × 60 min
    else:
        return max(1, 1440 // minutes_per_bar)  # 24 h × 60 min


def _bars_per_year_from_config(config: dict, asset_class: AssetClass) -> int:
    trading_days = 252 if asset_class is AssetClass.EQUITY else 365
    return trading_days * _bars_per_session_from_config(config, asset_class)


class FeatureEngineer:
    """Computes a causal 9- or 10-dimensional feature vector from bar history."""

    def __init__(self, config: dict, asset_class: AssetClass) -> None:
        feat_cfg = config.get("features", {})
        self._asset_class = asset_class
        self._realized_vol_window: int = int(feat_cfg.get("realized_vol_window", 20))
        self._vol_ratio_short: int = int(feat_cfg.get("vol_ratio_short_window", 5))
        self._vol_ratio_long: int = int(feat_cfg.get("vol_ratio_long_window", 60))
        self._outlier_clip_stds: float = float(feat_cfg.get("outlier_clip_stds", 5.0))

        # Bar-size-aware constants derived from config
        self._bars_per_session: int = _bars_per_session_from_config(config, asset_class)
        self._bars_per_year: int = _bars_per_year_from_config(config, asset_class)

        self._vwap = VWAPCalculator(
            asset_class,
            timezone=config.get("session", {}).get("timezone", "America/New_York"),
        )
        self._or: Optional[OpeningRange] = (
            OpeningRange(n_bars=int(feat_cfg.get("opening_range_bars", 6)))
            if asset_class is AssetClass.EQUITY
            else None
        )

        maxlen = self._vol_ratio_long + 10
        self._history: deque[BarData] = deque(maxlen=maxlen)

        self._scaler = StandardScaler()
        self.is_scaler_fitted: bool = False

    # ------------------------------------------------------------------
    # Core update
    # ------------------------------------------------------------------

    def update(self, bar: BarData) -> None:
        self._history.append(bar)
        self._vwap.update(bar)

    # ------------------------------------------------------------------
    # Feature computation
    # ------------------------------------------------------------------

    def compute_features(self, bars_since_open: int) -> np.ndarray | None:
        if self._or is not None:
            # Update opening range tracker with the latest bar
            latest = self._history[-1] if self._history else None
            if latest is not None:
                self._or.update(latest, bars_since_open)

        if len(self._history) < self._vol_ratio_long:
            return None

        bars = list(self._history)
        closes = np.array([b.close for b in bars], dtype=float)
        volumes = np.array([b.volume for b in bars], dtype=float)
        latest = bars[-1]

        # a. log_return_1bar
        log_return_1bar = math.log(closes[-1] / closes[-2]) if closes[-2] > 0 else 0.0

        # b. realized_vol_20bar
        log_returns = np.log(closes[1:] / closes[:-1])
        recent_returns = log_returns[-self._realized_vol_window:]
        ann_factor = self._bars_per_year
        realized_vol = float(np.std(recent_returns, ddof=1)) * math.sqrt(ann_factor)

        # c. vol_ratio_5_60
        std_short = float(np.std(log_returns[-self._vol_ratio_short:], ddof=1))
        std_long = float(np.std(log_returns[-self._vol_ratio_long:], ddof=1))
        vol_ratio = std_short / std_long if std_long >= 1e-8 else 1.0

        # d. vwap_deviation_pct
        vwap_dev = self._vwap.get_deviation_pct(latest.close)

        # e. volume_ratio
        mean_vol = float(np.mean(volumes[-self._realized_vol_window:]))
        volume_ratio = latest.volume / mean_vol if mean_vol > 0 else 1.0

        # f. high_low_range_pct
        hl_range_pct = (latest.high - latest.low) / latest.close * 100.0

        # g. bar_body_ratio
        bar_body_ratio = abs(latest.close - latest.open) / (
            latest.high - latest.low + 1e-8
        )

        # h/i. time_sin / time_cos
        phase = 2.0 * math.pi * bars_since_open / self._bars_per_session
        time_sin = math.sin(phase)
        time_cos = math.cos(phase)

        raw = [
            log_return_1bar,
            realized_vol,
            vol_ratio,
            vwap_dev,
            volume_ratio,
            hl_range_pct,
            bar_body_ratio,
            time_sin,
            time_cos,
        ]

        # j. or_position (equity only)
        if self._asset_class is AssetClass.EQUITY and self._or is not None:
            raw.append(self._or.get_or_position(latest.close))

        return np.array(raw, dtype=float)

    # ------------------------------------------------------------------
    # Scaler
    # ------------------------------------------------------------------

    def fit_scaler(self, feature_matrix: np.ndarray) -> None:
        stds = np.std(feature_matrix, axis=0, ddof=1)
        means = np.mean(feature_matrix, axis=0)
        lo = means - self._outlier_clip_stds * stds
        hi = means + self._outlier_clip_stds * stds
        clipped = np.clip(feature_matrix, lo, hi)
        self._scaler.fit(clipped)
        self.is_scaler_fitted = True

    def transform(self, raw_features: np.ndarray) -> np.ndarray:
        if not self.is_scaler_fitted:
            raise RuntimeError("Scaler has not been fitted. Call fit_scaler() first.")
        vec = raw_features.reshape(1, -1)
        stds = np.std(vec, axis=0, ddof=0)  # single-row: use scaler's saved params
        # Clip using scaler's learned mean/scale
        means = self._scaler.mean_
        scales = self._scaler.scale_
        lo = means - self._outlier_clip_stds * scales
        hi = means + self._outlier_clip_stds * scales
        clipped = np.clip(vec, lo, hi)
        return self._scaler.transform(clipped).flatten()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def get_feature_names(self) -> list[str]:
        return (
            _EQUITY_FEATURE_NAMES.copy()
            if self._asset_class is AssetClass.EQUITY
            else _CRYPTO_FEATURE_NAMES.copy()
        )

    def reset_session(self) -> None:
        self._vwap.reset()
        if self._or is not None:
            self._or.reset()


# ---------------------------------------------------------------------------
# Standalone helper
# ---------------------------------------------------------------------------

def build_feature_matrix(
    bar_history: list[BarData],
    config: dict,
    asset_class: AssetClass,
) -> np.ndarray:
    """Feed bars sequentially into a fresh FeatureEngineer.

    Returns an (n, features) array with rows where compute_features
    returned None removed.
    """
    engineer = FeatureEngineer(config, asset_class)
    rows: list[np.ndarray] = []
    for i, bar in enumerate(bar_history):
        engineer.update(bar)
        vec = engineer.compute_features(bars_since_open=i)
        if vec is not None:
            rows.append(vec)
    if not rows:
        n_features = 10 if asset_class is AssetClass.EQUITY else 9
        return np.empty((0, n_features), dtype=float)
    return np.vstack(rows)


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import random
    from src.config_loader import load_config

    rng = random.Random(42)
    cfg = load_config()

    price = 100.0
    bars: list[BarData] = []
    base_ts = datetime(2024, 1, 2, 9, 30, tzinfo=timezone.utc)

    for i in range(100):
        price *= 1.0 + rng.gauss(0, 0.002)
        high = price * (1 + abs(rng.gauss(0, 0.001)))
        low = price * (1 - abs(rng.gauss(0, 0.001)))
        from datetime import timedelta
        ts = base_ts + timedelta(minutes=5 * i)
        bars.append(
            BarData(
                symbol="SPY",
                timestamp=ts,
                open=price,
                high=high,
                low=low,
                close=price,
                volume=float(rng.randint(1000, 5000)),
                bar_size="5Min",
            )
        )

    matrix = build_feature_matrix(bars, cfg, AssetClass.EQUITY)
    print(f"Feature matrix shape: {matrix.shape}")
    print(f"Last feature vector ({len(matrix[-1])} values):")
    from src.brain.feature_engineering import _EQUITY_FEATURE_NAMES
    for name, val in zip(_EQUITY_FEATURE_NAMES, matrix[-1]):
        print(f"  {name:25s}: {val:.6f}")
