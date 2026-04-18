from __future__ import annotations

import logging
import math
import warnings
from collections import deque
from datetime import datetime
from typing import Optional

import joblib
import numpy as np
from hmmlearn.hmm import GaussianHMM
from scipy.special import logsumexp
from scipy.stats import multivariate_normal
from sklearn.exceptions import ConvergenceWarning
from sklearn.preprocessing import StandardScaler

from src.models import AssetClass, RegimeLabel

logger = logging.getLogger(__name__)


class HMMEngine:
    """Trains a Gaussian HMM on intraday feature vectors.

    Automatic state-count selection via BIC over candidates [3, 4, 5, 6, 7].
    One instance per asset.
    """

    def __init__(self, config: dict, asset: str) -> None:
        self._cfg = config.get("hmm", {})
        self.asset = asset
        self.asset_class = AssetClass.from_symbol(asset)

        # Model parameters (set after training)
        self.model: Optional[GaussianHMM] = None
        self.n_states: Optional[int] = None
        self.state_labels: list[RegimeLabel] = []
        self.scaler: Optional[StandardScaler] = None
        self.training_end_timestamp: Optional[datetime] = None
        self.training_bars: int = 0
        self.is_trained: bool = False

        # Online inference state
        flicker_window = int(self._cfg.get("flicker_window_bars", 20))
        self._alpha_current: Optional[np.ndarray] = None
        self._regime_history: deque = deque(maxlen=flicker_window)
        self._consecutive_same: int = 0
        self._current_regime: RegimeLabel = RegimeLabel.UNKNOWN

    # ------------------------------------------------------------------
    # Private helpers — training
    # ------------------------------------------------------------------

    def _compute_bic(self, model: GaussianHMM, X: np.ndarray) -> float:
        """BIC = -2 × log-likelihood + n_params × log(n_samples)."""
        n = model.n_components
        f = X.shape[1]
        n_means = n * f
        n_covars = n * f * (f + 1) // 2   # full covariance: lower triangle
        n_start = n - 1
        n_trans = n * (n - 1)
        n_params = n_means + n_covars + n_start + n_trans

        log_likelihood = model.score(X) * len(X)
        bic = -2.0 * log_likelihood + n_params * math.log(len(X))
        return bic

    def _sort_states_by_volatility(
        self,
        model: GaussianHMM,
        feature_names: list[str],
    ) -> list[int]:
        """Return state indices sorted ascending by realized volatility mean."""
        try:
            vol_idx = feature_names.index("realized_vol_20bar")
        except ValueError:
            vol_idx = 0
        vol_means = model.means_[:, vol_idx]
        return list(np.argsort(vol_means))

    def _assign_labels(
        self,
        sorted_indices: list[int],
        n_states: int,
    ) -> list[RegimeLabel]:
        """Map sorted volatility positions → RegimeLabel via from_index."""
        labels: list[RegimeLabel] = [RegimeLabel.UNKNOWN] * n_states
        for rank, state_idx in enumerate(sorted_indices):
            labels[state_idx] = RegimeLabel.from_index(rank, n_states)
        return labels

    # ------------------------------------------------------------------
    # Private helpers — inference
    # ------------------------------------------------------------------

    def _emission_log_prob(self, x: np.ndarray, state: int) -> float:
        """Log-probability of observation x under state's Gaussian emission."""
        return float(
            multivariate_normal.logpdf(
                x,
                mean=self.model.means_[state],
                cov=self.model.covars_[state],
                allow_singular=True,
            )
        )

    def _clip_outliers(self, X: np.ndarray) -> np.ndarray:
        """Clip X to scaler.mean_ ± 5 × scaler.scale_ (original feature space)."""
        lo = self.scaler.mean_ - 5.0 * self.scaler.scale_
        hi = self.scaler.mean_ + 5.0 * self.scaler.scale_
        return np.clip(X, lo, hi)

    # ------------------------------------------------------------------
    # Public — training
    # ------------------------------------------------------------------

    def train(
        self,
        feature_matrix: np.ndarray,
        feature_names: list[str],
        end_timestamp: datetime,
    ) -> None:
        """Select best n_states by BIC, refit, label, and store model."""
        min_bars = int(self._cfg.get("min_train_bars", 1000))
        if len(feature_matrix) < min_bars:
            raise ValueError(
                f"[{self.asset}] Insufficient training bars: "
                f"{len(feature_matrix)} < {min_bars} required."
            )

        # Clip and scale
        stds = np.std(feature_matrix, axis=0, ddof=1)
        means = np.mean(feature_matrix, axis=0)
        clipped = np.clip(feature_matrix, means - 5.0 * stds, means + 5.0 * stds)
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(clipped)

        # BIC search
        candidates: list[int] = list(self._cfg.get("n_components_candidates", [3, 4, 5, 6, 7]))
        n_iter = int(self._cfg.get("n_iter", 200))
        tol = float(self._cfg.get("tol", 1e-4))
        n_init = int(self._cfg.get("n_init", 10))
        cov_type: str = self._cfg.get("covariance_type", "full")

        best_bic = math.inf
        best_n = candidates[0]
        best_model: Optional[GaussianHMM] = None

        for n in candidates:
            # Manual multi-restart: hmmlearn ≥0.3 removed the n_init kwarg
            candidate_model: Optional[GaussianHMM] = None
            candidate_score = -math.inf
            for seed in range(n_init):
                try:
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore", ConvergenceWarning)
                        hmm = GaussianHMM(
                            n_components=n,
                            covariance_type=cov_type,
                            n_iter=n_iter,
                            tol=tol,
                            init_params="stmc",
                            random_state=seed,
                        )
                        hmm.fit(X_scaled)
                    score = hmm.score(X_scaled)
                    if score > candidate_score:
                        candidate_score = score
                        candidate_model = hmm
                except Exception as exc:  # noqa: BLE001
                    logger.debug("[%s] n=%d seed=%d failed: %s", self.asset, n, seed, exc)
                    continue

            if candidate_model is None:
                logger.warning("[%s] HMM n=%d: all seeds failed", self.asset, n)
                continue

            bic = self._compute_bic(candidate_model, X_scaled)
            logger.debug("[%s] n=%d  BIC=%.1f", self.asset, n, bic)
            if bic < best_bic:
                best_bic = bic
                best_n = n
                best_model = candidate_model

        if best_model is None:
            raise RuntimeError(f"[{self.asset}] All HMM candidates failed to fit.")

        # Refit winner with a fixed seed for reproducibility
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", ConvergenceWarning)
            final_model = GaussianHMM(
                n_components=best_n,
                covariance_type=cov_type,
                n_iter=n_iter,
                tol=tol,
                init_params="stmc",
                random_state=0,
            )
            final_model.fit(X_scaled)

        # Label states
        sorted_idx = self._sort_states_by_volatility(final_model, feature_names)
        labels = self._assign_labels(sorted_idx, best_n)

        # Commit
        self.model = final_model
        self.n_states = best_n
        self.state_labels = labels
        self.scaler = scaler
        self.training_end_timestamp = end_timestamp
        self.training_bars = len(feature_matrix)
        self.is_trained = True

        logger.info(
            "HMM trained [%s]: %d states, BIC=%.1f, %d bars, ending %s",
            self.asset,
            self.n_states,
            best_bic,
            self.training_bars,
            self.training_end_timestamp,
        )

    # ------------------------------------------------------------------
    # Public — batch inference (causal forward algorithm)
    # ------------------------------------------------------------------

    def predict_regime_filtered(self, feature_matrix: np.ndarray) -> np.ndarray:
        """Batch forward algorithm — CAUSAL ONLY.

        Returns alpha array of shape (T, n_states) where each row sums to 1.
        alpha[t] depends only on observations x[0..t] — no look-ahead.
        """
        T, _ = feature_matrix.shape
        n = self.model.n_components

        X = self.scaler.transform(self._clip_outliers(feature_matrix))

        log_startprob = np.log(self.model.startprob_ + 1e-300)
        log_transmat = np.log(self.model.transmat_ + 1e-300)

        alpha = np.zeros((T, n))

        # t = 0: initialise from start distribution
        log_em_0 = np.array([self._emission_log_prob(X[0], j) for j in range(n)])
        log_alpha = log_startprob + log_em_0
        log_alpha -= logsumexp(log_alpha)
        alpha[0] = np.exp(log_alpha)

        # t = 1..T-1: causal forward recursion
        for t in range(1, T):
            log_em = np.array([self._emission_log_prob(X[t], j) for j in range(n)])
            log_alpha_new = np.empty(n)
            for j in range(n):
                log_alpha_new[j] = logsumexp(log_alpha + log_transmat[:, j]) + log_em[j]
            log_alpha_new -= logsumexp(log_alpha_new)
            alpha[t] = np.exp(log_alpha_new)
            log_alpha = log_alpha_new

        return alpha

    # ------------------------------------------------------------------
    # Public — online single-step inference
    # ------------------------------------------------------------------

    def update_regime_online(
        self,
        alpha_prev: np.ndarray,
        new_observation: np.ndarray,
    ) -> np.ndarray:
        """Single-step forward update given previous alpha and a raw observation.

        new_observation is raw (unscaled). Returns new alpha (n_states,).
        """
        n = self.model.n_components
        x_scaled = self.scaler.transform(
            self._clip_outliers(new_observation.reshape(1, -1))
        )[0]

        log_transmat = np.log(self.model.transmat_ + 1e-300)
        log_alpha_prev = np.log(alpha_prev + 1e-300)

        log_em = np.array([self._emission_log_prob(x_scaled, j) for j in range(n)])
        log_alpha_new = np.empty(n)
        for j in range(n):
            log_alpha_new[j] = logsumexp(log_alpha_prev + log_transmat[:, j]) + log_em[j]
        log_alpha_new -= logsumexp(log_alpha_new)
        return np.exp(log_alpha_new)

    # ------------------------------------------------------------------
    # Public — stateful online stepping
    # ------------------------------------------------------------------

    def step(self, raw_observation: np.ndarray) -> None:
        """Advance online inference by one bar (raw, unscaled observation)."""
        if self._alpha_current is None:
            # Cold start: use the forward algorithm on this single observation
            self._alpha_current = self.predict_regime_filtered(
                raw_observation.reshape(1, -1)
            )[0]
        else:
            self._alpha_current = self.update_regime_online(
                self._alpha_current, raw_observation
            )

        new_regime = self.state_labels[int(np.argmax(self._alpha_current))]

        if new_regime == self._current_regime:
            self._consecutive_same += 1
        else:
            self._consecutive_same = 1
            self._current_regime = new_regime

        self._regime_history.append(new_regime)

    # ------------------------------------------------------------------
    # Accessors
    # ------------------------------------------------------------------

    def get_regime_probability(self) -> np.ndarray:
        """Copy of current alpha vector (n_states,), or zeros if not started."""
        if self._alpha_current is None:
            return np.zeros(self.n_states or 1)
        return self._alpha_current.copy()

    def get_confidence(self) -> float:
        """Max probability across states, or 0.0 if not started."""
        if self._alpha_current is None:
            return 0.0
        return float(np.max(self._alpha_current))

    def get_current_regime(self) -> RegimeLabel:
        return self._current_regime

    def get_stability_bars(self) -> int:
        """Number of consecutive bars the current regime has held."""
        return self._consecutive_same

    def get_transition_matrix(self) -> np.ndarray:
        return self.model.transmat_.copy()

    def get_expected_next_regime(self) -> RegimeLabel:
        """Most likely next state given the current MAP state."""
        if self._alpha_current is None or not self.state_labels:
            return RegimeLabel.UNKNOWN
        current_state_idx = int(np.argmax(self._alpha_current))
        next_state_idx = int(np.argmax(self.model.transmat_[current_state_idx]))
        return self.state_labels[next_state_idx]

    def is_flickering(self) -> bool:
        """True if regime changed more than flicker_rate_threshold times
        in the last flicker_window_bars bars."""
        history = list(self._regime_history)
        if len(history) < 2:
            return False
        changes = sum(1 for i in range(1, len(history)) if history[i] != history[i - 1])
        threshold = float(self._cfg.get("flicker_rate_threshold", 2.0))
        return changes > threshold

    def is_confirmed(self) -> bool:
        """True if regime has held for at least stability_confirm_bars bars."""
        confirm = int(self._cfg.get("stability_confirm_bars", 3))
        return self._consecutive_same >= confirm

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: str) -> None:
        payload = {
            "asset": self.asset,
            "asset_class": self.asset_class,
            "model": self.model,
            "n_states": self.n_states,
            "state_labels": self.state_labels,
            "scaler": self.scaler,
            "training_end_timestamp": self.training_end_timestamp,
            "training_bars": self.training_bars,
            "is_trained": self.is_trained,
        }
        joblib.dump(payload, path)
        logger.info("HMM saved [%s] → %s", self.asset, path)

    def load(self, path: str) -> None:
        payload: dict = joblib.load(path)
        self.asset = payload["asset"]
        self.asset_class = payload["asset_class"]
        self.model = payload["model"]
        self.n_states = payload["n_states"]
        self.state_labels = payload["state_labels"]
        self.scaler = payload["scaler"]
        self.training_end_timestamp = payload["training_end_timestamp"]
        self.training_bars = payload["training_bars"]
        self.is_trained = True
        logger.info("HMM loaded [%s] from %s", self.asset, path)

    def get_model_info(self) -> dict:
        return {
            "asset": self.asset,
            "n_states": self.n_states,
            "state_labels": [lbl.value for lbl in self.state_labels],
            "training_bars": self.training_bars,
            "training_end_timestamp": self.training_end_timestamp,
            "is_trained": self.is_trained,
        }
