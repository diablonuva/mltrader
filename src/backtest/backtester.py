from __future__ import annotations

import logging
import math
import random
from datetime import datetime, timezone
from typing import Optional

import numpy as np

from src.allocation.position_sizer import ATRPositionSizer
from src.brain.feature_engineering import FeatureEngineer
from src.brain.hmm_engine import HMMEngine
from src.brain.lgbm_experts import LGBMExpertRouter, LGBMExpertTrainer
from src.brain.opening_range import OpeningRange
from src.brain.regime_strategies import StrategyOrchestrator
from src.models import (
    AssetClass,
    BacktestResults,
    BarData,
    CompletedTrade,
    Direction,
    ExitReason,
    PortfolioState,
    Position,
    RegimeLabel,
)
from src.risk.circuit_breaker import CircuitBreaker
from src.risk.pdt_guard import PDTGuard
from src.risk.risk_manager import RiskManager
from src.session.session_manager import SessionManager

logger = logging.getLogger(__name__)


class WalkForwardBacktester:
    """Replays historical 5-min bars through the full signal + risk pipeline.

    Each walk-forward window independently trains HMM + LightGBM on
    ``train_bars``, then runs bar-by-bar simulation over the ``test_bars``.
    No look-ahead: the scaler, HMM, and LGBM are all fitted on training data
    only before the test window begins.
    """

    def __init__(self, config: dict) -> None:
        self._config = config
        self._session_manager = SessionManager(config)
        self._position_sizer = ATRPositionSizer(config)

        # PDT guard with a backtest-specific counter file (won't collide with live)
        bt_pdt_cfg = dict(config["pdt"])
        bt_pdt_cfg["pdt_counter_file"] = "logs/pdt_backtest.json"
        self._pdt_guard = PDTGuard({"pdt": bt_pdt_cfg})

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

        # Per-asset engine instances (populated in run())
        self._hmm_engines: dict[str, HMMEngine] = {}
        self._feature_engineers: dict[str, FeatureEngineer] = {}
        self._lgbm_trainers: dict[str, LGBMExpertTrainer] = {}
        self._lgbm_routers: dict[str, LGBMExpertRouter] = {}
        self._or_trackers: dict[str, Optional[OpeningRange]] = {}

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run(
        self,
        bar_data: dict[str, list[BarData]],
        start_date: datetime,
        end_date: datetime,
        regime_noise_pct: float = 0.0,
        slippage_multiplier: float = 1.0,
        commission_multiplier: float = 1.0,
        fill_delay_bars: int = 0,
    ) -> BacktestResults:
        assets = list(bar_data.keys())

        # Initialise per-asset engines
        for asset in assets:
            ac = AssetClass.from_symbol(asset)
            self._hmm_engines[asset] = HMMEngine(self._config, asset)
            self._feature_engineers[asset] = FeatureEngineer(self._config, ac)
            self._lgbm_trainers[asset] = LGBMExpertTrainer(self._config, asset)
            self._lgbm_routers[asset] = LGBMExpertRouter(self._config, asset)
            self._or_trackers[asset] = (
                OpeningRange(self._config["features"].get("opening_range_bars", 6))
                if ac is AssetClass.EQUITY
                else None
            )

        # Window parameters: use equity settings when any equity asset present
        has_equity = any(
            AssetClass.from_symbol(a) is AssetClass.EQUITY for a in assets
        )
        bt = self._config["backtest"]
        if has_equity:
            train_bars = bt["train_bars_equity"]
            test_bars_w = bt["test_bars_equity"]
            step_bars = bt["step_bars_equity"]
        else:
            train_bars = bt["train_bars_crypto"]
            test_bars_w = bt["test_bars_crypto"]
            step_bars = bt["step_bars_crypto"]

        primary_asset = assets[0]
        n_bars = len(bar_data[primary_asset])

        sim_portfolio = self._build_sim_portfolio()
        all_trades: list[CompletedTrade] = []
        equity_curve: list[tuple] = []
        bar_results: list[dict] = []
        per_window_results: list[dict] = []

        window_start = 0
        window_num = 0

        while window_start + train_bars + test_bars_w <= n_bars:
            train_end = window_start + train_bars
            test_end = train_end + test_bars_w

            # ----------------------------------------------------------
            # TRAINING PHASE
            # ----------------------------------------------------------
            trained_assets: set[str] = set()
            for asset in assets:
                ac = AssetClass.from_symbol(asset)
                asset_bars = bar_data[asset]
                if train_end > len(asset_bars):
                    continue
                train_slice = asset_bars[window_start:train_end]

                feature_matrix, close_prices = self._build_feature_matrix_with_closes(
                    train_slice, asset, ac
                )
                min_train = self._config["hmm"].get("min_train_bars", 1000)
                if len(feature_matrix) < min_train:
                    logger.warning(
                        "[%s] Window %d: only %d feature rows < %d required — skipping",
                        asset, window_num, len(feature_matrix), min_train,
                    )
                    continue

                feature_names = self._feature_engineers[asset].get_feature_names()

                # Fit scaler and store a fresh FeatureEngineer
                fe_train = self._feature_engineers[asset]
                fe_train.fit_scaler(feature_matrix)

                # Train HMM
                try:
                    hmm = self._hmm_engines[asset]
                    hmm.train(feature_matrix, feature_names, train_slice[-1].timestamp)
                except Exception as exc:
                    logger.warning("[%s] HMM training failed: %s", asset, exc)
                    continue

                # Regime labels via forward-argmax (causal)
                alpha = hmm.predict_regime_filtered(feature_matrix)
                regime_labels = np.array(
                    [hmm.state_labels[int(np.argmax(alpha[i]))] for i in range(len(alpha))]
                )

                # Train LGBM
                trainer = self._lgbm_trainers[asset]
                try:
                    trainer.train_all(
                        feature_matrix, regime_labels, feature_names, close_prices
                    )
                except Exception as exc:
                    logger.warning("[%s] LGBM training failed: %s", asset, exc)

                self._lgbm_routers[asset].load_from_trainer(trainer)

                # Reset online HMM state before test phase
                hmm._alpha_current = None

                trained_assets.add(asset)
                logger.info(
                    "[%s] Window %d trained: %d feature rows",
                    asset, window_num, len(feature_matrix),
                )

            # ----------------------------------------------------------
            # TEST PHASE — bar-by-bar, no re-fitting
            # ----------------------------------------------------------
            bars_since_open: dict[str, int] = {a: 0 for a in assets}
            last_bar_date: dict[str, object] = {a: None for a in assets}
            window_trades: list[CompletedTrade] = []

            # Merge bars from all assets in timestamp order
            merged: list[tuple] = []
            for asset in assets:
                asset_bars = bar_data[asset]
                for bar in asset_bars[train_end:test_end]:
                    merged.append((bar.timestamp, asset, bar))
            merged.sort(key=lambda x: x[0])

            for ts, asset, bar in merged:
                if asset not in trained_assets:
                    continue  # skip untrained assets

                ac = AssetClass.from_symbol(asset)
                fe = self._feature_engineers[asset]
                hmm = self._hmm_engines[asset]
                router = self._lgbm_routers[asset]

                # Detect new session
                bar_date = ts.date() if hasattr(ts, "date") else ts
                if last_bar_date[asset] is None or bar_date != last_bar_date[asset]:
                    bars_since_open[asset] = 0
                    fe.reset_session()
                    hmm._alpha_current = None
                    if ac is AssetClass.EQUITY:
                        sim_portfolio.session_open_equity = sim_portfolio.equity
                    self._circuit_breaker.reset_session()
                    self._risk_manager.reset_daily_counts()
                last_bar_date[asset] = bar_date

                fe.update(bar)
                raw_features = fe.compute_features(bars_since_open[asset])

                if raw_features is None or not fe.is_scaler_fitted:
                    bars_since_open[asset] += 1
                    equity_curve.append((ts, sim_portfolio.equity))
                    continue

                # HMM online step
                hmm.step(raw_features)
                regime = hmm.get_current_regime()
                confidence = hmm.get_confidence()
                flickering = hmm.is_flickering()
                confirmed = hmm.is_confirmed()

                # Regime noise injection for stress testing
                if regime_noise_pct > 0 and random.random() < regime_noise_pct:
                    regime = random.choice(list(RegimeLabel))

                # LGBM inference
                try:
                    scaled_features = fe.transform(raw_features)
                except RuntimeError:
                    bars_since_open[asset] += 1
                    equity_curve.append((ts, sim_portfolio.equity))
                    continue

                lgbm_direction, lgbm_conf = router.predict(regime, scaled_features)

                feature_names = fe.get_feature_names()
                feature_dict = dict(zip(feature_names, raw_features.tolist()))

                or_tracker = self._or_trackers.get(asset)
                if or_tracker is not None:
                    or_tracker.update(bar, bars_since_open[asset])

                # Strategy signal
                signal = None
                try:
                    signal = self._orchestrator.select_strategy(
                        regime=regime,
                        hmm_confidence=confidence,
                        hmm_is_flickering=flickering,
                        hmm_is_confirmed=confirmed,
                        lgbm_direction=lgbm_direction,
                        lgbm_confidence=lgbm_conf,
                        feature_dict=feature_dict,
                        asset=asset,
                        asset_class=ac,
                        current_bar=bar,
                        recent_bars=list(fe._history),
                        vwap=fe._vwap.get_vwap(),
                        or_tracker=or_tracker,
                        bars_since_open=bars_since_open[asset],
                        portfolio_state=sim_portfolio,
                        now=ts,
                    )
                except Exception as exc:
                    logger.debug("[%s] select_strategy error: %s", asset, exc)

                # Entry: only if no existing position for this asset
                if signal is not None and asset not in sim_portfolio.positions:
                    try:
                        risk_dec = self._risk_manager.evaluate(
                            signal, sim_portfolio, ts
                        )
                    except Exception as exc:
                        logger.debug("[%s] risk evaluate error: %s", asset, exc)
                        risk_dec = None

                    if risk_dec is not None and risk_dec.approved:
                        size_pct = (
                            risk_dec.modifications.get("size_pct", signal.size_pct)
                            if risk_dec.modified
                            else signal.size_pct
                        )
                        fill_price = self._simulate_fill(
                            signal, slippage_mult=slippage_multiplier
                        )
                        shares = math.floor(
                            size_pct * sim_portfolio.equity / fill_price
                        ) if fill_price > 0 else 0

                        if shares > 0:
                            commission = self._compute_commission(
                                asset, shares * fill_price,
                                comm_mult=commission_multiplier,
                            )
                            sim_portfolio.cash -= shares * fill_price + commission
                            tp1_shares = math.floor(float(shares) * signal.tp1_shares_pct)
                            trailing_cfg = self._config["strategy"]
                            sim_portfolio.positions[asset] = Position(
                                asset=asset,
                                direction=signal.direction,
                                entry_price=fill_price,
                                current_price=fill_price,
                                shares=float(shares),
                                entry_time=ts,
                                stop_price=signal.stop_price,
                                take_profit_price=signal.take_profit_price,
                                max_hold_bars=signal.max_hold_bars,
                                bars_held=0,
                                stop_order_id="",
                                strategy_name=signal.strategy_name,
                                regime_at_entry=regime,
                                # Phase 1 — trailing stop
                                atr_at_entry=signal.atr_at_entry,
                                trailing_stop_enabled=trailing_cfg.get(
                                    "trailing_stop_enabled", False
                                ),
                                highest_price_since_entry=fill_price,
                                # Phase 3 — partial TP
                                original_shares=float(shares),
                                tp1_price=signal.tp1_price,
                                tp1_shares=float(tp1_shares),
                            )
                            self._risk_manager.record_trade_open(asset)

                # Position management for this asset's bar
                if asset in sim_portfolio.positions:
                    pos = sim_portfolio.positions[asset]
                    pos.current_price = bar.close
                    pos.bars_held += 1

                    # Phase 1 — update trailing stop each bar
                    if pos.trailing_stop_enabled and pos.atr_at_entry > 0:
                        trail_mult = float(
                            self._config["strategy"].get("trailing_stop_atr_mult", 2.0)
                        )
                        if pos.direction is Direction.LONG:
                            if bar.close > pos.highest_price_since_entry:
                                pos.highest_price_since_entry = bar.close
                            trail_stop = (
                                pos.highest_price_since_entry
                                - trail_mult * pos.atr_at_entry
                            )
                            if trail_stop > pos.stop_price:
                                pos.stop_price = trail_stop

                    # Phase 3 — partial TP1 check (before full-exit check)
                    if (
                        not pos.tp1_triggered
                        and pos.tp1_price > 0
                        and pos.tp1_shares > 0
                    ):
                        tp1_hit = (
                            pos.direction is Direction.LONG
                            and bar.high >= pos.tp1_price
                        ) or (
                            pos.direction is Direction.SHORT
                            and bar.low <= pos.tp1_price
                        )
                        if tp1_hit:
                            partial_proceeds = pos.tp1_shares * pos.tp1_price
                            sim_portfolio.cash += partial_proceeds
                            pos.shares -= pos.tp1_shares
                            pos.realized_partial_pnl_dollar += (
                                pos.tp1_price - pos.entry_price
                            ) * pos.tp1_shares
                            pos.tp1_triggered = True
                            # Move stop to entry + buffer (not exact BE) so a
                            # 1-bar pullback after TP1 can't stop the remainder flat.
                            # Buffer defaults to 0.25×ATR; 0 = original exact-BE behaviour.
                            be_buffer = float(
                                self._config["strategy"].get("tp1_be_buffer_atr", 0.0)
                            )
                            if pos.direction is Direction.LONG:
                                new_stop = pos.entry_price + be_buffer * pos.atr_at_entry
                            else:
                                new_stop = pos.entry_price - be_buffer * pos.atr_at_entry
                            # Only raise (never lower) the stop
                            if new_stop > pos.stop_price:
                                pos.stop_price = new_stop
                            # Record the partial close as a trade entry
                            partial_pnl_pct = (
                                (pos.tp1_price - pos.entry_price) / pos.entry_price
                                if pos.direction is Direction.LONG
                                else (pos.entry_price - pos.tp1_price) / pos.entry_price
                            )
                            partial_trade = CompletedTrade(
                                asset=asset,
                                direction=pos.direction,
                                entry_price=pos.entry_price,
                                exit_price=pos.tp1_price,
                                shares=pos.tp1_shares,
                                entry_time=pos.entry_time,
                                exit_time=ts,
                                pnl_pct=partial_pnl_pct,
                                pnl_dollar=pos.realized_partial_pnl_dollar,
                                regime_at_entry=pos.regime_at_entry,
                                strategy_name=pos.strategy_name,
                                hold_bars=pos.bars_held,
                                exit_reason=ExitReason.PARTIAL_TP,
                                is_partial=True,
                            )
                            all_trades.append(partial_trade)
                            window_trades.append(partial_trade)

                    exit_reason = self._check_exit(pos, bar, ac, ts)

                    if exit_reason is not None:
                        trade = self._close_position(
                            asset, exit_reason, bar, sim_portfolio, ts
                        )
                        all_trades.append(trade)
                        window_trades.append(trade)
                        self._circuit_breaker.record_trade_result(trade.pnl_pct > 0)

                # Sync equity
                open_value = sum(
                    p.shares * p.current_price
                    for p in sim_portfolio.positions.values()
                )
                sim_portfolio.equity = sim_portfolio.cash + open_value
                sim_portfolio.buying_power = sim_portfolio.cash
                sim_portfolio.last_updated = ts

                equity_curve.append((ts, sim_portfolio.equity))
                bar_results.append({
                    "timestamp": ts,
                    "asset": asset,
                    "regime": regime.value,
                    "confidence": confidence,
                    "equity": sim_portfolio.equity,
                    "n_positions": len(sim_portfolio.positions),
                })

                bars_since_open[asset] += 1

            per_window_results.append({
                "window": window_num,
                "train_start": bar_data[primary_asset][window_start].timestamp,
                "test_start": bar_data[primary_asset][train_end].timestamp
                if train_end < len(bar_data[primary_asset])
                else None,
                "n_trades": len(window_trades),
                "equity_end": sim_portfolio.equity,
            })

            window_start += step_bars
            window_num += 1

        return BacktestResults(
            all_trades=all_trades,
            equity_curve=equity_curve,
            bar_results=bar_results,
            per_window_results=per_window_results,
            start_date=start_date,
            end_date=end_date,
            assets=assets,
            initial_capital=self._config["backtest"]["initial_capital"],
        )

    # ------------------------------------------------------------------
    # Private: fill simulation
    # ------------------------------------------------------------------

    def _simulate_fill(
        self,
        signal,  # Signal — avoid circular import annotation
        slippage_cfg: dict | None = None,
        slippage_mult: float = 1.0,
    ) -> float:
        """Apply per-asset slippage (scaled by *slippage_mult*) to entry price."""
        slippage_map = (slippage_cfg or self._config["backtest"].get("slippage_pct", {}))
        slippage = float(slippage_map.get(signal.asset, 0.0001)) * slippage_mult
        if signal.direction is Direction.LONG:
            return signal.entry_price * (1.0 + slippage)
        return signal.entry_price * (1.0 - slippage)

    def _compute_commission(
        self,
        asset: str,
        fill_value: float,
        comm_mult: float = 1.0,
    ) -> float:
        """Return commission cost for a fill, scaled by *comm_mult*."""
        if AssetClass.from_symbol(asset) is AssetClass.CRYPTO:
            rate = float(self._config["backtest"].get("commission_crypto_pct", 0.002))
            return fill_value * rate * comm_mult
        return 0.0  # zero-commission equity (multiplier not applied)

    def _build_sim_portfolio(self) -> PortfolioState:
        capital = float(self._config["backtest"]["initial_capital"])
        now = datetime.now()
        return PortfolioState(
            equity=capital,
            cash=capital,
            buying_power=capital,
            positions={},
            daily_pnl=0.0,
            session_open_equity=capital,
            rolling_30m_equity_marks=[],
            consecutive_loss_count=0,
            circuit_breaker_active=False,
            circuit_breaker_resume_time=None,
            last_updated=now,
        )

    # ------------------------------------------------------------------
    # Private: exit logic
    # ------------------------------------------------------------------

    def _check_exit(
        self,
        pos: Position,
        bar: BarData,
        ac: AssetClass,
        now: datetime,
    ) -> ExitReason | None:
        if pos.direction is Direction.LONG:
            if bar.low <= pos.stop_price:
                return ExitReason.STOP_LOSS
            if bar.high >= pos.take_profit_price:
                return ExitReason.TAKE_PROFIT
        else:
            if bar.high >= pos.stop_price:
                return ExitReason.STOP_LOSS
            if bar.low <= pos.take_profit_price:
                return ExitReason.TAKE_PROFIT

        if pos.bars_held >= pos.max_hold_bars:
            return ExitReason.MAX_HOLD

        if ac is AssetClass.EQUITY:
            try:
                if self._session_manager.is_eod_soft_close(pos.asset, now):
                    return ExitReason.EOD_FLAT
            except Exception:
                pass

        return None

    def _close_position(
        self,
        asset: str,
        exit_reason: ExitReason,
        bar: BarData,
        sim_portfolio: PortfolioState,
        ts: datetime,
    ) -> CompletedTrade:
        pos = sim_portfolio.positions.pop(asset)

        if exit_reason is ExitReason.STOP_LOSS:
            exit_price = pos.stop_price
        elif exit_reason is ExitReason.TAKE_PROFIT:
            exit_price = pos.take_profit_price
        else:
            exit_price = bar.close

        commission = self._compute_commission(asset, pos.shares * exit_price)

        if pos.direction is Direction.LONG:
            proceeds = pos.shares * exit_price - commission
            remaining_pnl_dollar = (exit_price - pos.entry_price) * pos.shares
        else:
            proceeds = pos.shares * (2 * pos.entry_price - exit_price) - commission
            remaining_pnl_dollar = (pos.entry_price - exit_price) * pos.shares

        # Blend partial and remaining PnL for a single summary trade record
        total_pnl_dollar = pos.realized_partial_pnl_dollar + remaining_pnl_dollar
        original_shares = pos.original_shares if pos.original_shares > 0 else pos.shares
        pnl_pct = total_pnl_dollar / (pos.entry_price * original_shares) if pos.entry_price > 0 else 0.0

        sim_portfolio.cash += proceeds

        return CompletedTrade(
            asset=asset,
            direction=pos.direction,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            shares=original_shares,
            entry_time=pos.entry_time,
            exit_time=ts,
            pnl_pct=pnl_pct,
            pnl_dollar=total_pnl_dollar,
            regime_at_entry=pos.regime_at_entry,
            strategy_name=pos.strategy_name,
            hold_bars=pos.bars_held,
            exit_reason=exit_reason,
            is_partial=False,
        )

    # ------------------------------------------------------------------
    # Private: feature matrix with aligned close prices
    # ------------------------------------------------------------------

    def _build_feature_matrix_with_closes(
        self,
        bars: list[BarData],
        asset: str,
        ac: AssetClass,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Return (feature_matrix, close_prices) aligned row-for-row."""
        from src.brain.feature_engineering import _bars_per_session_from_config
        fe = FeatureEngineer(self._config, ac)
        bars_per_session = _bars_per_session_from_config(self._config, ac)
        rows: list[np.ndarray] = []
        closes: list[float] = []
        for i, bar in enumerate(bars):
            fe.update(bar)
            vec = fe.compute_features(bars_since_open=i % bars_per_session)
            if vec is not None:
                rows.append(vec)
                closes.append(bar.close)

        # Store the fitted scaler so the test phase can use it
        if rows:
            fm = np.vstack(rows)
            # Fit the live FeatureEngineer's scaler with this data
            self._feature_engineers[asset] = fe
            return fm, np.array(closes)

        n_features = 10 if ac is AssetClass.EQUITY else 9
        return np.empty((0, n_features)), np.array([])
