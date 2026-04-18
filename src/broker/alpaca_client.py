"""
WARNING: This module interacts with real brokerage accounts.
Test ONLY in paper mode until the full system is validated end-to-end.
"""
from __future__ import annotations

import logging
import threading
import time
import traceback
from typing import Callable, Optional

from dotenv import load_dotenv

from alpaca.data.live import CryptoDataStream, StockDataStream
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import QueryOrderStatus
from alpaca.trading.requests import GetOrdersRequest

from src.models import AssetClass, BarData

logger = logging.getLogger(__name__)

_REQUIRED_ENV = ["ALPACA_API_KEY", "ALPACA_SECRET_KEY", "ALPACA_BASE_URL"]
_MAX_RECONNECT_ATTEMPTS = 10
_BACKOFF_BASE = 1.0
_BACKOFF_MAX = 60.0


class AlpacaClient:
    """Manages REST and WebSocket connections to Alpaca.

    Always create in paper mode first.  Live-mode requires an explicit operator
    confirmation at the terminal.
    """

    def __init__(self, config: dict) -> None:  # noqa: ARG002 — config reserved
        load_dotenv()

        import os
        missing = [k for k in _REQUIRED_ENV if not os.environ.get(k)]
        if missing:
            raise EnvironmentError(
                f"Missing required environment variable(s): {missing}. "
                "Add them to your .env file and restart."
            )

        api_key = os.environ["ALPACA_API_KEY"]
        secret_key = os.environ["ALPACA_SECRET_KEY"]
        base_url = os.environ["ALPACA_BASE_URL"]

        self.is_paper: bool = "paper" in base_url.lower()

        if not self.is_paper:
            print("\n" + "=" * 60)
            print("  !! LIVE TRADING MODE DETECTED !!")
            print("  This will interact with a REAL brokerage account.")
            print("  All trades are REAL and CANNOT be undone.")
            print("=" * 60)
            confirmation = input('Type "YES I UNDERSTAND THE RISKS": ').strip()
            if confirmation != "YES I UNDERSTAND THE RISKS":
                raise SystemExit("Cancelled.  No changes were made.")
            logger.warning("LIVE MODE CONFIRMED by operator")

        self._rest = TradingClient(
            api_key=api_key,
            secret_key=secret_key,
            paper=self.is_paper,
        )
        self._crypto_stream = CryptoDataStream(api_key, secret_key)
        self._stock_stream = StockDataStream(api_key, secret_key)

        self._bar_callback: Optional[Callable[[BarData], None]] = None
        self._bar_size: str = "5Min"
        self._crypto_symbols: list[str] = []
        self._stock_symbols: list[str] = []

        self._connected: bool = False
        self._reconnect_attempts: int = 0
        self._stop_event = threading.Event()

    # ------------------------------------------------------------------
    # REST: account & positions
    # ------------------------------------------------------------------

    def get_account(self) -> dict:
        """Return key account fields as a plain dict."""
        try:
            acc = self._rest.get_account()
            return {
                "equity": float(acc.equity),
                "cash": float(acc.cash),
                "buying_power": float(acc.buying_power),
                "pattern_day_trader": acc.pattern_day_trader,
                "account_blocked": acc.account_blocked,
            }
        except Exception:
            logger.error("get_account failed:\n%s", traceback.format_exc())
            raise RuntimeError("Failed to fetch account info from Alpaca") from None

    def get_positions(self) -> list[dict]:
        """Return all open positions as plain dicts."""
        try:
            raw = self._rest.get_all_positions()
            return [
                {
                    "symbol": p.symbol,
                    "qty": float(p.qty),
                    "side": p.side.value if hasattr(p.side, "value") else str(p.side),
                    "avg_entry_price": float(p.avg_entry_price),
                    "unrealized_pl": float(p.unrealized_pl),
                    "market_value": float(p.market_value),
                }
                for p in raw
            ]
        except Exception:
            logger.error("get_positions failed:\n%s", traceback.format_exc())
            raise RuntimeError("Failed to fetch positions from Alpaca") from None

    def get_open_orders(self) -> list[dict]:
        """Return all open orders as plain dicts."""
        try:
            raw = self._rest.get_orders(
                GetOrdersRequest(status=QueryOrderStatus.OPEN)
            )
            return [
                {
                    "id": str(o.id),
                    "symbol": o.symbol,
                    "qty": float(o.qty) if o.qty else None,
                    "side": o.side.value if hasattr(o.side, "value") else str(o.side),
                    "type": o.type.value if hasattr(o.type, "value") else str(o.type),
                    "status": o.status.value if hasattr(o.status, "value") else str(o.status),
                    "limit_price": float(o.limit_price) if o.limit_price else None,
                    "created_at": o.created_at,
                }
                for o in raw
            ]
        except Exception:
            logger.error("get_open_orders failed:\n%s", traceback.format_exc())
            raise RuntimeError("Failed to fetch open orders from Alpaca") from None

    # ------------------------------------------------------------------
    # WebSocket: bar subscriptions
    # ------------------------------------------------------------------

    def subscribe_bars(
        self,
        symbols: list[str],
        bar_size: str,
        callback: Callable[[BarData], None],
    ) -> None:
        """Register *callback* for 1-min bar events for each symbol.

        Routes each symbol to the correct stream (crypto vs equity) and wraps
        the raw Alpaca ``Bar`` object into a ``BarData`` before calling back.
        """
        self._bar_callback = callback
        self._bar_size = bar_size

        crypto_syms = [s for s in symbols if AssetClass.from_symbol(s) is AssetClass.CRYPTO]
        stock_syms = [s for s in symbols if AssetClass.from_symbol(s) is AssetClass.EQUITY]

        if crypto_syms:
            self._crypto_symbols = crypto_syms
            self._crypto_stream.subscribe_bars(
                self._make_bar_handler(bar_size), *crypto_syms
            )
            logger.info("Subscribed crypto bars: %s", crypto_syms)

        if stock_syms:
            self._stock_symbols = stock_syms
            self._stock_stream.subscribe_bars(
                self._make_bar_handler(bar_size), *stock_syms
            )
            logger.info("Subscribed stock bars: %s", stock_syms)

        self._connected = True

    def start_streaming(self) -> None:
        """Launch both stream clients in background daemon threads.

        Each thread runs an exponential-backoff reconnect loop (max 10 attempts).
        """
        self._stop_event.clear()
        self._connected = True

        if self._stock_symbols:
            threading.Thread(
                target=self._run_with_reconnect,
                args=(self._stock_stream, "stock"),
                daemon=True,
                name="alpaca-stock-stream",
            ).start()

        if self._crypto_symbols:
            threading.Thread(
                target=self._run_with_reconnect,
                args=(self._crypto_stream, "crypto"),
                daemon=True,
                name="alpaca-crypto-stream",
            ).start()

        logger.info("Streaming started.")

    def stop_streaming(self) -> None:
        """Stop all stream clients gracefully."""
        self._connected = False
        self._stop_event.set()

        for stream, name in (
            (self._stock_stream, "stock"),
            (self._crypto_stream, "crypto"),
        ):
            try:
                stream.stop()
            except Exception:
                logger.debug("stop() raised on %s stream (ignored)", name)

        logger.info("Streaming stopped.")

    def is_connected(self) -> bool:
        return self._connected

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _make_bar_handler(self, bar_size: str):
        """Return an async handler that converts Alpaca Bar → BarData."""

        async def handler(bar) -> None:
            if self._bar_callback is None:
                return
            try:
                bar_data = BarData(
                    symbol=bar.symbol,
                    timestamp=bar.timestamp,
                    open=float(bar.open),
                    high=float(bar.high),
                    low=float(bar.low),
                    close=float(bar.close),
                    volume=float(bar.volume),
                    bar_size=bar_size,
                )
                self._bar_callback(bar_data)
            except Exception:
                logger.error(
                    "Error in bar handler for %s:\n%s",
                    getattr(bar, "symbol", "?"),
                    traceback.format_exc(),
                )

        return handler

    def _run_with_reconnect(self, stream, name: str) -> None:
        """Run *stream* with exponential-backoff reconnection (max 10 attempts)."""
        attempt = 0
        while attempt < _MAX_RECONNECT_ATTEMPTS:
            if self._stop_event.is_set():
                return
            try:
                stream.run()
                # run() returned cleanly — check if we should exit
                if self._stop_event.is_set():
                    return
                logger.warning("%s stream exited unexpectedly; reconnecting…", name)
            except Exception as exc:
                if self._stop_event.is_set():
                    return
                logger.warning(
                    "%s stream error (attempt %d/%d): %s",
                    name, attempt + 1, _MAX_RECONNECT_ATTEMPTS, exc,
                )

            delay = min(_BACKOFF_BASE * (2 ** attempt), _BACKOFF_MAX)
            logger.info("Reconnecting %s stream in %.0fs…", name, delay)
            self._reconnect_attempts += 1
            # Wait interruptibly so stop_streaming() wakes us immediately
            self._stop_event.wait(timeout=delay)
            if self._stop_event.is_set():
                return

            attempt += 1
            # Re-subscribe symbols after reconnect
            self._resubscribe(stream, name)

        logger.error("%s stream gave up after %d attempts", name, _MAX_RECONNECT_ATTEMPTS)
        raise RuntimeError(
            f"Alpaca {name} stream failed after {_MAX_RECONNECT_ATTEMPTS} reconnect attempts"
        )

    def _resubscribe(self, stream, name: str) -> None:
        """Re-register bar handlers after a reconnect."""
        try:
            if name == "stock" and self._stock_symbols:
                stream.subscribe_bars(
                    self._make_bar_handler(self._bar_size), *self._stock_symbols
                )
            elif name == "crypto" and self._crypto_symbols:
                stream.subscribe_bars(
                    self._make_bar_handler(self._bar_size), *self._crypto_symbols
                )
        except Exception:
            logger.warning(
                "Re-subscription failed for %s stream:\n%s", name, traceback.format_exc()
            )
