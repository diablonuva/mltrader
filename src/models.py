from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class RegimeLabel(Enum):
    SQUEEZE = "SQUEEZE"
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    BREAKOUT = "BREAKOUT"
    CHOPPY = "CHOPPY"
    UNKNOWN = "UNKNOWN"

    @classmethod
    def from_index(cls, i: int, n_states: int) -> RegimeLabel:
        _maps: dict[int, dict[int, RegimeLabel]] = {
            3: {
                0: cls.SQUEEZE,
                1: cls.TRENDING_UP,
                2: cls.BREAKOUT,
            },
            4: {
                0: cls.SQUEEZE,
                1: cls.TRENDING_UP,
                2: cls.TRENDING_DOWN,
                3: cls.BREAKOUT,
            },
            5: {
                0: cls.SQUEEZE,
                1: cls.TRENDING_UP,
                2: cls.TRENDING_DOWN,
                3: cls.BREAKOUT,
                4: cls.CHOPPY,
            },
            6: {
                0: cls.SQUEEZE,
                1: cls.CHOPPY,
                2: cls.TRENDING_UP,
                3: cls.TRENDING_DOWN,
                4: cls.BREAKOUT,
                5: cls.BREAKOUT,
            },
            7: {
                0: cls.SQUEEZE,
                1: cls.CHOPPY,
                2: cls.TRENDING_UP,
                3: cls.TRENDING_DOWN,
                4: cls.BREAKOUT,
                5: cls.BREAKOUT,
                6: cls.UNKNOWN,
            },
        }
        return _maps.get(n_states, {}).get(i, cls.UNKNOWN)


class Direction(Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    FLAT = "FLAT"


class AssetClass(Enum):
    CRYPTO = "CRYPTO"
    EQUITY = "EQUITY"

    @classmethod
    def from_symbol(cls, symbol: str) -> AssetClass:
        return cls.CRYPTO if "/" in symbol else cls.EQUITY


class ExitReason(Enum):
    STOP_LOSS = "STOP_LOSS"
    TAKE_PROFIT = "TAKE_PROFIT"
    PARTIAL_TP = "PARTIAL_TP"       # Phase 3: first partial exit at TP1
    MAX_HOLD = "MAX_HOLD"
    EOD_FLAT = "EOD_FLAT"
    REGIME_CHANGE = "REGIME_CHANGE"
    CIRCUIT_BREAKER = "CIRCUIT_BREAKER"
    MANUAL = "MANUAL"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class Signal:
    asset: str
    direction: Direction
    size_pct: float
    entry_price: float
    stop_price: float
    take_profit_price: float
    max_hold_bars: int
    strategy_name: str
    regime: RegimeLabel
    hmm_confidence: float
    lgbm_confidence: float
    timestamp: datetime
    asset_class: AssetClass
    atr_at_entry: float = 0.0       # Phase 1: carried into Position for trailing stop
    tp1_price: float = 0.0          # Phase 3: first partial exit target
    tp1_shares_pct: float = 0.0     # Phase 3: fraction of position to close at TP1

    def __post_init__(self) -> None:
        if not (0.0 <= self.size_pct <= 1.25):
            raise ValueError(
                f"size_pct must be between 0.0 and 1.25, got {self.size_pct}"
            )


@dataclass
class RiskDecision:
    approved: bool
    modified: bool
    rejected: bool
    reason_code: str
    modifications: dict = field(default_factory=dict)

    @classmethod
    def approve(cls) -> RiskDecision:
        return cls(
            approved=True,
            modified=False,
            rejected=False,
            reason_code="",
            modifications={},
        )

    @classmethod
    def reject(cls, reason: str) -> RiskDecision:
        return cls(
            approved=False,
            modified=False,
            rejected=True,
            reason_code=reason,
            modifications={},
        )


@dataclass
class Position:
    asset: str
    direction: Direction
    entry_price: float
    current_price: float
    shares: float
    entry_time: datetime
    stop_price: float
    take_profit_price: float
    max_hold_bars: int
    bars_held: int
    stop_order_id: str
    strategy_name: str
    regime_at_entry: RegimeLabel

    # Phase 1 — Trailing stop
    atr_at_entry: float = 0.0
    highest_price_since_entry: float = 0.0   # tracks peak for long trailing stop
    trailing_stop_enabled: bool = False

    # Phase 3 — Partial take-profit
    original_shares: float = 0.0            # total shares at open (for blended PnL%)
    tp1_price: float = 0.0                  # first target (e.g. 0.75× ATR)
    tp1_triggered: bool = False
    tp1_shares: float = 0.0                 # shares to liquidate at TP1
    realized_partial_pnl_dollar: float = 0.0  # PnL banked at TP1

    def __post_init__(self) -> None:
        if self.original_shares == 0.0:
            self.original_shares = self.shares
        if self.highest_price_since_entry == 0.0:
            self.highest_price_since_entry = self.entry_price

    @property
    def unrealised_pnl_pct(self) -> float:
        if self.direction is Direction.LONG:
            return (self.current_price - self.entry_price) / self.entry_price
        if self.direction is Direction.SHORT:
            return (self.entry_price - self.current_price) / self.entry_price
        return 0.0


@dataclass
class CompletedTrade:
    asset: str
    direction: Direction
    entry_price: float
    exit_price: float
    shares: float
    entry_time: datetime
    exit_time: datetime
    pnl_pct: float
    pnl_dollar: float
    regime_at_entry: RegimeLabel
    strategy_name: str
    hold_bars: int
    exit_reason: ExitReason
    is_partial: bool = False        # True for Phase 3 TP1 partial-close records


@dataclass
class PortfolioState:
    equity: float
    cash: float
    buying_power: float
    positions: dict                        # str → Position
    daily_pnl: float
    session_open_equity: float
    rolling_30m_equity_marks: list         # list of (datetime, float)
    consecutive_loss_count: int
    circuit_breaker_active: bool
    circuit_breaker_resume_time: Optional[datetime]
    last_updated: datetime


@dataclass
class BarData:
    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    bar_size: str


@dataclass
class BacktestResults:
    all_trades: list                       # list[CompletedTrade]
    equity_curve: list                     # list[(datetime, float)]
    bar_results: list                      # list[dict] — per-bar snapshot
    per_window_results: list               # list[dict] — per walk-forward window
    start_date: datetime
    end_date: datetime
    assets: list
    initial_capital: float
