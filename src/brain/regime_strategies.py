from __future__ import annotations

import logging
from collections import deque
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from src.allocation.position_sizer import ATRPositionSizer
from src.brain.opening_range import OpeningRange
from src.models import (
    AssetClass,
    BarData,
    Direction,
    RegimeLabel,
    Signal,
)
from src.session.session_manager import SessionManager

if TYPE_CHECKING:
    from src.models import PortfolioState

logger = logging.getLogger(__name__)

# Regimes that map to each hold-bars bucket
_MOMENTUM_REGIMES = {RegimeLabel.TRENDING_UP, RegimeLabel.TRENDING_DOWN}
_BREAKOUT_REGIMES = {RegimeLabel.BREAKOUT}
_MEAN_REVERSION_REGIMES = {RegimeLabel.CHOPPY}


class StrategyOrchestrator:
    """Wires HMM, LGBM, session, sizer, PDT-guard, and circuit-breaker into a
    single evaluate-and-signal pipeline.

    ``select_strategy`` is the main entry point.  It runs pre-flight checks,
    applies regime-specific guard logic, then constructs a ``Signal`` via the
    ATR sizer.
    """

    def __init__(
        self,
        config: dict,
        session_manager: SessionManager,
        position_sizer: ATRPositionSizer,
        pdt_guard: object,          # PDTGuard — imported lazily to avoid cycles
        circuit_breaker: object,    # CircuitBreaker — same
    ) -> None:
        strat = config["strategy"]
        self.allow_short: bool = strat["allow_short"]
        self.hmm_confidence_threshold: float = config["hmm"]["confidence_threshold"]
        self.lgbm_confidence_threshold: float = config["lgbm"]["confidence_threshold"]

        # Phase 1 — trailing stop + ADX gate
        self.trailing_stop_enabled: bool = strat.get("trailing_stop_enabled", False)
        self.adx_trending_min: float = float(strat.get("adx_trending_min", 0))

        # Phase 2 — EMA slope + ORB+VWAP breakout filter
        self.ema_slope_filter: bool = strat.get("ema_slope_filter", False)
        self.breakout_orb_vwap_filter: bool = strat.get("breakout_orb_vwap_filter", False)

        # Phase 3 — partial take-profit (v2: wider TP1 target + BE buffer)
        self.partial_tp_enabled: bool = strat.get("partial_tp_enabled", False)
        self.tp1_atr_multiplier: float = float(strat.get("tp1_atr_multiplier", 1.0))
        self.tp1_size_pct: float = float(strat.get("tp1_size_pct", 0.50))
        self.tp1_be_buffer_atr: float = float(strat.get("tp1_be_buffer_atr", 0.25))

        self._config = config
        self._session_manager = session_manager
        self._position_sizer = position_sizer
        self._pdt_guard = pdt_guard
        self._circuit_breaker = circuit_breaker

        self._signal_history: deque = deque(maxlen=20)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def select_strategy(
        self,
        regime: RegimeLabel,
        hmm_confidence: float,
        hmm_is_flickering: bool,
        hmm_is_confirmed: bool,
        lgbm_direction: Direction,
        lgbm_confidence: float,
        feature_dict: dict,
        asset: str,
        asset_class: AssetClass,
        current_bar: BarData,
        recent_bars: list[BarData],
        vwap: float,
        or_tracker: OpeningRange | None,
        bars_since_open: int,
        portfolio_state: PortfolioState,
        now: datetime,
    ) -> Signal | None:
        """Evaluate all guards and return a Signal, or None with a logged reason."""

        # ------------------------------------------------------------------ #
        # PRE-FLIGHT CHECKS                                                   #
        # ------------------------------------------------------------------ #

        if regime in (RegimeLabel.SQUEEZE, RegimeLabel.UNKNOWN):
            self._skip("AVOID_REGIME_WAIT", asset, regime)
            return None

        if hmm_confidence < self.hmm_confidence_threshold:
            self._skip("AVOID_LOW_HMM_CONFIDENCE", asset, regime,
                       f"conf={hmm_confidence:.3f} < {self.hmm_confidence_threshold}")
            return None

        if not hmm_is_confirmed:
            self._skip("AVOID_HMM_NOT_CONFIRMED", asset, regime)
            return None

        if hmm_is_flickering and regime in (
            RegimeLabel.TRENDING_UP, RegimeLabel.TRENDING_DOWN, RegimeLabel.BREAKOUT
        ):
            self._skip("AVOID_FLICKERING", asset, regime)
            return None

        if lgbm_direction is Direction.FLAT:
            self._skip("AVOID_LGBM_FLAT", asset, regime)
            return None

        if lgbm_confidence < self.lgbm_confidence_threshold:
            self._skip("AVOID_LOW_LGBM_CONFIDENCE", asset, regime,
                       f"conf={lgbm_confidence:.3f} < {self.lgbm_confidence_threshold}")
            return None

        if lgbm_direction is Direction.SHORT and not self.allow_short:
            self._skip("AVOID_SHORT_DISABLED", asset, regime)
            return None

        entry_allowed, session_reason = self._session_manager.is_entry_allowed(asset, now)
        if not entry_allowed:
            self._skip(f"AVOID_{session_reason}", asset, regime)
            return None

        if not self._pdt_guard.can_trade(asset, portfolio_state.equity):
            self._skip("AVOID_PDT", asset, regime)
            return None

        if self._circuit_breaker.is_active():
            self._skip("AVOID_CIRCUIT_BREAKER", asset, regime)
            return None

        # ------------------------------------------------------------------ #
        # PHASE 1 — ADX GATE (TRENDING_UP only)                               #
        # ------------------------------------------------------------------ #

        if (
            self.adx_trending_min > 0
            and regime is RegimeLabel.TRENDING_UP
            and len(recent_bars) >= 28
        ):
            adx = self._position_sizer.compute_adx(recent_bars)
            if adx < self.adx_trending_min:
                self._skip(
                    "AVOID_ADX_TOO_LOW", asset, regime,
                    f"adx={adx:.1f} < {self.adx_trending_min}"
                )
                return None

        # ------------------------------------------------------------------ #
        # PHASE 2 — EMA SLOPE GATE (TRENDING_UP only)                         #
        # ------------------------------------------------------------------ #

        if self.ema_slope_filter and regime is RegimeLabel.TRENDING_UP:
            if not self._position_sizer.compute_ema_slope_bullish(recent_bars):
                self._skip("AVOID_EMA_SLOPE_BEARISH", asset, regime)
                return None

        # ------------------------------------------------------------------ #
        # REGIME-SPECIFIC GUARD LAYER                                         #
        # ------------------------------------------------------------------ #

        if regime in _MOMENTUM_REGIMES:
            if not self._momentum_guard(lgbm_direction, feature_dict):
                self._skip("AVOID_MOMENTUM_GUARD", asset, regime,
                           str({k: feature_dict.get(k) for k in
                                ("vwap_deviation_pct", "volume_ratio", "bar_body_ratio")}))
                return None

        elif regime in _BREAKOUT_REGIMES:
            if not self._breakout_guard(feature_dict, vwap, or_tracker, current_bar):
                self._skip("AVOID_BREAKOUT_GUARD", asset, regime,
                           f"volume_ratio={feature_dict.get('volume_ratio')}")
                return None

        elif regime in _MEAN_REVERSION_REGIMES:
            if not self._mean_reversion_guard(lgbm_direction, feature_dict):
                self._skip("AVOID_MEAN_REVERSION_GUARD", asset, regime,
                           str({k: feature_dict.get(k) for k in
                                ("vwap_deviation_pct", "volume_ratio", "bar_body_ratio")}))
                return None

        # ------------------------------------------------------------------ #
        # SIGNAL CONSTRUCTION                                                 #
        # ------------------------------------------------------------------ #

        regime_cfg = self._config["strategy"]["regime_allocations"][regime.name]
        regime_alloc: float = regime_cfg["allocation"]

        sizing = self._position_sizer.compute_full(
            equity=portfolio_state.equity,
            entry_price=current_bar.close,
            direction=lgbm_direction,
            max_size_pct=regime_alloc,
            recent_bars=recent_bars,
        )

        size_pct = min(sizing["size_pct"], regime_alloc)
        stop_price: float = sizing["stop_price"]
        take_profit_price: float = sizing["take_profit_price"]
        atr: float = sizing["atr"]

        max_hold_bars = self._max_hold_bars(regime)

        # Phase 3 — compute TP1 level
        tp1_price = 0.0
        tp1_shares_pct = 0.0
        if self.partial_tp_enabled and atr > 0:
            tp1_dist = atr * self.tp1_atr_multiplier
            if lgbm_direction is Direction.LONG:
                tp1_price = current_bar.close + tp1_dist
            else:
                tp1_price = current_bar.close - tp1_dist
            tp1_shares_pct = self.tp1_size_pct

        signal = Signal(
            asset=asset,
            direction=lgbm_direction,
            size_pct=size_pct,
            entry_price=current_bar.close,
            stop_price=stop_price,
            take_profit_price=take_profit_price,
            max_hold_bars=max_hold_bars,
            strategy_name=f"HMM_{regime.name}+LGBM",
            regime=regime,
            hmm_confidence=hmm_confidence,
            lgbm_confidence=lgbm_confidence,
            timestamp=current_bar.timestamp,
            asset_class=asset_class,
            atr_at_entry=atr,
            tp1_price=tp1_price,
            tp1_shares_pct=tp1_shares_pct,
        )

        self.record_signal(signal)
        return signal

    def record_signal(self, signal: Signal | None, reason: str = "") -> None:
        """Append signal (or None) with a reason tag to the rolling history."""
        self._signal_history.append(
            {"signal": signal, "reason": reason, "ts": datetime.now(timezone.utc).replace(tzinfo=None)}
        )

    def get_signal_history(self) -> list:
        """Return a snapshot of the rolling signal history (up to 20 entries)."""
        return list(self._signal_history)

    # ------------------------------------------------------------------
    # Regime guards (private)
    # ------------------------------------------------------------------

    def _momentum_guard(self, direction: Direction, fd: dict) -> bool:
        feat = self._config["features"]
        vol_min: float = feat["volume_ratio_momentum_min"]
        body_min: float = feat["bar_body_ratio_trend_min"]

        vwap_dev: float = fd.get("vwap_deviation_pct", 0.0)
        volume_ratio: float = fd.get("volume_ratio", 0.0)
        bar_body: float = fd.get("bar_body_ratio", 0.0)

        if direction is Direction.LONG and vwap_dev <= 0.0:
            return False
        if direction is Direction.SHORT and vwap_dev >= 0.0:
            return False
        if volume_ratio < vol_min:
            return False
        if bar_body < body_min:
            return False
        return True

    def _breakout_guard(
        self,
        fd: dict,
        vwap: float = 0.0,
        or_tracker: OpeningRange | None = None,
        current_bar: BarData | None = None,
    ) -> bool:
        vol_min: float = self._config["features"]["volume_ratio_breakout_min"]
        if fd.get("volume_ratio", 0.0) < vol_min:
            return False

        # Phase 2 — ORB + VWAP conjunction filter
        if self.breakout_orb_vwap_filter and current_bar is not None:
            price = current_bar.close

            # Price must be above VWAP (institutional long bias)
            if vwap > 0 and price <= vwap:
                return False

            # Price must be above the opening-range high (breakout has committed)
            if or_tracker is not None:
                or_high = or_tracker.get_or_high()
                if or_high is not None and or_high > 0 and price <= or_high:
                    return False

        return True

    def _mean_reversion_guard(self, direction: Direction, fd: dict) -> bool:
        vwap_dev: float = fd.get("vwap_deviation_pct", 0.0)
        volume_ratio: float = fd.get("volume_ratio", 1.0)
        bar_body: float = fd.get("bar_body_ratio", 1.0)

        vol_ok = volume_ratio < 0.8
        body_ok = bar_body < 0.4

        if direction is Direction.LONG:
            return vwap_dev < -0.8 and vol_ok and body_ok
        if direction is Direction.SHORT:
            return vwap_dev > 0.8 and vol_ok and body_ok
        return False

    # ------------------------------------------------------------------
    # Helpers (private)
    # ------------------------------------------------------------------

    def _max_hold_bars(self, regime: RegimeLabel) -> int:
        hold_cfg = self._config["strategy"]["max_hold_bars"]
        if regime in _MOMENTUM_REGIMES:
            return hold_cfg["momentum"]
        if regime in _BREAKOUT_REGIMES:
            return hold_cfg["breakout"]
        if regime in _MEAN_REVERSION_REGIMES:
            return hold_cfg["mean_reversion"]
        return hold_cfg["momentum"]  # safe default

    def _skip(self, reason: str, asset: str, regime: RegimeLabel,
              detail: str = "") -> None:
        msg = f"[{asset}] {reason} (regime={regime.name})"
        if detail:
            msg += f" — {detail}"
        logger.debug(msg)
        self.record_signal(None, reason)
