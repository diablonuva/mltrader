from __future__ import annotations

import csv
import math
import random
import statistics
from collections import defaultdict
from datetime import date
from typing import Optional

import numpy as np

from src.models import BacktestResults, ExitReason, RegimeLabel


_RISK_FREE_RATE_DAILY = 0.02 / 252   # from config default 2 % annual


def _daily_equity_series(results: BacktestResults) -> tuple[list[date], list[float]]:
    """Return (sorted_days, equity_per_day) using the last equity value of each day."""
    daily: dict[date, float] = {}
    for ts, eq in results.equity_curve:
        d = ts.date() if hasattr(ts, "date") else ts
        daily[d] = eq   # last bar of each day
    if not daily:
        return [], []
    sorted_days = sorted(daily)
    return sorted_days, [daily[d] for d in sorted_days]


def _max_drawdown(equities: list[float]) -> tuple[float, int]:
    """Return (max_drawdown_pct, max_duration_days) from a daily equity list."""
    peak = equities[0]
    peak_idx = 0
    max_dd = 0.0
    max_dur = 0
    curr_dur = 0

    for i, eq in enumerate(equities):
        if eq >= peak:
            peak = eq
            peak_idx = i
            curr_dur = 0
        else:
            curr_dur = i - peak_idx
            dd = (peak - eq) / peak
            max_dd = max(max_dd, dd)
            max_dur = max(max_dur, curr_dur)

    return max_dd, max_dur


def _percentiles(values: list[float], p_list: list[int]) -> dict[str, float]:
    if not values:
        return {f"p{p}": float("nan") for p in p_list}
    arr = sorted(values)
    result = {}
    for p in p_list:
        idx = (p / 100) * (len(arr) - 1)
        lo, hi = int(idx), min(int(idx) + 1, len(arr) - 1)
        frac = idx - lo
        result[f"p{p}"] = arr[lo] * (1 - frac) + arr[hi] * frac
    return result


class PerformanceAnalyzer:
    """Compute and report comprehensive performance metrics from BacktestResults."""

    def __init__(self, results: BacktestResults, config: dict) -> None:
        self._results = results
        self._config = config
        self._rf_daily = float(
            config.get("backtest", {}).get("risk_free_rate", 0.02)
        ) / 252

    # ------------------------------------------------------------------
    # Public: full metric computation
    # ------------------------------------------------------------------

    def compute_all(self) -> dict:
        metrics: dict = {}
        metrics.update(self._per_trade_metrics())
        metrics.update(self._portfolio_metrics())
        metrics["regime_breakdown"] = self._regime_breakdown()
        metrics.update(self._benchmark_metrics())
        metrics["monte_carlo"] = self._monte_carlo(n_runs=200)
        return metrics

    # ------------------------------------------------------------------
    # Public: display and export
    # ------------------------------------------------------------------

    def print_rich_table(self, metrics: dict) -> None:
        """Print a Rich-formatted summary table with colour-coded thresholds."""
        try:
            from rich.console import Console
            from rich.table import Table

            console = Console()

            # Colour-code helper
            def _color(value: float, green: float, yellow: float, low_is_bad=True) -> str:
                """Return a Rich markup string with colour applied."""
                if low_is_bad:
                    if value >= green:
                        return f"[green]{value:.4f}[/green]"
                    if value >= yellow:
                        return f"[yellow]{value:.4f}[/yellow]"
                    return f"[red]{value:.4f}[/red]"
                else:  # low is good (e.g. max drawdown)
                    if value <= green:
                        return f"[green]{value:.4f}[/green]"
                    if value <= yellow:
                        return f"[yellow]{value:.4f}[/yellow]"
                    return f"[red]{value:.4f}[/red]"

            # Portfolio table
            pt = Table(title="Portfolio Metrics", show_lines=True)
            pt.add_column("Metric", style="cyan")
            pt.add_column("Value", justify="right")

            sharpe = metrics.get("sharpe_ratio", float("nan"))
            max_dd = metrics.get("max_drawdown_pct", float("nan"))
            pf = metrics.get("profit_factor", float("nan"))

            rows = [
                ("Total Return %", f"{metrics.get('total_return_pct', 0)*100:.2f}%"),
                ("CAGR %", f"{metrics.get('cagr_pct', 0)*100:.2f}%"),
                ("Sharpe Ratio", _color(sharpe, green=1.5, yellow=1.0)),
                ("Sortino Ratio", f"{metrics.get('sortino_ratio', 0):.4f}"),
                ("Max Drawdown %", _color(max_dd, green=0.10, yellow=0.15, low_is_bad=False)),
                ("Max DD Duration (days)", str(metrics.get("max_drawdown_duration_days", 0))),
                ("Calmar Ratio", f"{metrics.get('calmar_ratio', 0):.4f}"),
                ("Profit Factor", _color(pf, green=1.5, yellow=1.2)),
                ("Win Rate %", f"{metrics.get('win_rate', 0)*100:.1f}%"),
                ("Avg Win %", f"{metrics.get('avg_win_pct', 0)*100:.2f}%"),
                ("Avg Loss %", f"{metrics.get('avg_loss_pct', 0)*100:.2f}%"),
                ("Trades / Day", f"{metrics.get('trades_per_day', 0):.3f}"),
                ("Total Trades", str(metrics.get("total_trades", 0))),
            ]
            for label, val in rows:
                pt.add_row(label, val)
            console.print(pt)

            # Monte Carlo summary
            mc = metrics.get("monte_carlo", {})
            if mc:
                mt = Table(title="Monte Carlo (200 runs)", show_lines=True)
                mt.add_column("Metric", style="cyan")
                for p in ("p5", "p25", "p50", "p75", "p95"):
                    mt.add_column(p, justify="right")
                for mc_metric in ("total_return", "max_drawdown"):
                    row_data = mc.get(mc_metric, {})
                    mt.add_row(
                        mc_metric,
                        *[f"{row_data.get(p, float('nan')):.4f}" for p in
                          ("p5", "p25", "p50", "p75", "p95")],
                    )
                console.print(mt)

        except ImportError:
            self._print_plain(metrics)

    def _print_plain(self, metrics: dict) -> None:
        print("\n=== Performance Metrics ===")
        for k, v in metrics.items():
            if k not in ("regime_breakdown", "monte_carlo", "exit_reason_distribution"):
                print(f"  {k}: {v}")

    def export_csv(self, path: str) -> None:
        """Write per-trade and equity-curve CSVs to {path}_trades.csv / {path}_equity.csv."""
        # Trades
        trades_path = f"{path}_trades.csv"
        trades = self._results.all_trades
        if trades:
            fields = [
                "asset", "direction", "entry_price", "exit_price", "shares",
                "entry_time", "exit_time", "pnl_pct", "pnl_dollar",
                "regime_at_entry", "strategy_name", "hold_bars", "exit_reason",
            ]
            with open(trades_path, "w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=fields)
                w.writeheader()
                for t in trades:
                    w.writerow({
                        "asset": t.asset,
                        "direction": t.direction.value,
                        "entry_price": t.entry_price,
                        "exit_price": t.exit_price,
                        "shares": t.shares,
                        "entry_time": t.entry_time.isoformat(),
                        "exit_time": t.exit_time.isoformat(),
                        "pnl_pct": round(t.pnl_pct, 6),
                        "pnl_dollar": round(t.pnl_dollar, 4),
                        "regime_at_entry": t.regime_at_entry.value,
                        "strategy_name": t.strategy_name,
                        "hold_bars": t.hold_bars,
                        "exit_reason": t.exit_reason.value,
                    })

        # Equity curve
        equity_path = f"{path}_equity.csv"
        with open(equity_path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["timestamp", "equity"])
            for ts, eq in self._results.equity_curve:
                w.writerow([ts.isoformat() if hasattr(ts, "isoformat") else str(ts), eq])

    # ------------------------------------------------------------------
    # Private: per-trade metrics
    # ------------------------------------------------------------------

    def _per_trade_metrics(self) -> dict:
        trades = self._results.all_trades
        if not trades:
            return {
                "total_trades": 0,
                "win_rate": 0.0,
                "avg_win_pct": 0.0,
                "avg_loss_pct": 0.0,
                "profit_factor": 0.0,
                "avg_hold_bars": 0.0,
                "exit_reason_distribution": {},
            }

        wins = [t for t in trades if t.pnl_pct > 0]
        losses = [t for t in trades if t.pnl_pct <= 0]

        win_rate = len(wins) / len(trades)
        avg_win = statistics.mean(t.pnl_pct for t in wins) if wins else 0.0
        avg_loss = statistics.mean(t.pnl_pct for t in losses) if losses else 0.0

        gross_profit = sum(t.pnl_dollar for t in wins) if wins else 0.0
        gross_loss = abs(sum(t.pnl_dollar for t in losses)) if losses else 0.0
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else float("inf")

        avg_hold = statistics.mean(t.hold_bars for t in trades)

        exit_dist: dict[str, int] = defaultdict(int)
        for t in trades:
            exit_dist[t.exit_reason.value] += 1

        return {
            "total_trades": len(trades),
            "win_rate": win_rate,
            "avg_win_pct": avg_win,
            "avg_loss_pct": avg_loss,
            "profit_factor": profit_factor,
            "avg_hold_bars": avg_hold,
            "exit_reason_distribution": dict(exit_dist),
        }

    # ------------------------------------------------------------------
    # Private: portfolio metrics
    # ------------------------------------------------------------------

    def _portfolio_metrics(self) -> dict:
        days, equities = _daily_equity_series(self._results)
        if len(equities) < 2:
            return {
                "total_return_pct": 0.0,
                "cagr_pct": 0.0,
                "sharpe_ratio": 0.0,
                "sortino_ratio": 0.0,
                "max_drawdown_pct": 0.0,
                "max_drawdown_duration_days": 0,
                "calmar_ratio": 0.0,
                "trades_per_day": 0.0,
            }

        initial = self._results.initial_capital
        final = equities[-1]
        total_return = (final - initial) / initial

        n_days = max((days[-1] - days[0]).days, 1)
        trading_days = len(days)
        cagr = (final / initial) ** (365.0 / n_days) - 1.0

        daily_returns = [
            (equities[i + 1] - equities[i]) / equities[i]
            for i in range(len(equities) - 1)
        ]
        mean_ret = statistics.mean(daily_returns)
        std_ret = statistics.stdev(daily_returns) if len(daily_returns) > 1 else 1e-9

        excess = mean_ret - self._rf_daily
        sharpe = (excess / std_ret) * math.sqrt(252) if std_ret > 0 else 0.0

        downside = [r for r in daily_returns if r < 0]
        downside_std = statistics.stdev(downside) if len(downside) > 1 else 1e-9
        sortino = (excess / downside_std) * math.sqrt(252) if downside_std > 0 else 0.0

        max_dd, max_dur = _max_drawdown(equities)
        calmar = (cagr / max_dd) if max_dd > 0 else float("inf")

        trades_per_day = len(self._results.all_trades) / max(trading_days, 1)

        return {
            "total_return_pct": total_return,
            "cagr_pct": cagr,
            "sharpe_ratio": sharpe,
            "sortino_ratio": sortino,
            "max_drawdown_pct": max_dd,
            "max_drawdown_duration_days": max_dur,
            "calmar_ratio": calmar,
            "trades_per_day": trades_per_day,
        }

    # ------------------------------------------------------------------
    # Private: regime breakdown
    # ------------------------------------------------------------------

    def _regime_breakdown(self) -> dict:
        trades = self._results.all_trades
        if not trades:
            return {}

        by_regime: dict[str, list] = defaultdict(list)
        for t in trades:
            by_regime[t.regime_at_entry.value].append(t)

        total_pnl = sum(t.pnl_dollar for t in trades) or 1.0
        result = {}
        for regime_name, rtrades in by_regime.items():
            wins = [t for t in rtrades if t.pnl_pct > 0]
            regime_pnl = sum(t.pnl_dollar for t in rtrades)
            result[regime_name] = {
                "trade_count": len(rtrades),
                "win_rate": len(wins) / len(rtrades),
                "avg_pnl_pct": statistics.mean(t.pnl_pct for t in rtrades),
                "pnl_contribution_pct": regime_pnl / total_pnl,
            }
        return result

    # ------------------------------------------------------------------
    # Private: benchmark (buy-and-hold approximation)
    # ------------------------------------------------------------------

    def _benchmark_metrics(self) -> dict:
        trades = self._results.all_trades
        if not trades:
            return {"buy_and_hold_return_pct": float("nan"),
                    "excess_return_vs_bah": float("nan")}

        # Approximate: buy first entry, hold to last exit price
        first_price = trades[0].entry_price
        last_price = trades[-1].exit_price
        bah_return = (last_price - first_price) / first_price if first_price > 0 else 0.0

        _, equities = _daily_equity_series(self._results)
        initial = self._results.initial_capital
        strategy_return = (equities[-1] - initial) / initial if equities else 0.0
        excess = strategy_return - bah_return

        return {
            "buy_and_hold_return_pct": bah_return,
            "excess_return_vs_bah": excess,
        }

    # ------------------------------------------------------------------
    # Private: Monte Carlo bootstrap
    # ------------------------------------------------------------------

    def _monte_carlo(self, n_runs: int = 200, seed: int = 42) -> dict:
        trades = self._results.all_trades
        if not trades:
            return {}

        # Group trades by calendar day
        by_day: dict[date, list] = defaultdict(list)
        for t in trades:
            d = t.entry_time.date() if hasattr(t.entry_time, "date") else t.entry_time
            by_day[d].append(t)

        days = sorted(by_day)
        if len(days) < 2:
            return {}

        rng = random.Random(seed)
        total_returns: list[float] = []
        max_drawdowns: list[float] = []
        initial = self._results.initial_capital

        for _ in range(n_runs):
            # Resample days with replacement
            sampled_days = [rng.choice(days) for _ in days]
            equity = initial
            equity_series = [equity]

            for d in sampled_days:
                for t in by_day[d]:
                    equity *= 1.0 + t.pnl_pct
                equity_series.append(equity)

            total_ret = (equity_series[-1] - initial) / initial
            max_dd, _ = _max_drawdown(equity_series)
            total_returns.append(total_ret)
            max_drawdowns.append(max_dd)

        p_list = [5, 25, 50, 75, 95]
        return {
            "total_return": _percentiles(total_returns, p_list),
            "max_drawdown": _percentiles(max_drawdowns, p_list),
        }
