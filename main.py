"""
ML Trader — Diablo v1 — Main Trading Loop
Architecture: HMM Regime Detection + LightGBM Expert Classifiers
Broker: Alpaca Paper Trading API
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import concurrent.futures
import logging
import os
import signal
import sys
import threading
import traceback
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

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
from src.monitoring.performance_reporter import PerformanceReporter, _is_last_trading_day_of_month as _is_last_trading_day_of_month_safe
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
        self._last_eod_report_date: Optional[date] = None
        self._session_trade_count: int = 0

        # Long-lived bar archive for HMM/LGBM training — kept across session
        # resets (the FeatureEngineer._history only holds 36 bars; this holds
        # enough history to satisfy min_train_bars across multiple trading days)
        _train_archive_maxlen: int = 2000   # ~5 trading days of 1-min bars
        self._bar_archives: dict[str, collections.deque] = {}

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

        self._archive_path: str = os.path.join(
            config["monitoring"]["log_dir"], "bar_archives.pkl"
        )
        self._feature_history_path: str = os.path.join(
            config["monitoring"]["log_dir"], "feature_history.pkl"
        )

        for asset in all_assets:
            ac = AssetClass.from_symbol(asset)
            self._hmm_engines[asset] = HMMEngine(config, asset)
            self._feature_engineers[asset] = FeatureEngineer(config, ac)
            self._lgbm_trainers[asset] = LGBMExpertTrainer(config, asset)
            self._lgbm_routers[asset] = LGBMExpertRouter(config, asset)
            self._retrain_locks[asset] = threading.Lock()
            self._retrain_bar_counters[asset] = 0
            self._bar_archives[asset] = collections.deque(maxlen=_train_archive_maxlen)

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
        min_train = self._config["hmm"].get("min_train_bars", 390)
        archive_sizes = {a: len(self._bar_archives.get(a, [])) for a in self._assets}
        total_archive = sum(archive_sizes.values())
        # Source-of-truth: HMM is trained when every asset's engine has been
        # fitted. Reading is_trained directly avoids the chicken-and-egg where
        # the dashboard waits for inference output that is itself blocked on
        # the feature engineer's per-session warmup.
        all_hmm_trained = all(self._hmm_engines[a].is_trained for a in self._assets)
        # Per-asset feature engineer warmup status — surfaced to the dashboard
        # so the operator can see why inference hasn't started yet.
        feature_warmup = {
            a: {
                "bars": self._feature_engineers[a].bars_in_history,
                "needed": self._feature_engineers[a].min_history_bars,
                "ready": self._feature_engineers[a].is_warmed_up,
            }
            for a in self._assets
        }
        self._structured_logger.update_shared_state(
            portfolio_state=self._portfolio_state,
            regime_info=regime_info,
            signal_history=self._orchestrator.get_signal_history(),
            equity_curve=self._equity_curve,
            training_bars=total_archive,
            training_needed=min_train,
            hmm_trained=all_hmm_trained,
            feature_warmup=feature_warmup,
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
            self._session_trade_count = 0
            self._equity_curve = []

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
        self._bar_archives[asset].append(bar)  # cross-session archive for retraining

        # ----------------------------------------------------------------
        # Refresh portfolio state from Alpaca on every bar so the dashboard
        # reflects real-time equity / cash / positions — including externally
        # placed orders (e.g. smoke-test trades). One API call per bar is
        # well within Alpaca's 200 req/min paper rate limit.
        # ----------------------------------------------------------------
        refreshed = self._refresh_portfolio_state()
        if refreshed is not None:
            self._portfolio_state = refreshed

        # ----------------------------------------------------------------
        # Feature engineer update — must happen on every bar (including
        # pre-training) so rolling windows accumulate continuously.
        # ----------------------------------------------------------------
        self._feature_engineers[asset].update(bar)
        bars_since_open = self._session_manager.get_bars_since_open(asset)
        raw_features = self._feature_engineers[asset].compute_features(bars_since_open)

        # ----------------------------------------------------------------
        # HMM training trigger — checked BEFORE the raw_features early-return
        # so initial training can fire even while the per-session feature
        # engineer is still warming up. Training uses the historical bar
        # archive, not the current bar's computed features.
        # ----------------------------------------------------------------
        hmm = self._hmm_engines[asset]
        if not hmm.is_trained:
            archive_len = len(self._bar_archives[asset])
            min_bars = self._config["hmm"].get("min_train_bars", 390)
            if archive_len >= min_bars:
                # _retrain_background uses a non-blocking lock so redundant
                # submissions from subsequent bars are immediately discarded.
                asyncio.get_event_loop().run_in_executor(
                    self._executor, self._retrain_background, asset
                )
            self._last_bar_time[asset] = now
            self._emit_state(now)
            return

        # Once trained, we need raw_features to feed HMM inference
        if raw_features is None:
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
                    self._session_trade_count += 1
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
        # EOD performance report — fast path. Fires when a bar arrives in
        # the hard-close window (≥ 15:55 ET). The reporter's persistent
        # state file is the source of truth for "did this fire?", so we
        # never double-send. The async _eod_scheduler_loop is the safety
        # net at 16:05 ET if no bar arrives during the hard-close window.
        # ----------------------------------------------------------------
        eod_today = now.date()
        if (
            self._session_manager.is_eod_hard_close(asset, now)
            and not self._reporter.was_daily_sent_today(eod_today)
            and self._portfolio_state is not None
        ):
            try:
                self._reporter.on_session_close(
                    today=eod_today,
                    equity_end=self._portfolio_state.equity,
                    equity_start_of_day=self._portfolio_state.session_open_equity,
                    engine_state=self._build_engine_state_for_email(),
                )
                self._last_eod_report_date = eod_today
            except Exception:
                logger.error("EOD report failed:\n%s", traceback.format_exc())

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

            bars = list(self._bar_archives[asset])
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

            # MAP regime labels for LGBM supervision.
            # predict_regime_filtered returns alpha of shape (T, n_states) — a
            # probability matrix. Argmax gives the most likely state per bar,
            # which we map through RegimeLabel.from_index to get enum labels.
            alpha = new_hmm.predict_regime_filtered(feature_matrix)
            state_indices = alpha.argmax(axis=1)
            n_states = new_hmm.model.n_components
            regime_labels = np.array(
                [RegimeLabel.from_index(int(i), n_states) for i in state_indices],
                dtype=object,
            )
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
    # Bar-archive persistence
    # ------------------------------------------------------------------

    def _save_bar_archives(self) -> None:
        """Write _bar_archives to disk so restarts don't lose training history."""
        import pickle
        tmp = self._archive_path + ".tmp"
        try:
            os.makedirs(os.path.dirname(self._archive_path) or ".", exist_ok=True)
            payload = {a: list(dq) for a, dq in self._bar_archives.items()}
            with open(tmp, "wb") as fh:
                pickle.dump(payload, fh, protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(tmp, self._archive_path)
            total = sum(len(v) for v in payload.values())
            logger.info(
                "Bar archives saved: %d bars across %d asset(s) → %s",
                total, len(payload), self._archive_path,
            )
        except Exception:
            logger.warning("_save_bar_archives failed:\n%s", traceback.format_exc())

    def _load_bar_archives(self) -> None:
        """Restore _bar_archives from disk on startup."""
        import pickle
        if not os.path.exists(self._archive_path):
            logger.info("No saved bar archives at %s — starting fresh", self._archive_path)
            return
        try:
            with open(self._archive_path, "rb") as fh:
                payload: dict = pickle.load(fh)
            for asset, bars in payload.items():
                if asset in self._bar_archives:
                    self._bar_archives[asset].extend(bars)
                    logger.info(
                        "Bar archive restored: %s — %d bars", asset, len(self._bar_archives[asset])
                    )
        except Exception:
            logger.warning("_load_bar_archives failed:\n%s", traceback.format_exc())

    # ------------------------------------------------------------------
    # Feature engineer history persistence
    # ------------------------------------------------------------------

    def _save_feature_history(self) -> None:
        """Persist each FeatureEngineer's rolling bar window so warmup
        survives container restarts."""
        import pickle
        tmp = self._feature_history_path + ".tmp"
        try:
            os.makedirs(os.path.dirname(self._feature_history_path) or ".", exist_ok=True)
            payload = {a: fe.get_history() for a, fe in self._feature_engineers.items()}
            with open(tmp, "wb") as fh:
                pickle.dump(payload, fh, protocol=pickle.HIGHEST_PROTOCOL)
            os.replace(tmp, self._feature_history_path)
            total = sum(len(v) for v in payload.values())
            logger.info(
                "Feature history saved: %d bars across %d asset(s) → %s",
                total, len(payload), self._feature_history_path,
            )
        except Exception:
            logger.warning("_save_feature_history failed:\n%s", traceback.format_exc())

    # ------------------------------------------------------------------
    # EOD email — async safety net
    # ------------------------------------------------------------------

    def _seconds_until_next_eod_check(self) -> int:
        """Returns seconds until 16:05 ET on the next weekday."""
        et_now = datetime.now(ZoneInfo("America/New_York"))
        target = et_now.replace(hour=16, minute=5, second=0, microsecond=0)
        if et_now >= target:
            target += timedelta(days=1)
        # Skip weekends (5 = Sat, 6 = Sun)
        while target.weekday() >= 5:
            target += timedelta(days=1)
        delta = (target - et_now).total_seconds()
        return max(60, int(delta))

    def _build_engine_state_for_email(self) -> dict:
        """Snapshot of engine state for inclusion in the daily email."""
        per_asset: dict = {}
        for asset, hmm in self._hmm_engines.items():
            regime = hmm.get_current_regime().value if hmm.is_trained else "UNKNOWN"
            conf   = hmm.get_confidence() if hmm.is_trained else 0.0
            fe = self._feature_engineers.get(asset)
            warmup = (
                f"{fe.bars_in_history}/{fe.min_history_bars}"
                if fe else "?"
            )
            per_asset[asset] = {
                "regime":     regime,
                "confidence": conf,
                "warmup":     warmup,
            }

        # Today's signal histogram
        sigs = []
        try:
            sigs = list(self._orchestrator.get_signal_history())
        except Exception:
            pass
        today_iso = datetime.now(timezone.utc).date().isoformat()
        today_sigs = [s for s in sigs if str(s.get("ts", "")).startswith(today_iso)]
        reasons: dict[str, int] = {}
        for s in today_sigs:
            r = s.get("reason", "?")
            reasons[r] = reasons.get(r, 0) + 1

        # Uptime
        uptime = "?"
        try:
            started = getattr(self, "_started_at", None)
            if started:
                delta = datetime.now(timezone.utc) - started
                hours = int(delta.total_seconds() // 3600)
                mins  = int((delta.total_seconds() % 3600) // 60)
                uptime = f"{hours}h {mins:02d}m"
        except Exception:
            pass

        return {
            "hmm_trained":            all(h.is_trained for h in self._hmm_engines.values()),
            "bars_archived":          sum(len(d) for d in self._bar_archives.values()),
            "bars_today":             sum(self._session_manager.get_bars_since_open(a) for a in self._assets),
            "signals_today":          len(today_sigs),
            "signal_reasons":         reasons,
            "open_positions":         len(self._portfolio_state.positions) if self._portfolio_state else 0,
            "circuit_breaker_active": self._circuit_breaker.is_active() if self._circuit_breaker else False,
            "per_asset":              per_asset,
            "uptime":                 uptime,
        }

    async def _eod_scheduler_loop(self) -> None:
        """Wake up at 16:05 ET each weekday and ensure the daily email fired.

        This is the safety net for the bar-based fast-path trigger. Even if no
        bar arrives during the hard-close window (websocket reconnect, network
        blip, Alpaca data delay), the daily email goes out within 5 minutes
        of market close.
        """
        logger.info("EOD scheduler started — daily email guaranteed at 16:05 ET")
        while self._running:
            try:
                sleep_secs = self._seconds_until_next_eod_check()
                logger.info(
                    "EOD scheduler: next check in %d seconds (%.1f hours)",
                    sleep_secs, sleep_secs / 3600,
                )
                await asyncio.sleep(sleep_secs)
                if not self._running:
                    break

                today = datetime.now(timezone.utc).date()

                # Skip the entire pass only if EVERY report type that's due
                # today has already been sent — otherwise on_session_close
                # will internally skip what's done and retry what's missing.
                daily_done   = self._reporter.was_daily_sent_today(today)
                weekly_due   = today.weekday() == 4
                weekly_done  = (not weekly_due) or self._reporter.was_weekly_sent_for_isoweek(today)
                monthly_due  = _is_last_trading_day_of_month_safe(today)
                monthly_done = (not monthly_due) or self._reporter.was_monthly_sent_for_month(today)

                if daily_done and weekly_done and monthly_done:
                    logger.info(
                        "EOD scheduler: all due reports already sent (daily=%s weekly=%s monthly=%s) — skipping",
                        daily_done,
                        "n/a" if not weekly_due else weekly_done,
                        "n/a" if not monthly_due else monthly_done,
                    )
                    continue

                # Refresh portfolio state — retry up to 3× if Alpaca momentarily down
                ps = None
                for attempt in range(3):
                    ps = self._refresh_portfolio_state()
                    if ps is not None:
                        break
                    logger.warning(
                        "EOD scheduler: portfolio refresh failed (attempt %d/3), retrying in 30s",
                        attempt + 1,
                    )
                    await asyncio.sleep(30)

                if ps is None:
                    logger.error("EOD scheduler: cannot refresh portfolio — skipping email")
                    continue

                self._portfolio_state = ps
                logger.info(
                    "EOD scheduler: firing on_session_close — equity=$%.2f, day_open=$%.2f "
                    "(daily_done=%s weekly_due=%s weekly_done=%s monthly_due=%s monthly_done=%s)",
                    ps.equity, ps.session_open_equity,
                    daily_done, weekly_due, weekly_done, monthly_due, monthly_done,
                )
                self._reporter.on_session_close(
                    today=today,
                    equity_end=ps.equity,
                    equity_start_of_day=ps.session_open_equity,
                    engine_state=self._build_engine_state_for_email(),
                )
                self._last_eod_report_date = today
            except asyncio.CancelledError:
                logger.info("EOD scheduler cancelled — exiting cleanly")
                break
            except Exception:
                logger.error(
                    "EOD scheduler error — backing off 5 minutes:\n%s",
                    traceback.format_exc(),
                )
                await asyncio.sleep(300)

    def _load_feature_history(self) -> None:
        """Restore each FeatureEngineer's rolling bar window from disk."""
        import pickle
        if not os.path.exists(self._feature_history_path):
            logger.info(
                "No saved feature history at %s — warmup starts fresh",
                self._feature_history_path,
            )
            return
        try:
            with open(self._feature_history_path, "rb") as fh:
                payload: dict = pickle.load(fh)
            for asset, bars in payload.items():
                if asset in self._feature_engineers:
                    self._feature_engineers[asset].restore_history(bars)
                    logger.info(
                        "Feature history restored: %s — %d bars (warmup needs %d)",
                        asset,
                        self._feature_engineers[asset].bars_in_history,
                        self._feature_engineers[asset].min_history_bars,
                    )
        except Exception:
            logger.warning("_load_feature_history failed:\n%s", traceback.format_exc())

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

        # Attempt to warm up from persisted models
        for asset in self._assets:
            self._initial_model_load(asset)

        # Restore bar archives so training history survives restarts
        self._load_bar_archives()
        # Restore feature engineer rolling windows so warmup survives restarts
        self._load_feature_history()

        # Subscribe bar callback and start streaming threads
        self._alpaca_client.subscribe_bars(
            symbols=self._assets,
            bar_size=self._config.get("features", {}).get("bar_size", "5Min"),
            callback=self._enqueue_bar,
        )
        self._alpaca_client.start_streaming()

        logger.info("Streaming started — waiting for bars …")
        self._running = True
        self._started_at = datetime.now(timezone.utc)

        # Spawn EOD safety-net scheduler — guarantees daily email at 16:05 ET
        self._eod_scheduler_task: Optional[asyncio.Task] = asyncio.create_task(
            self._eod_scheduler_loop()
        )

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

        # Cancel the EOD scheduler so it doesn't keep waiting on its sleep
        eod_task = getattr(self, "_eod_scheduler_task", None)
        if eod_task is not None and not eod_task.done():
            eod_task.cancel()

        # Persist bar archives before anything else so a crash-shutdown
        # doesn't lose the training history collected since last restart.
        self._save_bar_archives()
        # Persist feature engineer rolling windows so warmup is not lost
        self._save_feature_history()

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
                    trades_today=self._session_trade_count,
                    pnl=self._portfolio_state.daily_pnl,
                    equity=self._portfolio_state.equity,
                    timestamp=datetime.now(timezone.utc),
                )
            except Exception:
                pass
            # Only send shutdown report if today's daily email hasn't been sent.
            # Source-of-truth is the reporter's persistent state — survives
            # restarts so we don't double-send across reboots.
            shutdown_today = datetime.now(timezone.utc).date()
            if not self._reporter.was_daily_sent_today(shutdown_today):
                try:
                    self._reporter.on_session_close(
                        today=shutdown_today,
                        equity_end=self._portfolio_state.equity,
                        equity_start_of_day=self._portfolio_state.session_open_equity,
                        engine_state=self._build_engine_state_for_email(),
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
