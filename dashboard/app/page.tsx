'use client'

import useSWR from 'swr'
import { SharedState, Trade, Meta } from '@/lib/types'
import { currency, signed, pct } from '@/lib/format'
import TopBar from '@/components/TopBar'
import KpiCard from '@/components/KpiCard'
import EngineMode from '@/components/EngineMode'
import EquityCurve from '@/components/EquityCurve'
import RegimePanel from '@/components/RegimePanel'
import PositionsTable from '@/components/PositionsTable'
import TradeHistory from '@/components/TradeHistory'

const fetcher = (url: string) => fetch(url).then((r) => r.json())

export default function Dashboard() {
  const { data: state, error: stateError } = useSWR<SharedState>('/api/state', fetcher, {
    refreshInterval: 5000,
    revalidateOnFocus: false,
    shouldRetryOnError: true,
    errorRetryInterval: 5000,
  })
  const { data: trades = [] } = useSWR<Trade[]>('/api/trades', fetcher, {
    refreshInterval: 10000,
    revalidateOnFocus: false,
  })
  const { data: meta } = useSWR<Meta>('/api/meta', fetcher, {
    revalidateOnFocus: false,
    refreshInterval: 60000,
  })

  const positions = state?.positions ?? {}
  const positionCount = Object.keys(positions).length
  const winners = trades.filter((t) => t.pnl_dollar > 0).length
  const winRate = trades.length > 0 ? winners / trades.length : null
  const totalTradePnl = trades.reduce((s, t) => s + t.pnl_dollar, 0)
  const isOffline = !!stateError || !state

  return (
    <div className="min-h-screen bg-[#0a0e1a]">
      <TopBar state={state} meta={meta} />

      <main className="max-w-[1600px] mx-auto px-4 sm:px-6 py-5 space-y-4">

        {/* Offline banner */}
        {isOffline && (
          <div className="flex items-center gap-3 px-4 py-3 rounded-xl bg-[#f59e0b]/08 border border-[#f59e0b]/20">
            <span className="w-2 h-2 rounded-full bg-[#f59e0b] amber-dot shrink-0" />
            <span className="text-[#f59e0b] text-sm">
              Engine offline — waiting for trading engine to start
            </span>
          </div>
        )}

        {/* Circuit breaker alert */}
        {state?.circuit_breaker_active && (
          <div className="flex items-center gap-3 px-4 py-3 rounded-xl bg-[#ff4d6d]/08 border border-[#ff4d6d]/25">
            <span className="text-[#ff4d6d] font-bold shrink-0">⚠</span>
            <span className="text-[#ff4d6d] font-semibold text-sm">
              Circuit Breaker Active — all trading halted
            </span>
          </div>
        )}

        {/* KPI row */}
        <div className="grid grid-cols-2 xl:grid-cols-4 gap-3">
          <KpiCard
            label="Portfolio Equity"
            value={state ? currency(state.equity) : '—'}
            sub={state ? `Cash ${currency(state.cash)}` : undefined}
            mono
          />
          <KpiCard
            label="Daily P&L"
            value={state ? signed(state.daily_pnl) : '—'}
            positive={state ? state.daily_pnl >= 0 : undefined}
            sub={
              state && state.equity - state.daily_pnl !== 0
                ? pct(state.daily_pnl / (state.equity - state.daily_pnl))
                : undefined
            }
            mono
          />
          <KpiCard
            label="Open Positions"
            value={positionCount.toString()}
            sub={
              positionCount > 0
                ? Object.keys(positions).join(', ')
                : 'Flat'
            }
          />
          <KpiCard
            label="Trades Today"
            value={trades.length.toString()}
            positive={winRate !== null ? winRate >= 0.5 : undefined}
            sub={
              winRate !== null
                ? `Win rate ${(winRate * 100).toFixed(0)}%  ·  P&L ${totalTradePnl >= 0 ? '+' : ''}${currency(totalTradePnl)}`
                : 'No trades yet'
            }
          />
        </div>

        {/* Engine mode + training progress */}
        <EngineMode state={state} />

        {/* Charts row */}
        <div className="grid grid-cols-1 xl:grid-cols-3 gap-4">
          <div className="xl:col-span-2">
            <EquityCurve data={state?.equity_curve_30m ?? []} equity={state?.equity} />
          </div>
          <RegimePanel
            regimeInfo={state?.regime_info ?? {}}
            signals={state?.last_10_signals ?? []}
          />
        </div>

        {/* Positions */}
        <PositionsTable positions={positions} />

        {/* Trade history */}
        <TradeHistory trades={trades} />

        {/* Footer */}
        <p className="text-center text-[10px] text-[#334155] pb-2">
          ML Trader Diablo v1 · refreshes every 5 s ·{' '}
          {state
            ? `last update ${new Date(state.timestamp).toLocaleTimeString('en-US', { hour12: false, timeZone: 'America/New_York' })} ET`
            : 'waiting for engine'}
        </p>
      </main>
    </div>
  )
}
