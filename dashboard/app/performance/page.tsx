'use client'

import useSWR from 'swr'
import type { Trade } from '@/lib/types'
import StatsGrid from '@/components/StatsGrid'
import PnlCurve from '@/components/PnlCurve'
import TradeHistory from '@/components/TradeHistory'

const fetcher = (url: string) => fetch(url).then((r) => r.json())

export default function PerformancePage() {
  const { data: allTrades = [], isLoading: tradesLoading } = useSWR<Trade[]>(
    '/api/trades?period=all', fetcher, { refreshInterval: 30000, revalidateOnFocus: false }
  )
  const { data: pnlHistory = [], isLoading: pnlLoading } = useSWR(
    '/api/pnl-history', fetcher, { refreshInterval: 60000, revalidateOnFocus: false }
  )

  const loading = tradesLoading || pnlLoading

  return (
    <div className="max-w-[1600px] mx-auto px-4 sm:px-6 py-5 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-lg font-semibold text-[#e2e8f0]">Performance Analytics</h1>
          <p className="text-xs text-[#475569] mt-0.5">All-time trade history · live from logs</p>
        </div>
        {loading && (
          <span className="text-xs text-[#f59e0b] font-mono animate-pulse">Loading…</span>
        )}
        {!loading && (
          <span className="text-xs font-mono text-[#475569]">
            {allTrades.length} total trades
          </span>
        )}
      </div>

      {/* Cumulative P&L curve */}
      <PnlCurve records={pnlHistory} />

      {/* Stats + charts */}
      <StatsGrid trades={allTrades} />

      {/* Full trade log */}
      <div className="space-y-2">
        <h2 className="text-[10px] text-[#475569] uppercase tracking-widest font-medium px-1">
          Full Trade History ({allTrades.length})
        </h2>
        <TradeHistory trades={allTrades} showAll />
      </div>
    </div>
  )
}
