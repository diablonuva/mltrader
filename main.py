"""
ML Trader — Diablo v1 — Main Trading Loop
Architecture: HMM Regime Detection + LightGBM Expert Classifiers
Broker: Alpaca Paper Trading API
"""
from __future__ import annotations

import argparse
import asyncio
import concurrent.futures
import logging
import os
import signal
import sys
import threading
import traceback
from datetime import datetime, timezone
from typing import Optional

import numpy as np

from src.allocation.position_sizer import ATRPositionSizer
from src.brain.feature_engineering import FeatureEngineer, build_feature_matrix
from src.brain.hmm_engine import HMMEngine
from src.brain.lgbm_experts import LGBMExpertRouter, LGBMExpertTrainer
from src.brain.regime_strategies import StrategyOrchestrator
from src.config_loader import load_config, validate_config
from src.models import AssetClass, BarData, ExitReason, PortfolioState, RegimeLabel
from src.monitoring.alerting import AlertingSystem
from src.monitoring.logger import StructuredLogger
from src.monitoring.performance_reporter import PerformanceReporter
from src.risk.circuit_breaker import CircuitBreaker
from src.risk.pdt_guard import PDTGuard
from src.risk.risk_manager import RiskManager
from src.session.session_manager import SessionManager

logger = logging.getLogger(__name__)

_MODEL_DIR = "models"
_PERMITTED_ASSETS: frozenset[str] = frozenset({"SPY"})


def _assert_spy_only(config: dict) -> None:
    """Hard-stop if the asset list contains anything outside _PERMITTED_ASSETS.

    This guard exists because the multi-asset pipeline (QQQ, BTC/USD, ETH/USD)
    was shown to degrade SPY performance by ~19% due to incompatible volatility
    profiles.  Any accidental --assets override or config edit that reintroduces
    those symbols will be caught here before any broker connection is made.
    """
    equity = config["assets"].get("primary_equity", [])
    crypto = config["assets"].get("primary_crypto", [])
    all_assets = equity + crypto

    unsupported = [a for a in all_assets if a not in _PERMITTED_ASSETS]
    if unsupported:
        raise SystemExit(
            f"\n[ASSET GUARD] Unsupported asset(s) in pipeline: {unsupported}\n"
            "The live pipeline is configured for SPY paper trading only.\n"
            "Remove these symbols from config/settings.yaml → assets section\n"
            "or do not pass them via --assets."
        )
    if not all_assets:
        raise SystemExit(
            "[ASSET GUARD] No assets configured. "
            "Set assets.primary_equity: [\"SPY\"] in config/settings.yaml."
        )


# ---------------------------------------------------------------------------
# Rich startup banner
# ---------------------------------------------------------------------------

def _print_banner(config: dict, is_paper: bool) -> None:
    assets_cfg = config.get("assets", {})
    all_assets = (
        assets_cfg.get("primary_equity", []) + assets_cfg.get("primary_crypto", [])
    )
    mode_tag = "PAPER" if is_paper else "*** LIVE ***"
    try:
        from rich.console import Console
        from rich.panel import Panel

        console = Console()
        body = (
            f"Mode     : [bold {'green' if is_paper else 'red'}]{mode_tag}[/]\n"
            f"Assets   : {', '.join(all_assets)}\n"
            f"HMM conf : {config['hmm']['confidence_threshold']:.2f}   "
            f"LGBM conf: {config['lgbm']['confidence_threshold']:.2f}\n"
            f"Log dir  : {config['monitoring']['log_dir']}\n"
            f"Started  : {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
        )
        console.print(
            Panel(
                body,
                title="[bold cyan]🧠 ML Trader — Diablo v1[/bold cyan]",
                border_style="cyan",
            )
        )
    except ImportError:
        print("\n" + "=" * 60)
        print("  ML Trader — Diablo v1")
        print(f"  Mode   : {mode_tag}")
        print(f"  Assets : {', '.join(all_assets)}")
        print("=" * 60 + "\n")


# ---------------------------------------------------------------------------
# MLTrader
# ---------------------------------------------------------------------------

class MLTrader:
    """Async orchestration loop that wires every module into the live system.

    Bar events arrive on background Alpaca streaming threads and are dispatched
    to ``on_bar`` via a thread-safe asyncio queue so all state mutations happen
    on a single async task — no locking required.
    """

    def __init__(self, config: dict) -> None:
        self._config = config
        self._running = False

        # Thread-safe queue: streaming threads push; main loop pops
        self._bar_queue: asyncio.Queue[BarData] = asyncio.Queue(maxsize=1000)

        # Equity curve for the current session
        self._equity_curve: list[tuple[datetime, float]] = []

        # Per-bar tracking
        self._last_bar_time: dict[str, datetime] = {}
        self._retrain_bar_counters: dict[str, int] = {}
        self._prev_regime: dict[str, Optional[RegimeLabel]] = {}

        # Thread pool for background HMM/LGBM retraining (max 2 workers)
        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="retrain"
        )

        # ----------------------------------------------------------------
        # Core services
        # ----------------------------------------------------------------
        self._structured_logger = StructuredLogger(config)
        self._alerting = AlertingSystem(config, self._structured_logger)
        self._reporter = PerformanceReporter(config)

        self._session_manager = SessionManager(config)
        self._position_sizer = ATRPositionSizer(config)
        self._pdt_guard = PDTGuard(config)
        self._circuit_breaker = CircuitBreaker(config)
        self._risk_manager = RiskManager(
            config,
            self._pdt_guard,
            self._circuit_breaker,
            self._session_manager,
        )
        self._orchestrator = StrategyOrchestrator(
            config,
            self._session_manager,
            self._position_sizer,
            self._pdt_guard,
            self._circuit_breaker,
        )

        # ----------------------------------------------------------------
        # Per-asset brain instances
        # ----------------------------------------------------------------
        all_assets = (
            config["assets"].get("primary_equity", [])
            + config["assets"].get("primary_crypto", [])
        )
        self._assets: list[str] = all_assets

        self._hmm_engines: dict[str, HMMEngine] = {}
        self._feature_engineers: dict[str, FeatureEngineer] = {}
        self._lgbm_trainers: dict[str, LGBMExpertTrainer] = {}
        self._lgbm_routers: dict[str, LGBMExpertRouter] = {}
        self._retrain_locks: dict[str, threading.Lock] = {}

        for asset in all_assets:
            ac = AssetClass.from_symbol(asset)
            self._hmm_engines[asset] = HMMEngine(config, asset)
            self._feature_engineers[asset] = FeatureEngineer(config, ac)
            self._lgbm_trainers[asset] = LGBMExpertTrainer(config, asset)
            self._lgbm_routers[asset] = LGBMExpertRouter(config, asset)
            self._retrain_locks[asset] = threading.Lock()
            self._retrain_bar_counters[asset] = 0

        # ----------------------------------------------------------------
        # Broker — deferred until run() so --help works without .env
        # ----------------------------------------------------------------
        self._alpaca_client = None
        self._executor_broker = None
        self._portfolio_state: Optional[PortfolioState] = None

    # ------------------------------------------------------------------
    # Thread-safe bar enqueue (called from Alpaca streaming threads)
    # ------------------------------------------------------------------

    def _enqueue_bar(self, bar: BarData) -> None:
        """Bridge from streaming threads into the asyncio queue.

        Must use call_soon_threadsafe — asyncio.Queue.put_nowait() is not
        thread-safe when called from outside the running event loop (Python 3.10+).
        Direct put_nowait() from a foreign thread adds to the deque but never
        wakes the event loop's await queue.get(), so bars are silently lost.
        """
        loop: Optional[asyncio.AbstractEventLoop] = getattr(self, "_loop", None)
        if loop and loop.is_running():
            try:
                loop.call_soon_threadsafe(self._bar_queue.put_nowait, bar)
            except asyncio.QueueFull:
                logger.warning(
                    "Bar queue full — dropping bar for %s @ %s", bar.symbol, bar.timestamp
                )
        else:
            # Fallback before run() sets self._loop (should not occur in normal operation)
            try:
                self._bar_queue.put_nowait(bar)
            except asyncio.QueueFull:
                logger.warning(
                    "Bar queue full (no loop) — dropping bar for %s @ %s",
                    bar.symbol, bar.timestamp,
                )

    def _emit_state(self, now) -> None:
        """Write shared_state.json on every processed bar — even before HMM is trained."""
        if self._portfolio_state is None:
            return
        regime_info = {
            a: {
                "regime": (
                    self._hmm_engines[a].get_current_regime().value
                    if self._hmm_engines[a].is_trained else "UNKNOWN"
                ),
                "confidence": (
                    self._hmm_engines[a].get_confidence()
                    if self._hmm_engines[a].is_trained else 0.0
                ),
            }
            for a in self._assets
        }
        self._equity_curve.append((now, self._portfolio_state.equity))
        self._structured_logger.update_shared_state(
            portfolio_state=self._portfolio_state,
            regime_info=regime_info,
            signal_history=self._orchestrator.get_signal_history(),
            equity_curve=self._equity_curve,
        )

    # ------------------------------------------------------------------
    # Core bar handler (runs on the asyncio event loop)
    # ------------------------------------------------------------------

    async def on_bar(self, bar: BarData) -> None:
        """Process a single 5-min bar through the full signal + risk pipeline."""
        asset = bar.symbol
        now = bar.timestamp
        ac = AssetClass.from_symbol(asset)

        # ----------------------------------------------------------------
        # Session boundary detection + reset
        # ----------------------------------------------------------------
        if self._session_manager.is_new_session(
            asset, now, self._last_bar_time.get(asset)
        ):
            self._session_manager.reset_session(asset)
            self._feature_engineers[asset].reset_session()
            self._circuit_breaker.reset_session()
            self._risk_manager.reset_daily_counts()

            self._portfolio_state = self._refresh_portfolio_state()
            if self._portfolio_state:
                self._structured_logger.log_session_open(
                    assets=self._assets,
                    equity=self._portfolio_state.equity,
                    timestamp=now,
                )
                self._alerting.alert(
                    level="INFO",
                    title=f"Session Open — {asset}",
                    message=f"equity=${self._portfolio_state.equity:,.2f}",
                    data={"asset": asset, "timestamp": str(now)},
                )

        self._session_manager.increment_bar(asset)

        # ----------------------------------------------------------------
        # Feature computation
        # ----------------------------------------------------------------
        self._feature_engineers[asset].update(bar)
        bars_since_open = self._session_manager.get_bars_since_open(asset)
        raw_features = self._feature_engineers[asset].compute_features(bars_since_open)
        if raw_features is None:
            self._last_bar_time[asset] = now
            self._emit_state(now)
            return

        # ----------------------------------------------------------------
        # HMM inference — skip if model not yet trained
        # ----------------------------------------------------------------
        hmm = self._hmm_engines[asset]
        if not hmm.is_trained:
            self._last_bar_time[asset] = now
            self._emit_state(now)
            return

        hmm.step(raw_features)
        regime: RegimeLabel = hmm.get_current_regime()
        hmm_conf: float = hmm.get_confidence()
        is_flickering: bool = hmm.is_flickering()
        is_confirmed: bool = hmm.is_confirmed()

        # Regime-change alert
        prev = self._prev_regime.get(asset)
        if prev is not None and prev != regime:
            self._alerting.regime_change(
                asset=asset,
                old=prev.value,
                new=regime.value,
                confidence=hmm_conf,
                timestamp=now,
            )
            self._structured_logger.log_regime_change(
                asset=asset,
                old_regime=prev,
                new_regime=regime,
                confidence=hmm_conf,
                timestamp=now,
            )
        self._prev_regime[asset] = regime

        # ----------------------------------------------------------------
        # LightGBM inference
        # ----------------------------------------------------------------
        router = self._lgbm_routers[asset]
        if self._feature_engineers[asset].is_scaler_fitted:
            scaled = self._feature_engineers[asset].transform(raw_features)
        else:
            scaled = raw_features.reshape(1, -1) if raw_features.ndim == 1 else raw_features

        from src.models import Direction  # avoid top-level circular risk
        lgbm_dir, lgbm_conf = router.predict(regime, scaled)

        # ----------------------------------------------------------------
        # Strategy selection
        # ----------------------------------------------------------------
        feature_names = self._feature_engineers[asset].get_feature_names()
        feature_dict = dict(zip(feature_names, raw_features))
        or_tracker = getattr(self._feature_engineers[asset], "_or", None)
        vwap_val: float = self._feature_engineers[asset]._vwap.get_vwap()
        recent_bars = list(self._feature_engineers[asset]._history)

        if self._portfolio_state is None:
            self._portfolio_state = self._refresh_portfolio_state()
        if self._portfolio_state is None:
            self._last_bar_time[asset] = now
            return

        signal = self._orchestrator.select_strategy(
            regime=regime,
            hmm_confidence=hmm_conf,
            hmm_is_flickering=is_flickering,
            hmm_is_confirmed=is_confirmed,
            lgbm_direction=lgbm_dir,
            lgbm_confidence=lgbm_conf,
            feature_dict=feature_dict,
            asset=asset,
            asset_class=ac,
            current_bar=bar,
            recent_bars=recent_bars,
            vwap=vwap_val,
            or_tracker=or_tracker,
            bars_since_open=bars_since_open,
            portfolio_state=self._portfolio_state,
            now=now,
        )

        # ----------------------------------------------------------------
        # Risk evaluation + order submission
        # ----------------------------------------------------------------
        if signal is not None and self._executor_broker is not None:
            decision = self._risk_manager.evaluate(signal, self._portfolio_state, now)
            self._structured_logger.log_risk_decision(signal, decision, now)

            if decision.approved:
                if decision.modified:
                    signal.size_pct = decision.modifications.get("size_pct", signal.size_pct)

                account = self._alpaca_client.get_account()
                order = self._executor_broker.submit_entry_order(
                    signal, float(account["equity"])
                )
                if order:
                    self._risk_manager.record_trade_open(asset)
                    logger.info(
                        "ORDER_SUBMITTED  %s %s  size_pct=%.3f  order_id=%s",
                        signal.direction.value, asset,
                        signal.size_pct, order.get("id"),
                    )

        # ----------------------------------------------------------------
        # Open position management: max-hold + EOD hard close
        # ----------------------------------------------------------------
        if self._portfolio_state and self._executor_broker:
            for pos_asset, pos in list(self._portfolio_state.positions.items()):
                pos.bars_held += 1
                if bar.symbol == pos_asset:
                    pos.current_price = bar.close

                if pos.bars_held >= pos.max_hold_bars:
                    logger.info(
                        "MAX_HOLD exit: %s  bars_held=%d >= %d",
                        pos_asset, pos.bars_held, pos.max_hold_bars,
                    )
                    self._executor_broker.close_position(pos_asset, ExitReason.MAX_HOLD)

                elif self._session_manager.is_eod_hard_close(pos_asset, now):
                    logger.info("EOD_HARD_CLOSE: %s", pos_asset)
                    result = self._executor_broker.close_position(
                        pos_asset, ExitReason.EOD_FLAT
                    )
                    if result is None:
                        self._alerting.eod_flat_failed(pos_asset, now)

        # ----------------------------------------------------------------
        # PDT approaching warning
        # ----------------------------------------------------------------
        pdt_remaining = self._pdt_guard.get_remaining_trades()
        if pdt_remaining == 1:
            self._alerting.pdt_approaching(
                current_count=self._pdt_guard.get_current_count(),
                max_count=self._config["pdt"]["max_daytrades_per_5d"],
            )

        # ----------------------------------------------------------------
        # Circuit breaker state sync
        # ----------------------------------------------------------------
        cb_now_active = self._circuit_breaker.is_active()
        if self._portfolio_state:
            if cb_now_active and not self._portfolio_state.circuit_breaker_active:
                cb_reason = self._circuit_breaker.get_reason()
                cb_equity = self._portfolio_state.equity
                self._alerting.circuit_breaker_triggered(
                    reason=cb_reason,
                    equity=cb_equity,
                    timestamp=now,
                )
                self._reporter.on_circuit_breaker(
                    reason=cb_reason,
                    equity=cb_equity,
                    timestamp=now,
                )
            self._portfolio_state.circuit_breaker_active = cb_now_active

        # ----------------------------------------------------------------
        # Periodic background retrain
        # ----------------------------------------------------------------
        self._retrain_bar_counters[asset] = (
            self._retrain_bar_counters.get(asset, 0) + 1
        )
        retrain_every = self._config["hmm"].get("retrain_every_bars", 390)
        if self._retrain_bar_counters[asset] >= retrain_every:
            self._retrain_bar_counters[asset] = 0
            asyncio.get_event_loop().run_in_executor(
                self._executor, self._retrain_background, asset
            )

        # ----------------------------------------------------------------
        # Structured logging + dashboard shared state
        # ----------------------------------------------------------------
        self._emit_state(now)
        self._last_bar_time[asset] = now

    # ------------------------------------------------------------------
    # Portfolio state sync from broker
    # ------------------------------------------------------------------

    def _refresh_portfolio_state(self) -> Optional[PortfolioState]:
        """Fetch current account state from Alpaca and build a PortfolioState."""
        if self._alpaca_client is None:
            return None
        try:
            acc = self._alpaca_client.get_account()
            existing_positions = (
                self._portfolio_state.positions if self._portfolio_state else {}
            )
            return PortfolioState(
                equity=float(acc["equity"]),
                cash=float(acc["cash"]),
                buying_power=float(acc["buying_power"]),
                positions=existing_positions,
                daily_pnl=0.0,
                session_open_equity=float(acc["equity"]),
                rolling_30m_equity_marks=[],
                consecutive_loss_count=0,
                circuit_breaker_active=False,
                circuit_breaker_resume_time=None,
                last_updated=datetime.now(timezone.utc),
            )
        except Exception:
            logger.error(
                "_refresh_portfolio_state failed:\n%s", traceback.format_exc()
            )
            return None

    # ------------------------------------------------------------------
    # Background retrain (runs in thread pool, never touches the event loop)
    # ------------------------------------------------------------------

    def _retrain_background(self, asset: str) -> None:
        """Re-train HMM + LightGBM off the main loop; swap models atomically."""
        lock = self._retrain_locks[asset]
        if not lock.acquire(blocking=False):
            logger.info("RETRAIN_SKIPPED [%s]: already in progress", asset)
            return

        try:
            logger.info("RETRAIN_STARTED [%s]", asset)

            bars = list(self._feature_engineers[asset]._history)
            if not bars:
                logger.warning("RETRAIN_ABORTED [%s]: no bar history", asset)
                return

            ac = AssetClass.from_symbol(asset)
            feature_matrix = build_feature_matrix(bars, self._config, ac)
            min_bars = self._config["hmm"].get("min_train_bars", 1000)
            if len(feature_matrix) < min_bars:
                logger.warning(
                    "RETRAIN_ABORTED [%s]: %d rows < %d required",
                    asset, len(feature_matrix), min_bars,
                )
                return

            feature_names = self._feature_engineers[asset].get_feature_names()

            # Fit new HMM
            new_hmm = HMMEngine(self._config, asset)
            new_hmm.train(
                feature_matrix=feature_matrix,
                feature_names=feature_names,
                end_timestamp=bars[-1].timestamp,
            )

            # Viterbi regime labels for LGBM supervision
            regime_labels = new_hmm.predict_regime_filtered(feature_matrix)
            scaled_features = new_hmm.scaler.transform(feature_matrix)
            close_prices = np.array(
                [b.close for b in bars[-len(feature_matrix):]], dtype=float
            )

            # Fit new LGBM experts
            new_trainer = LGBMExpertTrainer(self._config, asset)
            new_trainer.train_all(
                feature_matrix=scaled_features,
                regime_labels=regime_labels,
                feature_names=feature_names,
                close_prices=close_prices,
            )

            new_router = LGBMExpertRouter(self._config, asset)
            new_router.load_from_trainer(new_trainer)

            # Atomic swap
            self._hmm_engines[asset] = new_hmm
            self._lgbm_trainers[asset] = new_trainer
            self._lgbm_routers[asset] = new_router

            # Persist to disk
            os.makedirs(_MODEL_DIR, exist_ok=True)
            new_hmm.save(
                os.path.join(_MODEL_DIR, f"{asset.replace('/', '_')}_hmm.pkl")
            )
            new_trainer.save_all(_MODEL_DIR)

            logger.info("RETRAIN_COMPLETE [%s]", asset)

        except Exception:
            logger.error("RETRAIN_FAILED [%s]:\n%s", asset, traceback.format_exc())
        finally:
            lock.release()

    # ------------------------------------------------------------------
    # Initial model load from disk
    # ------------------------------------------------------------------

    def _initial_model_load(self, asset: str) -> None:
        """Try to load persisted models. Log an info message if not found."""
        hmm_path = os.path.join(_MODEL_DIR, f"{asset.replace('/', '_')}_hmm.pkl")
        if not os.path.exists(hmm_path):
            logger.info(
                "No saved HMM for %s at %s — will activate after first retrain window",
                asset, hmm_path,
            )
            return

        try:
            self._hmm_engines[asset].load(hmm_path)
            logger.info("HMM loaded: %s", hmm_path)
        except Exception:
            logger.warning(
                "HMM load failed for %s:\n%s", asset, traceback.format_exc()
            )
            return

        # Load LGBM experts
        try:
            self._lgbm_trainers[asset].load_all(_MODEL_DIR, list(RegimeLabel))
            self._lgbm_routers[asset].load_from_trainer(self._lgbm_trainers[asset])
            logger.info("LGBM experts loaded for %s", asset)
        except Exception:
            logger.warning(
                "LGBM load failed for %s (predictions unavailable until retrain):\n%s",
                asset, traceback.format_exc(),
            )

    # ------------------------------------------------------------------
    # Main async run loop
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Load models, subscribe to bars, then process the bar queue."""
        # Capture the running loop so _enqueue_bar can use call_soon_threadsafe
        self._loop: asyncio.AbstractEventLoop = asyncio.get_running_loop()

        # Deferred broker import: keeps --help clean without a .env file
        from src.broker.alpaca_client import AlpacaClient
        from src.broker.broker_executor import BrokerExecutor

        self._alpaca_client = AlpacaClient(self._config)
        self._executor_broker = BrokerExecutor(self._alpaca_client, self._config)

        _print_banner(self._config, self._alpaca_client.is_paper)

        # Initial portfolio snapshot
        self._portfolio_state = self._refresh_portfolio_state()
        if self._portfolio_state is None:
            raise RuntimeError(
                "Could not fetch account state from Alpaca — check credentials."
            )

        self._structured_logger.log_session_open(
            assets=self._assets,
            equity=self._portfolio_state.equity,
            timestamp=datetime.now(timezone.utc),
        )

        # Attempt to warm up from persisted models
        for asset in self._assets:
            self._initial_model_load(asset)

        # Subscribe bar callback and start streaming threads
        self._alpaca_client.subscribe_bars(
            symbols=self._assets,
            bar_size=self._config.get("features", {}).get("bar_size", "5Min"),
            callback=self._enqueue_bar,
        )
        self._alpaca_client.start_streaming()

        logger.info("Streaming started — waiting for bars …")
        self._running = True

        # Bar dispatch loop
        while self._running:
            try:
                bar: BarData = await asyncio.wait_for(
                    self._bar_queue.get(), timeout=1.0
                )
                try:
                    await self.on_bar(bar)
                except Exception:
                    logger.error(
                        "on_bar error [%s]:\n%s", bar.symbol, traceback.format_exc()
                    )
                finally:
                    self._bar_queue.task_done()
            except asyncio.TimeoutError:
                continue  # no bar yet — check _running and loop
            except asyncio.CancelledError:
                break

        logger.info("Main loop exited.")

    # ------------------------------------------------------------------
    # Graceful shutdown
    # ------------------------------------------------------------------

    def shutdown(self) -> None:
        """Stop streaming, close all positions, flush logs."""
        logger.warning("SHUTDOWN_INITIATED")
        self._running = False

        if self._alpaca_client is not None:
            try:
                self._alpaca_client.stop_streaming()
            except Exception:
                logger.debug("stop_streaming error (ignored):\n%s", traceback.format_exc())

        if self._executor_broker is not None:
            try:
                closed = self._executor_broker.close_all_positions(ExitReason.MANUAL)
                logger.info("Closed %d position(s) on shutdown.", len(closed))
            except Exception:
                logger.error(
                    "close_all_positions failed:\n%s", traceback.format_exc()
                )

        if self._portfolio_state is not None:
            try:
                self._structured_logger.log_session_close(
                    trades_today=len(self._equity_curve),
                    pnl=self._portfolio_state.daily_pnl,
                    equity=self._portfolio_state.equity,
                    timestamp=datetime.now(timezone.utc),
                )
            except Exception:
                pass
            try:
                self._reporter.on_session_close(
                    today=datetime.now(timezone.utc).date(),
                    equity_end=self._portfolio_state.equity,
                    equity_start_of_day=self._portfolio_state.session_open_equity,
                )
            except Exception:
                logger.error(
                    "PerformanceReporter.on_session_close failed:\n%s",
                    traceback.format_exc(),
                )

        try:
            self._structured_logger.flush()
            self._structured_logger.close()
        except Exception:
            pass

        self._executor.shutdown(wait=False)
        logger.warning("SHUTDOWN_COMPLETE")


# ---------------------------------------------------------------------------
# Signal handlers
# ---------------------------------------------------------------------------

_trader_ref: Optional[MLTrader] = None


def _signal_handler(signum, frame) -> None:  # noqa: ANN001
    sig_name = (
        signal.Signals(signum).name if hasattr(signal, "Signals") else str(signum)
    )
    print(f"\nReceived {sig_name} — shutting down …", file=sys.stderr)
    if _trader_ref is not None:
        _trader_ref.shutdown()
    sys.exit(0)


# ---------------------------------------------------------------------------
# CLI argument parser
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python main.py",
        description=(
            "ML Trader — Diablo v1  |  HMM + LightGBM, Alpaca broker\n"
            "  Paper trading mode by default (set ALPACA_BASE_URL to override)."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--paper",
        action="store_true",
        default=True,
        help="Run in paper-trading mode (default). "
             "Overridden automatically by ALPACA_BASE_URL env var.",
    )
    parser.add_argument(
        "--backtest-only",
        action="store_true",
        default=False,
        help="Run the walk-forward backtest CLI instead of the live loop "
             "(delegates to src/backtest/cli.py).",
    )
    parser.add_argument(
        "--config",
        default="config/settings.yaml",
        metavar="PATH",
        help="Path to settings.yaml  (default: config/settings.yaml).",
    )
    parser.add_argument(
        "--assets",
        nargs="+",
        metavar="SYMBOL",
        help="Override asset list from settings.yaml  (e.g. --assets SPY QQQ BTC/USD).",
    )
    return parser.parse_args()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    args = _parse_args()

    # Delegate to backtest CLI if requested
    if args.backtest_only:
        from src.backtest.cli import main as backtest_main

        sys.exit(backtest_main())

    # Load + validate config
    config = load_config(args.config)
    validate_config(config)

    # Asset override (CLI wins over config, but still subject to the guard below)
    if args.assets:
        config["assets"]["primary_equity"] = [a for a in args.assets if "/" not in a]
        config["assets"]["primary_crypto"] = [a for a in args.assets if "/" in a]

    # Enforce SPY-only universe before any broker connection is made
    _assert_spy_only(config)

    # Build trader (no .env required until run())
    trader = MLTrader(config)

    # Register OS signal handlers
    _trader_ref = trader
    signal.signal(signal.SIGINT, _signal_handler)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _signal_handler)

    # Run the async event loop
    try:
        asyncio.run(trader.run())
    except KeyboardInterrupt:
        trader.shutdown()
