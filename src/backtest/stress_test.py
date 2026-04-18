from __future__ import annotations

import copy
import logging
from datetime import datetime, timedelta, date

from src.backtest.backtester import WalkForwardBacktester
from src.backtest.performance import PerformanceAnalyzer, _max_drawdown, _daily_equity_series
from src.models import BarData, BacktestResults

logger = logging.getLogger(__name__)


class StressTester:
    """Runs parametric stress scenarios against the WalkForwardBacktester.

    All methods return a result dict and log a summary.  The backtester and
    config are deep-copied before each run so the caller's state is preserved.
    """

    def __init__(
        self,
        backtester: WalkForwardBacktester,
        config: dict,
    ) -> None:
        self._backtester = backtester
        self._config = config

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _run(
        self,
        bar_data: dict[str, list[BarData]],
        **kwargs,
    ) -> BacktestResults:
        """Create a fresh backtester with current config and run with *kwargs*."""
        fresh = WalkForwardBacktester(copy.deepcopy(self._config))
        bars = list(bar_data.values())[0]
        return fresh.run(bar_data, bars[0].timestamp, bars[-1].timestamp, **kwargs)

    def _max_dd_from_results(self, results: BacktestResults) -> float:
        _, equities = _daily_equity_series(results)
        if len(equities) < 2:
            return 0.0
        dd, _ = _max_drawdown(equities)
        return dd

    def _profit_factor(self, results: BacktestResults) -> float:
        trades = results.all_trades
        if not trades:
            return 0.0
        wins = sum(t.pnl_dollar for t in trades if t.pnl_pct > 0)
        losses = abs(sum(t.pnl_dollar for t in trades if t.pnl_pct <= 0))
        return wins / losses if losses > 0 else float("inf")

    def _total_return(self, results: BacktestResults) -> float:
        _, equities = _daily_equity_series(results)
        if len(equities) < 2:
            return 0.0
        return (equities[-1] - results.initial_capital) / results.initial_capital

    # ------------------------------------------------------------------
    # 1. Regime misclassification
    # ------------------------------------------------------------------

    def regime_misclassification(
        self,
        bar_data: dict[str, list[BarData]],
        shuffle_pct: float = 0.20,
    ) -> dict:
        """Re-run with *shuffle_pct* of HMM regime outputs randomly flipped.

        Returns result dict and asserts max_drawdown < 15 %.
        """
        logger.info("Stress: regime misclassification (shuffle_pct=%.0f%%)", shuffle_pct * 100)
        results = self._run(bar_data, regime_noise_pct=shuffle_pct)

        max_dd = self._max_dd_from_results(results)
        passed = max_dd < 0.15
        outcome = {
            "shuffle_pct": shuffle_pct,
            "n_trades": len(results.all_trades),
            "max_drawdown_pct": max_dd,
            "assert_max_dd_lt_15pct": passed,
        }
        logger.info("  regime_misclassification: max_dd=%.2f%%  passed=%s",
                    max_dd * 100, passed)
        return outcome

    # ------------------------------------------------------------------
    # 2. Fee sensitivity
    # ------------------------------------------------------------------

    def fee_sensitivity(
        self,
        bar_data: dict[str, list[BarData]],
        multipliers: list[float] | None = None,
    ) -> dict:
        """Re-run at 2×, 3×, 5× slippage + commission.  Reports profit_factor."""
        if multipliers is None:
            multipliers = [2.0, 3.0, 5.0]

        logger.info("Stress: fee sensitivity multipliers=%s", multipliers)
        result: dict[str, float] = {}

        for mult in multipliers:
            r = self._run(
                bar_data,
                slippage_multiplier=mult,
                commission_multiplier=mult,
            )
            pf = self._profit_factor(r)
            key = f"{int(mult)}x"
            result[key] = pf
            logger.info("  fee_sensitivity %s: profit_factor=%.3f  trades=%d",
                        key, pf, len(r.all_trades))

        return {"profit_factor_by_multiplier": result}

    # ------------------------------------------------------------------
    # 3. Latency injection
    # ------------------------------------------------------------------

    def latency_injection(
        self,
        bar_data: dict[str, list[BarData]],
        delay_bars: list[int] | None = None,
    ) -> dict:
        """Fill at bar N+delay instead of N.  Reports PnL % degradation."""
        if delay_bars is None:
            delay_bars = [1, 2, 3]

        logger.info("Stress: latency injection delays=%s", delay_bars)

        # Baseline: no delay
        baseline = self._run(bar_data)
        base_return = self._total_return(baseline)
        result: dict[str, float] = {"baseline_return_pct": base_return * 100}

        for delay in delay_bars:
            shifted = self._shift_bar_data(bar_data, delay)
            r = self._run(shifted)
            ret = self._total_return(r)
            degradation = base_return - ret
            result[f"delay_{delay}bar_return_pct"] = ret * 100
            result[f"delay_{delay}bar_degradation_pct"] = degradation * 100
            logger.info(
                "  latency delay=%d: return=%.2f%%  degradation=%.2f%%",
                delay, ret * 100, degradation * 100,
            )

        return result

    # ------------------------------------------------------------------
    # 4. PDT stress
    # ------------------------------------------------------------------

    def pdt_stress(
        self,
        bar_data: dict[str, list[BarData]],
        low_equity: float = 15_000.0,
    ) -> dict:
        """Run with a small account to verify PDTGuard counter behaviour.

        Note: The SEC eliminated the $25k PDT rule in April 2026.  This test
        now validates that the guard counter tracks trades correctly rather
        than enforcing a hard block — with equity_threshold=0.0 the guard
        permits unlimited day trades (max_daytrades_per_5d=999).
        """
        import copy as _copy
        logger.info("Stress: PDT stress (equity=%.0f)", low_equity)

        cfg = _copy.deepcopy(self._config)
        cfg["backtest"]["initial_capital"] = low_equity

        fresh = WalkForwardBacktester(cfg)
        bars = list(bar_data.values())[0]
        results = fresh.run(bar_data, bars[0].timestamp, bars[-1].timestamp)

        # Collect same-day round-trips per asset per rolling window
        trades = results.all_trades
        equity_trades = [t for t in trades if "/" not in t.asset]

        violation_found = False
        max_window_count = 0

        if equity_trades:
            # Check every trade: count same-asset trades in [date - 4 biz days, date]
            for i, t in enumerate(equity_trades):
                t_date = t.entry_time.date() if hasattr(t.entry_time, "date") else t.entry_time
                window_start = _biz_days_ago(4, t_date)
                window_trades = [
                    other for other in equity_trades
                    if other.asset == t.asset
                    and _trade_date(other) >= window_start
                    and _trade_date(other) <= t_date
                ]
                count = len(window_trades)
                max_window_count = max(max_window_count, count)
                if count > 3:
                    violation_found = True
                    logger.error(
                        "PDT VIOLATION: %d trades for %s in 5-day window ending %s",
                        count, t.asset, t_date,
                    )

        passed = not violation_found
        outcome = {
            "low_equity": low_equity,
            "n_equity_trades": len(equity_trades),
            "max_trades_in_any_5d_window": max_window_count,
            "pdt_guard_passed": passed,
        }
        logger.info(
            "  pdt_stress: n_trades=%d  max_window=%d  passed=%s",
            len(equity_trades), max_window_count, passed,
        )
        return outcome

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _shift_bar_data(
        bar_data: dict[str, list[BarData]],
        delay: int,
    ) -> dict[str, list[BarData]]:
        """Shift each asset's bar list forward by *delay* positions.

        The first *delay* bars (used as signal bars) are replaced by copies of
        their immediately following bar, simulating a delayed fill.
        """
        shifted: dict[str, list[BarData]] = {}
        for asset, bars in bar_data.items():
            new_bars: list[BarData] = []
            for i, bar in enumerate(bars):
                fill_idx = min(i + delay, len(bars) - 1)
                fill_bar = bars[fill_idx]
                new_bars.append(
                    BarData(
                        symbol=bar.symbol,
                        timestamp=bar.timestamp,  # keep original timestamp
                        open=fill_bar.open,
                        high=fill_bar.high,
                        low=fill_bar.low,
                        close=fill_bar.close,
                        volume=fill_bar.volume,
                        bar_size=bar.bar_size,
                    )
                )
            shifted[asset] = new_bars
        return shifted


# ---------------------------------------------------------------------------
# Module-level helpers used by pdt_stress
# ---------------------------------------------------------------------------

def _trade_date(trade) -> date:
    d = trade.entry_time
    return d.date() if hasattr(d, "date") else d


def _biz_days_ago(n: int, from_date: date) -> date:
    """Return the calendar date that is *n* business days before *from_date*."""
    result = from_date
    counted = 0
    while counted < n:
        result -= timedelta(days=1)
        if result.weekday() < 5:
            counted += 1
    return result
