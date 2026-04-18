from __future__ import annotations

import logging
import os
import warnings
from typing import Optional

import joblib
import numpy as np
from lightgbm import LGBMClassifier

from src.models import Direction, RegimeLabel

logger = logging.getLogger(__name__)


class LGBMExpertTrainer:
    """Trains one LGBMClassifier per market regime on labelled feature data.

    Runs offline (batch).  One instance per asset.
    """

    def __init__(self, config: dict, asset: str) -> None:
        self._cfg = config.get("lgbm", {})
        self.asset = asset
        self.experts: dict[RegimeLabel, LGBMClassifier] = {}
        self.expert_accuracy: dict[RegimeLabel, float] = {}
        self.trained_regimes: set[RegimeLabel] = set()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_label(val: object) -> RegimeLabel:
        """Normalise a RegimeLabel enum, string value, or name to RegimeLabel."""
        if isinstance(val, RegimeLabel):
            return val
        # Try matching by value first, then by name
        for member in RegimeLabel:
            if member.value == val or member.name == val:
                return member
        return RegimeLabel.UNKNOWN

    def _label_strings(self, regime_labels: np.ndarray) -> np.ndarray:
        """Return an object array of RegimeLabel members for comparison."""
        return np.array([self._to_label(v) for v in regime_labels], dtype=object)

    # ------------------------------------------------------------------
    # Training
    # ------------------------------------------------------------------

    def train_all(
        self,
        feature_matrix: np.ndarray,
        regime_labels: np.ndarray,
        feature_names: list[str],
        close_prices: np.ndarray,
    ) -> None:
        """Train one expert per regime.

        Args:
            feature_matrix:  (n, f) pre-scaled feature matrix (training window only).
            regime_labels:   (n,) array of RegimeLabel per bar (from HMM MAP/Viterbi).
            feature_names:   ordered list of feature names.
            close_prices:    (n,) raw close prices aligned to feature_matrix rows.
        """
        lookahead = int(self._cfg.get("label_lookahead_bars", 3))
        threshold = float(self._cfg.get("label_threshold_pct", 0.003))
        min_samples = int(self._cfg.get("min_samples_per_regime", 100))

        n = len(feature_matrix)

        # Build forward-looking returns (training context — lookahead is valid here)
        future_returns = np.full(n, np.nan)
        for i in range(n - lookahead):
            if close_prices[i] > 0:
                future_returns[i] = np.log(close_prices[i + lookahead] / close_prices[i])

        # Normalise regime label array to RegimeLabel objects
        labels_normalised = self._label_strings(regime_labels)
        unique_regimes: set[RegimeLabel] = set(labels_normalised)

        # LightGBM hyper-parameters from config
        clf_kwargs = dict(
            n_estimators=int(self._cfg.get("n_estimators", 200)),
            max_depth=int(self._cfg.get("max_depth", 4)),
            num_leaves=int(self._cfg.get("num_leaves", 15)),
            learning_rate=float(self._cfg.get("learning_rate", 0.05)),
            subsample=float(self._cfg.get("subsample", 0.8)),
            colsample_bytree=float(self._cfg.get("colsample_bytree", 0.8)),
            min_child_samples=int(self._cfg.get("min_child_samples", 20)),
            class_weight=self._cfg.get("class_weight", "balanced"),
            verbose=-1,
            random_state=42,
        )

        for regime in unique_regimes:
            # Select rows belonging to this regime that have valid future returns
            mask = (labels_normalised == regime) & ~np.isnan(future_returns)
            X_regime = feature_matrix[mask]
            fr_regime = future_returns[mask]

            if len(X_regime) < min_samples:
                logger.warning(
                    "[%s] Skipping regime %s: %d samples < %d required",
                    self.asset, regime.name, len(X_regime), min_samples,
                )
                continue

            # Build directional labels (+1 LONG, -1 SHORT, 0 FLAT)
            Y = np.zeros(len(X_regime), dtype=int)
            Y[fr_regime > threshold] = 1
            Y[fr_regime < -threshold] = -1

            # Drop undecided bars
            decisive = Y != 0
            X_decisive = X_regime[decisive]
            Y_decisive = Y[decisive]

            if len(X_decisive) < 2:
                logger.warning(
                    "[%s] Regime %s: only %d decisive bars — skipping",
                    self.asset, regime.name, len(X_decisive),
                )
                continue

            # Temporal 80 / 20 split (no shuffling — preserve time order)
            split = max(1, int(len(X_decisive) * 0.8))
            X_train, X_val = X_decisive[:split], X_decisive[split:]
            Y_train, Y_val = Y_decisive[:split], Y_decisive[split:]

            clf = LGBMClassifier(**clf_kwargs)
            clf.fit(X_train, Y_train)

            with warnings.catch_warnings():
                warnings.simplefilter("ignore", UserWarning)
                val_acc = float(clf.score(X_val, Y_val)) if len(X_val) > 0 else float("nan")

            self.experts[regime] = clf
            self.expert_accuracy[regime] = val_acc
            self.trained_regimes.add(regime)

            logger.info(
                "[%s] Expert [%s] trained — decisive=%d, val_acc=%.3f",
                self.asset, regime.name, len(X_decisive), val_acc,
            )

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save_all(self, model_dir: str) -> None:
        asset_key = self.asset.replace("/", "_")
        os.makedirs(model_dir, exist_ok=True)
        for regime, clf in self.experts.items():
            path = os.path.join(model_dir, f"{asset_key}_{regime.name}_lgbm.pkl")
            joblib.dump(clf, path)
            logger.info("Expert saved → %s", path)

    def load_all(self, model_dir: str, regimes: list[RegimeLabel]) -> None:
        asset_key = self.asset.replace("/", "_")
        for regime in regimes:
            path = os.path.join(model_dir, f"{asset_key}_{regime.name}_lgbm.pkl")
            if os.path.exists(path):
                self.experts[regime] = joblib.load(path)
                self.trained_regimes.add(regime)
                logger.info("[%s] Expert loaded: %s from %s", self.asset, regime.name, path)
            else:
                logger.warning(
                    "[%s] No expert for %s at %s — will use AvoidStrategy",
                    self.asset, regime.name, path,
                )


# ---------------------------------------------------------------------------

class LGBMExpertRouter:
    """Routes live feature vectors to the correct regime expert.

    Used in the live trading loop.  Expects pre-scaled features.
    """

    def __init__(self, config: dict, asset: str) -> None:
        self._cfg = config.get("lgbm", {})
        self.asset = asset
        self.experts: dict[RegimeLabel, LGBMClassifier] = {}
        self.confidence_threshold: float = float(
            config.get("lgbm", {}).get("confidence_threshold", 0.75)
        )

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load_from_trainer(self, trainer: LGBMExpertTrainer) -> None:
        self.experts = trainer.experts.copy()

    def load_from_disk(self, model_dir: str, regimes: list[RegimeLabel]) -> None:
        asset_key = self.asset.replace("/", "_")
        for regime in regimes:
            path = os.path.join(model_dir, f"{asset_key}_{regime.name}_lgbm.pkl")
            if os.path.exists(path):
                self.experts[regime] = joblib.load(path)
                logger.info("[%s] Expert loaded from disk: %s", self.asset, regime.name)

    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------

    def predict(
        self,
        regime: RegimeLabel,
        scaled_features: np.ndarray,
    ) -> tuple[Direction, float]:
        """Return (Direction, confidence) for the given regime and feature vector.

        scaled_features must already be scaled by FeatureEngineer's StandardScaler.
        Returns (FLAT, 0.0) if no expert is available for this regime.
        """
        if regime not in self.experts:
            return (Direction.FLAT, 0.0)

        expert = self.experts[regime]
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            prob = expert.predict_proba(scaled_features.reshape(1, -1))[0]
        classes = list(expert.classes_)

        p_long = float(prob[classes.index(1)]) if 1 in classes else 0.0
        p_short = float(prob[classes.index(-1)]) if -1 in classes else 0.0

        if p_long >= p_short and p_long >= self.confidence_threshold:
            return (Direction.LONG, p_long)
        if p_short > p_long and p_short >= self.confidence_threshold:
            return (Direction.SHORT, p_short)
        return (Direction.FLAT, max(p_long, p_short))

    def is_expert_available(self, regime: RegimeLabel) -> bool:
        return regime in self.experts
