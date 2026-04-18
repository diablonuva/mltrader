from __future__ import annotations

import math
from typing import List

from src.models import BarData, Direction


_HALF_KELLY_RISK = 0.005  # 0.5 % of equity risked per trade


class ATRPositionSizer:
    """ATR-based position sizer.

    Determines stop, take-profit, and share count from Average True Range so
    that every trade risks a fixed fraction of equity rather than a fixed
    percentage of notional.
    """

    def __init__(self, config: dict) -> None:
        strat = config["strategy"]
        risk = config["risk"]

        self.stop_loss_atr_mult: float = strat["stop_loss_atr_multiplier"]
        self.take_profit_atr_mult: float = strat["take_profit_atr_multiplier"]
        self.max_single_pos_pct: float = risk["max_single_position_pct"]
        self.max_leverage: float = risk["max_portfolio_leverage"]

    # ------------------------------------------------------------------
    # ATR
    # ------------------------------------------------------------------

    def compute_atr(self, recent_bars: List[BarData], period: int = 14) -> float:
        """Wilder's ATR over the last *period* bars.

        True Range = max(H-L, |H-prev_C|, |L-prev_C|).
        ATR = EMA of TR with alpha = 1/period (Wilder smoothing).
        Returns 0.0 when fewer than 2 bars are available.
        """
        if len(recent_bars) < 2:
            return 0.0

        bars = recent_bars[-period - 1:]  # one extra to compute first TR
        alpha = 1.0 / period
        atr: float | None = None

        for i in range(1, len(bars)):
            prev_close = bars[i - 1].close
            high = bars[i].high
            low = bars[i].low
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            if atr is None:
                atr = tr
            else:
                atr = alpha * tr + (1.0 - alpha) * atr

        return atr if atr is not None else 0.0

    # ------------------------------------------------------------------
    # Stop & take-profit
    # ------------------------------------------------------------------

    def compute_stop_and_tp(
        self,
        entry_price: float,
        direction: Direction,
        atr: float,
    ) -> tuple[float, float]:
        """Return (stop_price, take_profit_price) based on ATR multiples.

        LONG : stop = entry - atr*stop_mult,  tp = entry + atr*tp_mult
        SHORT: stop = entry + atr*stop_mult,  tp = entry - atr*tp_mult
        """
        stop_dist = atr * self.stop_loss_atr_mult
        tp_dist = atr * self.take_profit_atr_mult

        if direction is Direction.LONG:
            return (entry_price - stop_dist, entry_price + tp_dist)
        else:  # SHORT
            return (entry_price + stop_dist, entry_price - tp_dist)

    # ------------------------------------------------------------------
    # Position size
    # ------------------------------------------------------------------

    def compute_size(
        self,
        equity: float,
        entry_price: float,
        stop_price: float,
        max_size_pct: float,
    ) -> tuple[float, float]:
        """Return (size_pct, shares).

        Half-Kelly risk: risk 0.5 % of equity per trade.
        Dollar risk / risk_per_share gives the raw share count.
        The position is then capped at max(max_size_pct, max_single_pos_pct)
        of equity.  Returns the actual size_pct used.

        Falls back to a flat max_size_pct allocation when the stop distance
        is zero (e.g. ATR is unavailable).
        """
        risk_per_share = abs(entry_price - stop_price)
        cap_pct = max(max_size_pct, self.max_single_pos_pct)
        max_notional = equity * cap_pct

        if risk_per_share == 0.0 or entry_price == 0.0:
            # Fallback: flat percentage allocation
            shares = math.floor(max_notional / entry_price) if entry_price > 0 else 0
            size_pct = (shares * entry_price) / equity if equity > 0 else 0.0
            return (size_pct, float(shares))

        dollar_risk = equity * _HALF_KELLY_RISK
        shares = math.floor(dollar_risk / risk_per_share)

        # Apply notional cap
        if shares * entry_price > max_notional:
            shares = math.floor(max_notional / entry_price)

        shares = max(shares, 0)
        size_pct = (shares * entry_price) / equity if equity > 0 else 0.0
        return (size_pct, float(shares))

    # ------------------------------------------------------------------
    # Phase 1 — ADX (Average Directional Index)
    # ------------------------------------------------------------------

    def compute_adx(self, recent_bars: List[BarData], period: int = 14) -> float:
        """Wilder's ADX over the last *period* bars.

        Returns 0.0 when fewer than 2×period bars are available.
        ADX > 25 = directional trend has real force; < 20 = choppy/ranging.
        """
        if len(recent_bars) < period * 2:
            return 0.0

        bars = recent_bars[-(period * 2 + 2):]
        alpha = 1.0 / period

        smooth_tr: float | None = None
        smooth_plus_dm: float | None = None
        smooth_minus_dm: float | None = None
        adx: float | None = None

        for i in range(1, len(bars)):
            h, l = bars[i].high, bars[i].low
            ph, pl, pc = bars[i - 1].high, bars[i - 1].low, bars[i - 1].close

            tr = max(h - l, abs(h - pc), abs(l - pc))
            up_move = h - ph
            down_move = pl - l
            plus_dm = up_move if (up_move > down_move and up_move > 0) else 0.0
            minus_dm = down_move if (down_move > up_move and down_move > 0) else 0.0

            if smooth_tr is None:
                smooth_tr = tr
                smooth_plus_dm = plus_dm
                smooth_minus_dm = minus_dm
            else:
                smooth_tr = alpha * tr + (1.0 - alpha) * smooth_tr
                smooth_plus_dm = alpha * plus_dm + (1.0 - alpha) * smooth_plus_dm
                smooth_minus_dm = alpha * minus_dm + (1.0 - alpha) * smooth_minus_dm

            if smooth_tr and smooth_tr > 0:
                plus_di = 100.0 * smooth_plus_dm / smooth_tr
                minus_di = 100.0 * smooth_minus_dm / smooth_tr
                di_sum = plus_di + minus_di
                dx = 100.0 * abs(plus_di - minus_di) / di_sum if di_sum > 0 else 0.0
            else:
                dx = 0.0

            adx = alpha * dx + (1.0 - alpha) * adx if adx is not None else dx

        return adx if adx is not None else 0.0

    # ------------------------------------------------------------------
    # Phase 2 — EMA helpers
    # ------------------------------------------------------------------

    def compute_ema(self, recent_bars: List[BarData], period: int) -> float:
        """Exponential Moving Average of closes over *period* bars.

        Uses standard EMA alpha = 2/(period+1).  Returns the last bar's close
        when insufficient history is available.
        """
        if not recent_bars:
            return 0.0
        if len(recent_bars) < period:
            return recent_bars[-1].close

        # Use enough history to warm up the EMA (3× period)
        closes = [b.close for b in recent_bars[-(period * 3):]]
        alpha = 2.0 / (period + 1)
        ema = closes[0]
        for c in closes[1:]:
            ema = alpha * c + (1.0 - alpha) * ema
        return ema

    def compute_ema_slope_bullish(
        self,
        recent_bars: List[BarData],
        fast: int = 9,
        slow: int = 20,
        lookback: int = 2,
    ) -> bool:
        """Return True when fast EMA > slow EMA AND fast EMA is rising.

        "Rising" = fast EMA now > fast EMA *lookback* bars ago.
        Requires at least slow + lookback + 1 bars.
        """
        if len(recent_bars) < slow + lookback + 1:
            return False

        ema_fast_now = self.compute_ema(recent_bars, fast)
        ema_slow_now = self.compute_ema(recent_bars, slow)
        if ema_fast_now <= ema_slow_now:
            return False

        ema_fast_prev = self.compute_ema(recent_bars[:-lookback], fast)
        return ema_fast_now > ema_fast_prev

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    def compute_full(
        self,
        equity: float,
        entry_price: float,
        direction: Direction,
        max_size_pct: float,
        recent_bars: List[BarData],
    ) -> dict:
        """One-shot computation returning all sizing fields.

        Returns:
            {
                "atr": float,
                "stop_distance": float,
                "stop_price": float,
                "take_profit_price": float,
                "size_pct": float,
                "shares": float,
            }
        """
        atr = self.compute_atr(recent_bars)
        stop_price, take_profit_price = self.compute_stop_and_tp(
            entry_price, direction, atr
        )
        size_pct, shares = self.compute_size(
            equity, entry_price, stop_price, max_size_pct
        )
        return {
            "atr": atr,
            "stop_distance": abs(entry_price - stop_price),
            "stop_price": stop_price,
            "take_profit_price": take_profit_price,
            "size_pct": size_pct,
            "shares": shares,
        }
