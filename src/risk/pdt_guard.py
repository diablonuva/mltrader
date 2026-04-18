from __future__ import annotations

import json
import logging
import os
from datetime import datetime, date, timedelta
from typing import List

from src.models import AssetClass

logger = logging.getLogger(__name__)


def _business_days_ago(n: int, from_date: date) -> date:
    """Return the calendar date that is *n* business days before *from_date*."""
    result = from_date
    counted = 0
    while counted < n:
        result -= timedelta(days=1)
        if result.weekday() < 5:  # Mon–Fri
            counted += 1
    return result


class PDTGuard:
    """Day-trade counter for equity accounts.

    Note: The SEC eliminated the traditional $25,000 PDT rule in April 2026,
    replacing it with a risk-based margin framework (Bloomberg, 2026-04-14).
    equity_threshold is now 0.0 in all configs, so can_trade() always returns
    True for equity — the guard is kept as an informational counter only.
    Crypto assets remain unconditionally exempt.
    """

    def __init__(self, config: dict) -> None:
        pdt_cfg = config["pdt"]
        self._equity_threshold: float = pdt_cfg["equity_threshold"]
        self._max_daytrades: int = pdt_cfg["max_daytrades_per_5d"]
        self._rolling_window_days: int = pdt_cfg["rolling_window_days"]
        self._counter_file: str = pdt_cfg["pdt_counter_file"]

        self._trade_log: List[dict] = []
        self._load_from_disk()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def can_trade(self, asset: str, account_equity: float) -> bool:
        """Return True if another equity day trade is permitted.

        With equity_threshold=0.0 (post-April-2026 SEC rule change) this
        always returns True for equity.  Retained for counter/audit purposes.
        """
        if AssetClass.from_symbol(asset) is AssetClass.CRYPTO:
            return True
        if account_equity >= self._equity_threshold:
            return True
        self._evict_stale()
        return len(self._trade_log) < self._max_daytrades

    def record_daytrade(
        self,
        asset: str,
        entry_time: datetime,
        exit_time: datetime,
    ) -> None:
        """Record a completed day trade (opened and closed on the same calendar day).

        No-op for crypto or inter-day trades.
        """
        if AssetClass.from_symbol(asset) is AssetClass.CRYPTO:
            return
        if entry_time.date() != exit_time.date():
            return

        self._trade_log.append(
            {
                "asset": asset,
                "entry_time": entry_time.isoformat(),
                "exit_time": exit_time.isoformat(),
                "trade_date": entry_time.date().isoformat(),
            }
        )
        self._save_to_disk()
        logger.info(
            "PDT trade recorded for %s on %s  (count=%d/%d)",
            asset,
            entry_time.date(),
            len(self._trade_log),
            self._max_daytrades,
        )

    def get_current_count(self) -> int:
        """Return the number of day trades in the current rolling window."""
        self._evict_stale()
        return len(self._trade_log)

    def get_remaining_trades(self) -> int:
        """Return how many more day trades are allowed in the rolling window."""
        return max(0, self._max_daytrades - self.get_current_count())

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _evict_stale(self) -> None:
        """Remove entries older than rolling_window_days business days."""
        cutoff = _business_days_ago(self._rolling_window_days, date.today())
        self._trade_log = [
            entry for entry in self._trade_log
            if date.fromisoformat(entry["trade_date"]) >= cutoff
        ]

    def _save_to_disk(self) -> None:
        """Persist the trade log to the configured JSON file."""
        os.makedirs(os.path.dirname(self._counter_file) or ".", exist_ok=True)
        try:
            with open(self._counter_file, "w", encoding="utf-8") as fh:
                json.dump(self._trade_log, fh, indent=2)
        except OSError as exc:
            logger.warning("PDTGuard: could not save counter file: %s", exc)

    def _load_from_disk(self) -> None:
        """Load the trade log from disk, then evict stale entries."""
        if not os.path.exists(self._counter_file):
            return
        try:
            with open(self._counter_file, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            if isinstance(raw, list):
                self._trade_log = raw
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("PDTGuard: could not read counter file: %s", exc)
        self._evict_stale()
