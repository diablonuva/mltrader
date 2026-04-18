"""Walk-forward backtest CLI.

Usage examples:
  python -m src.backtest.cli --asset SPY --start 2023-01-01 --end 2023-12-31
  python -m src.backtest.cli --all-assets --start 2024-01-01 --end 2024-06-30
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger(__name__)


def _fetch_bars(
    asset: str,
    start: datetime,
    end: datetime,
    bar_size_minutes: int = 5,
) -> list:
    """Download historical bars from Alpaca and cache to Parquet."""
    from dotenv import load_dotenv
    from src.models import AssetClass, BarData

    load_dotenv()
    api_key = os.environ.get("ALPACA_API_KEY", "")
    secret_key = os.environ.get("ALPACA_SECRET_KEY", "")

    cache_dir = Path("data")
    cache_dir.mkdir(exist_ok=True)
    safe_name = asset.replace("/", "_")
    cache_file = cache_dir / f"{safe_name}_{start.date()}_{end.date()}_{bar_size_minutes}min.parquet"

    if cache_file.exists():
        import pandas as pd
        logger.info("Loading %s from cache: %s", asset, cache_file)
        df = pd.read_parquet(cache_file)
    else:
        import pandas as pd
        from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

        tf = TimeFrame(bar_size_minutes, TimeFrameUnit.Minute)

        if AssetClass.from_symbol(asset) is AssetClass.CRYPTO:
            from alpaca.data.historical import CryptoHistoricalDataClient
            from alpaca.data.requests import CryptoBarsRequest

            client = CryptoHistoricalDataClient(api_key, secret_key)
            req = CryptoBarsRequest(
                symbol_or_symbols=asset,
                timeframe=tf,
                start=start,
                end=end,
            )
            bars = client.get_crypto_bars(req)
        else:
            from alpaca.data.historical import StockHistoricalDataClient
            from alpaca.data.requests import StockBarsRequest

            client = StockHistoricalDataClient(api_key, secret_key)
            req = StockBarsRequest(
                symbol_or_symbols=asset,
                timeframe=tf,
                start=start,
                end=end,
            )
            bars = client.get_stock_bars(req)

        df = bars.df
        if isinstance(df.index, pd.MultiIndex):
            df = df.xs(asset, level="symbol")
        df.to_parquet(cache_file)
        logger.info("Cached %d bars for %s → %s", len(df), asset, cache_file)

    from src.models import BarData

    result: list[BarData] = []
    for ts, row in df.iterrows():
        if hasattr(ts, "to_pydatetime"):
            ts = ts.to_pydatetime()
        result.append(
            BarData(
                symbol=asset,
                timestamp=ts,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
                bar_size=f"{bar_size_minutes}Min",
            )
        )
    return result


def _print_summary(results, assets: list[str]) -> None:
    """Print a Rich summary table to stdout."""
    try:
        from rich.console import Console
        from rich.table import Table

        console = Console()
        table = Table(title="Walk-Forward Backtest Results", show_lines=True)
        table.add_column("Window", style="cyan")
        table.add_column("Test Start")
        table.add_column("# Trades", justify="right")
        table.add_column("Equity End", justify="right", style="green")

        for row in results.per_window_results:
            ts = row.get("test_start")
            ts_str = ts.strftime("%Y-%m-%d") if ts else "—"
            table.add_row(
                str(row["window"]),
                ts_str,
                str(row["n_trades"]),
                f"${row['equity_end']:,.2f}",
            )
        console.print(table)

        if results.all_trades:
            import statistics
            from collections import Counter
            from src.models import ExitReason, Direction

            # Full trades only (exclude PARTIAL_TP records — they're sub-entries of full trades)
            full_trades = [t for t in results.all_trades if not t.is_partial]
            partial_trades = [t for t in results.all_trades if t.is_partial]

            pnls = [t.pnl_pct for t in full_trades]
            wins = sum(1 for p in pnls if p > 0)
            console.print(f"\n[bold]Total trades:[/bold] {len(pnls)}"
                          + (f"  ({len(partial_trades)} partial TP1 closes)"
                             if partial_trades else ""))
            console.print(
                f"[bold]Win rate:[/bold] {wins/len(pnls)*100:.1f}%"
                f"  [bold]Mean PnL:[/bold] {statistics.mean(pnls)*100:.2f}%"
            )

            # Exit reason breakdown (full trades only)
            exit_counts = Counter(t.exit_reason for t in full_trades)
            exit_table = Table(title="Exit Reason Breakdown", show_lines=True)
            exit_table.add_column("Exit Reason", style="cyan")
            exit_table.add_column("Count", justify="right")
            exit_table.add_column("Avg PnL %", justify="right")
            exit_table.add_column("Win Rate", justify="right")
            for reason, cnt in sorted(exit_counts.items(), key=lambda x: -x[1]):
                reason_trades = [t for t in full_trades if t.exit_reason == reason]
                avg_pnl = statistics.mean(t.pnl_pct for t in reason_trades) * 100
                wr = sum(1 for t in reason_trades if t.pnl_pct > 0) / cnt * 100
                exit_table.add_row(
                    str(reason.value) if hasattr(reason, "value") else str(reason),
                    str(cnt),
                    f"{avg_pnl:+.3f}%",
                    f"{wr:.1f}%",
                )
            console.print(exit_table)

            # Direction breakdown
            long_trades = [t for t in full_trades if t.direction is Direction.LONG]
            short_trades = [t for t in full_trades if t.direction is Direction.SHORT]
            if long_trades:
                long_wr = sum(1 for t in long_trades if t.pnl_pct > 0) / len(long_trades) * 100
                long_avg = statistics.mean(t.pnl_pct for t in long_trades) * 100
                console.print(f"[bold]LONG:[/bold]  {len(long_trades)} trades  WR={long_wr:.1f}%  Avg={long_avg:+.3f}%")
            if short_trades:
                short_wr = sum(1 for t in short_trades if t.pnl_pct > 0) / len(short_trades) * 100
                short_avg = statistics.mean(t.pnl_pct for t in short_trades) * 100
                console.print(f"[bold]SHORT:[/bold] {len(short_trades)} trades  WR={short_wr:.1f}%  Avg={short_avg:+.3f}%")

            # Regime breakdown
            regime_counts = Counter(t.regime_at_entry for t in full_trades)
            if regime_counts:
                console.print("\n[bold]By Regime:[/bold]")
                for regime, cnt in sorted(regime_counts.items(), key=lambda x: -x[1]):
                    r_trades = [t for t in full_trades if t.regime_at_entry == regime]
                    r_wr = sum(1 for t in r_trades if t.pnl_pct > 0) / cnt * 100
                    r_avg = statistics.mean(t.pnl_pct for t in r_trades) * 100
                    console.print(f"  {str(regime.value) if hasattr(regime,'value') else regime}: {cnt} trades  WR={r_wr:.1f}%  Avg={r_avg:+.3f}%")

            # Hold-bar distribution
            hold_bars = [t.hold_bars for t in full_trades]
            console.print(f"\n[bold]Hold bars:[/bold] min={min(hold_bars)}  median={statistics.median(hold_bars):.0f}  max={max(hold_bars)}")

        if results.equity_curve:
            start_eq = results.equity_curve[0][1]
            end_eq = results.equity_curve[-1][1]
            console.print(
                f"[bold]Equity:[/bold] ${start_eq:,.0f} -> ${end_eq:,.0f}"
                f"  ({(end_eq/start_eq - 1)*100:+.1f}%)"
            )
    except ImportError:
        # Fallback without Rich
        print("\n=== Walk-Forward Backtest Results ===")
        for row in results.per_window_results:
            print(f"  Window {row['window']}: trades={row['n_trades']} equity=${row['equity_end']:,.2f}")
        print(f"Total trades: {len(results.all_trades)}")


def main() -> None:
    from src.backtest.backtester import WalkForwardBacktester
    from src.config_loader import load_config

    parser = argparse.ArgumentParser(description="Walk-Forward Backtester")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--asset", type=str, help="Single asset symbol, e.g. SPY")
    group.add_argument(
        "--all-assets",
        action="store_true",
        help="Use primary_equity + primary_crypto from config",
    )
    parser.add_argument("--start", required=True, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="End date YYYY-MM-DD")
    parser.add_argument("--config", default="config/settings.yaml")
    args = parser.parse_args()

    cfg = load_config(args.config)
    start_dt = datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end_dt = datetime.strptime(args.end, "%Y-%m-%d").replace(tzinfo=timezone.utc)

    if args.all_assets:
        symbols = (
            cfg["assets"].get("primary_equity", [])
            + cfg["assets"].get("primary_crypto", [])
        )
    else:
        symbols = [args.asset]

    bar_size_str: str = cfg.get("features", {}).get("bar_size", "5Min")
    try:
        bar_size_minutes = int(bar_size_str.replace("Min", "").strip())
    except ValueError:
        bar_size_minutes = 5

    logger.info(
        "Fetching bars for: %s  (%s -> %s)  bar_size=%s",
        symbols, args.start, args.end, bar_size_str,
    )
    bar_data: dict[str, list] = {}
    for sym in symbols:
        try:
            bar_data[sym] = _fetch_bars(sym, start_dt, end_dt, bar_size_minutes)
            logger.info("  %s: %d bars", sym, len(bar_data[sym]))
        except Exception as exc:
            logger.error("  Failed to fetch %s: %s", sym, exc)

    if not bar_data:
        logger.error("No data loaded — aborting.")
        sys.exit(1)

    backtester = WalkForwardBacktester(cfg)
    logger.info("Running walk-forward backtest…")
    results = backtester.run(bar_data, start_dt, end_dt)

    _print_summary(results, list(bar_data.keys()))


if __name__ == "__main__":
    main()
