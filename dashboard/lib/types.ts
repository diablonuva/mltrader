export type Regime =
  | 'TRENDING_UP'
  | 'TRENDING_DOWN'
  | 'BREAKOUT'
  | 'SQUEEZE'
  | 'CHOPPY'
  | 'UNKNOWN'

export interface Position {
  direction: 'LONG' | 'SHORT'
  entry_price: number
  current_price: number
  shares: number
  entry_time: string
  stop_price: number
  take_profit_price: number
  bars_held: number
  unrealised_pnl_pct: number
}

export interface RegimeInfo {
  regime: Regime
  confidence: number
  strategy?: string
}

export interface Signal {
  ts: string
  reason: string
  asset: string | null
  direction: string | null
  size_pct: number | null
}

export interface SharedState {
  timestamp: string
  equity: number
  cash: number
  buying_power: number
  daily_pnl: number
  circuit_breaker_active: boolean
  positions: Record<string, Position>
  regime_info: Record<string, RegimeInfo>
  last_10_signals: Signal[]
  equity_curve_30m: [string, number][]
  training_bars: number
  training_needed: number
  training_pct: number
  hmm_trained: boolean
}

export interface Trade {
  event: string
  ts: string
  asset: string
  direction: 'LONG' | 'SHORT'
  entry_price: number
  exit_price: number
  shares: number
  entry_time: string
  exit_time: string
  pnl_pct: number
  pnl_dollar: number
  regime_at_entry: string
  strategy_name: string
  hold_bars: number
  exit_reason: string
}

export interface Meta {
  mode: 'LIVE' | 'PAPER'
  assets: string[]
}
