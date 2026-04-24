'use client'

import useSWR from 'swr'
import clsx from 'clsx'

const fetcher = (url: string) => fetch(url).then((r) => r.json())

function mask(s: string): string {
  if (!s || s.length < 8) return '••••••••'
  return s.slice(0, 4) + '•'.repeat(Math.max(s.length - 8, 4)) + s.slice(-4)
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="card p-5 space-y-4">
      <h2 className="text-[10px] text-[#475569] uppercase tracking-widest font-medium border-b border-[#1e293b] pb-3">
        {title}
      </h2>
      {children}
    </div>
  )
}

function ConfigRow({ label, value, mono = false, highlight }: {
  label: string; value: string | number | boolean; mono?: boolean; highlight?: boolean
}) {
  const display = typeof value === 'boolean' ? (value ? 'true' : 'false') : String(value)
  return (
    <div className="flex items-center justify-between py-1.5 border-b border-[#0d1117] last:border-0">
      <span className="text-sm text-[#64748b]">{label}</span>
      <span
        className={clsx(
          'text-sm',
          mono && 'font-mono',
          highlight === true  && 'text-[#00ff87]',
          highlight === false && 'text-[#ff4d6d]',
          highlight === undefined && 'text-[#94a3b8]'
        )}
      >
        {display}
      </span>
    </div>
  )
}

export default function SettingsPage() {
  const { data: meta  } = useSWR('/api/meta',   fetcher, { revalidateOnFocus: false })
  const { data: config } = useSWR('/api/config', fetcher, { revalidateOnFocus: false })

  const apiKey    = process.env.NEXT_PUBLIC_ALPACA_KEY_HINT ?? ''
  const mode: 'PAPER' | 'LIVE' = meta?.mode ?? 'PAPER'
  const baseUrl   = mode === 'PAPER'
    ? 'https://paper-api.alpaca.markets'
    : 'https://api.alpaca.markets'

  return (
    <div className="max-w-[1000px] mx-auto px-4 sm:px-6 py-5 space-y-4">
      <div>
        <h1 className="text-lg font-semibold text-[#e2e8f0]">Settings</h1>
        <p className="text-xs text-[#475569] mt-0.5">Read-only configuration view · edit .env or settings.yaml on the Pi to change</p>
      </div>

      {/* Connection */}
      <Section title="Broker Connection">
        <div className="flex items-center gap-3 mb-2">
          <span
            className={clsx(
              'px-3 py-1.5 rounded-lg text-sm font-mono font-bold',
              mode === 'LIVE'
                ? 'bg-[#ff4d6d]/15 text-[#ff4d6d] border border-[#ff4d6d]/30'
                : 'bg-[#f59e0b]/15 text-[#f59e0b] border border-[#f59e0b]/30'
            )}
          >
            {mode === 'LIVE' ? '⚡ LIVE TRADING' : '🧪 PAPER TRADING'}
          </span>
          {mode === 'LIVE' && (
            <span className="text-xs text-[#ff4d6d]">Real money — exercise caution</span>
          )}
        </div>
        <ConfigRow label="API Base URL"   value={baseUrl}                mono />
        <ConfigRow label="API Key"        value={`APCA-…${mask('').slice(-8)}`} mono />
        <ConfigRow label="Secret Key"     value="••••••••••••••••"       mono />
        <ConfigRow label="Data Feed"      value="IEX (free tier)"        mono />

        <div className="mt-4 p-4 rounded-lg bg-[#1a2236] border border-[#1e293b] space-y-2">
          <p className="text-xs font-semibold text-[#64748b] uppercase tracking-wider">
            How to switch Paper ↔ Live
          </p>
          <p className="text-xs text-[#475569] leading-relaxed">
            Edit <code className="text-[#94a3b8] font-mono bg-[#0d1117] px-1 rounded">.env</code> on the Pi,
            then restart the engine container:
          </p>
          <div className="space-y-1">
            <code className="block text-xs font-mono text-[#94a3b8] bg-[#0d1117] px-3 py-2 rounded">
              # Paper trading (current)<br />
              ALPACA_BASE_URL=https://paper-api.alpaca.markets
            </code>
            <code className="block text-xs font-mono text-[#f59e0b] bg-[#0d1117] px-3 py-2 rounded">
              # Live trading (real money)<br />
              ALPACA_BASE_URL=https://api.alpaca.markets
            </code>
          </div>
          <p className="text-xs font-mono text-[#475569] mt-1">
            Then: <span className="text-[#94a3b8]">docker compose up -d --force-recreate trader dashboard</span>
          </p>
        </div>
      </Section>

      {/* Strategy */}
      {config?.strategy && (
        <Section title="Strategy Configuration">
          <ConfigRow label="Allow Short"             value={config.strategy.allow_short}                       highlight={config.strategy.allow_short} />
          <ConfigRow label="Stop Loss (ATR mult)"    value={config.strategy.stop_loss_atr_multiplier}          mono />
          <ConfigRow label="Take Profit (ATR mult)"  value={config.strategy.take_profit_atr_multiplier}        mono />
          <ConfigRow label="Trailing Stop"           value={config.strategy.trailing_stop_enabled}             highlight={config.strategy.trailing_stop_enabled} />
          <ConfigRow label="Trailing Stop (ATR)"     value={config.strategy.trailing_stop_atr_mult}            mono />
          <ConfigRow label="ADX Trend Min"           value={config.strategy.adx_trending_min}                  mono />
          <ConfigRow label="EMA Slope Filter"        value={config.strategy.ema_slope_filter}                  highlight={config.strategy.ema_slope_filter} />
          <ConfigRow label="ORB+VWAP Filter"         value={config.strategy.breakout_orb_vwap_filter}          highlight={config.strategy.breakout_orb_vwap_filter} />
          <ConfigRow label="Partial TP"              value={config.strategy.partial_tp_enabled}                highlight={config.strategy.partial_tp_enabled} />
          <ConfigRow label="Max Hold — Momentum"     value={`${config.strategy.max_hold_bars?.momentum} bars (${config.strategy.max_hold_bars?.momentum * 15} min)`} mono />
          <ConfigRow label="Max Hold — Breakout"     value={`${config.strategy.max_hold_bars?.breakout} bars (${config.strategy.max_hold_bars?.breakout * 15} min)`} mono />
          <div className="mt-3 space-y-1">
            <p className="text-[10px] text-[#475569] uppercase tracking-widest">Regime Allocations</p>
            {config.strategy.regime_allocations && Object.entries(config.strategy.regime_allocations).map(
              ([regime, val]: [string, any]) => (
                <div key={regime} className="flex items-center justify-between py-1 border-b border-[#0d1117] last:border-0">
                  <span className="text-sm text-[#64748b] font-mono">{regime}</span>
                  <div className="flex items-center gap-3 text-xs font-mono">
                    <span className="text-[#475569]">{val.mode}</span>
                    <span className={val.allocation > 0 ? 'text-[#00ff87]' : 'text-[#475569]'}>
                      {(val.allocation * 100).toFixed(0)}%
                    </span>
                  </div>
                </div>
              )
            )}
          </div>
        </Section>
      )}

      {/* Risk */}
      {config?.risk && (
        <Section title="Risk Management">
          <ConfigRow label="Daily Drawdown Limit"       value={`${(config.risk.daily_dd_limit * 100).toFixed(1)}%`}       mono highlight={false} />
          <ConfigRow label="Half-Hour Drawdown Limit"   value={`${(config.risk.half_hour_dd_limit * 100).toFixed(1)}%`}   mono highlight={false} />
          <ConfigRow label="Max Trades / Day"           value={config.risk.max_trades_per_day_equity}                     mono />
          <ConfigRow label="Consecutive Loss Pause"     value={`${config.risk.consecutive_loss_pause} losses`}            mono />
          <ConfigRow label="Pause Duration"             value={`${config.risk.pause_duration_minutes} min`}               mono />
          <ConfigRow label="Max Portfolio Leverage"     value={`${config.risk.max_portfolio_leverage}×`}                  mono />
          <ConfigRow label="Max Position Size"          value={`${(config.risk.max_single_position_pct * 100).toFixed(0)}%`} mono />
          <ConfigRow label="Max Simultaneous Positions" value={config.risk.max_simultaneous_positions}                    mono />
        </Section>
      )}

      {/* Session */}
      {config?.session && (
        <Section title="Session Windows">
          <ConfigRow label="Equity Session Start"       value={config.session.equity_session_start}            mono />
          <ConfigRow label="Equity Session End"         value={config.session.equity_session_end}              mono />
          <ConfigRow label="Entry Blackout (open)"      value={`${config.session.entry_blackout_open_minutes} min`} mono />
          <ConfigRow label="EOD Soft Close"             value={`${config.session.eod_soft_close_minutes} min before close`} mono />
          <ConfigRow label="EOD Hard Close"             value={`${config.session.eod_hard_close_minutes} min before close`} mono />
          <ConfigRow label="Timezone"                   value={config.session.timezone}                        mono />
        </Section>
      )}

      {/* HMM */}
      {config?.hmm && (
        <Section title="HMM Parameters">
          <ConfigRow label="Min Train Bars"    value={`${config.hmm.min_train_bars} (1 NYSE session)`}  mono />
          <ConfigRow label="Retrain Every"     value={`${config.hmm.retrain_every_bars} bars (daily)`}  mono />
          <ConfigRow label="Conf. Threshold"   value={config.hmm.confidence_threshold}                  mono />
          <ConfigRow label="Components (auto)" value={config.hmm.n_components_candidates?.join(', ')}   mono />
          <ConfigRow label="Default Components"value={config.hmm.n_components_default}                  mono />
          <ConfigRow label="Covariance Type"   value={config.hmm.covariance_type}                       mono />
          <ConfigRow label="Max Iterations"    value={config.hmm.n_iter}                                mono />
        </Section>
      )}

      {/* Monitoring */}
      {config?.monitoring && (
        <Section title="Monitoring & Alerts">
          <ConfigRow label="Email Alerts"    value={config.monitoring.alert_email_enabled}  highlight={config.monitoring.alert_email_enabled} />
          <ConfigRow label="Alert Email"     value={config.monitoring.alert_email_address}  mono />
          <ConfigRow label="Dashboard Port"  value={config.monitoring.dashboard_port}       mono />
          <ConfigRow label="Refresh Rate"    value={`${config.monitoring.dashboard_refresh_seconds}s`} mono />
        </Section>
      )}

      {/* PDT */}
      {config?.pdt && (
        <Section title="Pattern Day Trader Rules">
          <ConfigRow label="Equity Threshold"   value={`$${config.pdt.equity_threshold.toFixed(0)}`}             mono />
          <ConfigRow label="Max Daytrades / 5d" value={config.pdt.max_daytrades_per_5d >= 999 ? 'No cap (SEC rule removed Apr 2026)' : config.pdt.max_daytrades_per_5d} mono />
          <ConfigRow label="Rolling Window"     value={`${config.pdt.rolling_window_days} days`}                  mono />
        </Section>
      )}
    </div>
  )
}
