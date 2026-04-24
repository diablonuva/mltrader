'use client'

import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer,
  CartesianGrid, ReferenceLine, Cell,
} from 'recharts'
import { currency, pct } from '@/lib/format'
import clsx from 'clsx'
import type { Trade } from '@/lib/types'

interface Props {
  trades: Trade[]
}

const REGIME_COLOR: Record<string, string> = {
  TRENDING_UP:   '#00ff87',
  TRENDING_DOWN: '#ff4d6d',
  BREAKOUT:      '#60a5fa',
  SQUEEZE:       '#fbbf24',
  CHOPPY:        '#f97316',
  UNKNOWN:       '#64748b',
}

function Stat({
  label, value, sub, positive,
}: { label: string; value: string; sub?: string; positive?: boolean }) {
  return (
    <div className="card p-4 space-y-1.5">
      <p className="text-[10px] text-[#475569] uppercase tracking-widest font-medium">{label}</p>
      <p
        className={clsx(
          'text-xl font-semibold font-mono leading-none',
          positive === true && 'text-[#00ff87]',
          positive === false && 'text-[#ff4d6d]',
          positive === undefined && 'text-[#e2e8f0]'
        )}
      >
        {value}
      </p>
      {sub && <p className="text-xs text-[#64748b]">{sub}</p>}
    </div>
  )
}

export default function StatsGrid({ trades }: Props) {
  if (!trades.length) {
    return (
      <div className="card p-6 text-center text-[#475569] text-sm">
        No trade history to compute stats
      </div>
    )
  }

  const wins   = trades.filter((t) => t.pnl_dollar > 0)
  const losses = trades.filter((t) => t.pnl_dollar <= 0)
  const totalPnl   = trades.reduce((s, t) => s + t.pnl_dollar, 0)
  const winPnl     = wins.reduce((s, t) => s + t.pnl_dollar, 0)
  const lossPnl    = Math.abs(losses.reduce((s, t) => s + t.pnl_dollar, 0))
  const winRate    = wins.length / trades.length
  const profitFactor = lossPnl > 0 ? winPnl / lossPnl : wins.length > 0 ? Infinity : 0
  const avgPnl     = totalPnl / trades.length
  const avgHold    = trades.reduce((s, t) => s + (t.hold_bars || 0), 0) / trades.length
  const bestTrade  = Math.max(...trades.map((t) => t.pnl_dollar))
  const worstTrade = Math.min(...trades.map((t) => t.pnl_dollar))
  const avgWin     = wins.length ? winPnl / wins.length : 0
  const avgLoss    = losses.length ? lossPnl / losses.length : 0

  // By-regime stats
  const byRegime: Record<string, { count: number; wins: number; pnl: number }> = {}
  for (const t of trades) {
    const r = t.regime_at_entry || 'UNKNOWN'
    if (!byRegime[r]) byRegime[r] = { count: 0, wins: 0, pnl: 0 }
    byRegime[r].count++
    byRegime[r].pnl += t.pnl_dollar
    if (t.pnl_dollar > 0) byRegime[r].wins++
  }

  const regimeBarData = Object.entries(byRegime).map(([regime, s]) => ({
    regime: regime.replace('_', ' '),
    key: regime,
    pnl: parseFloat(s.pnl.toFixed(2)),
    winRate: parseFloat(((s.wins / s.count) * 100).toFixed(1)),
    count: s.count,
  }))

  // P&L distribution (histogram buckets)
  const pnlValues = trades.map((t) => t.pnl_dollar)
  const minPnl = Math.min(...pnlValues)
  const maxPnl = Math.max(...pnlValues)
  const bucketCount = 10
  const bucketSize = (maxPnl - minPnl) / bucketCount || 1
  const buckets = Array.from({ length: bucketCount }, (_, i) => ({
    label: currency(minPnl + i * bucketSize),
    count: 0,
    positive: minPnl + i * bucketSize >= 0,
  }))
  for (const v of pnlValues) {
    const idx = Math.min(Math.floor((v - minPnl) / bucketSize), bucketCount - 1)
    buckets[idx].count++
  }

  return (
    <div className="space-y-4">
      {/* Summary stats grid */}
      <div className="grid grid-cols-2 sm:grid-cols-3 xl:grid-cols-6 gap-3">
        <Stat label="Total Trades"   value={trades.length.toString()} />
        <Stat label="Win Rate"       value={`${(winRate * 100).toFixed(1)}%`}   positive={winRate >= 0.5} />
        <Stat label="Total P&L"      value={`${totalPnl >= 0 ? '+' : ''}${currency(totalPnl)}`} positive={totalPnl >= 0} />
        <Stat label="Profit Factor"  value={isFinite(profitFactor) ? profitFactor.toFixed(2) : '∞'} positive={profitFactor >= 1.5} sub={`W:${wins.length}  L:${losses.length}`} />
        <Stat label="Avg Trade P&L"  value={`${avgPnl >= 0 ? '+' : ''}${currency(avgPnl)}`} positive={avgPnl >= 0} />
        <Stat label="Avg Hold"       value={`${avgHold.toFixed(1)} bars`} sub={`${(avgHold * 15).toFixed(0)} min`} />
      </div>

      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <Stat label="Best Trade"   value={`+${currency(bestTrade)}`}    positive />
        <Stat label="Worst Trade"  value={currency(worstTrade)}         positive={false} />
        <Stat label="Avg Win"      value={`+${currency(avgWin)}`}       positive />
        <Stat label="Avg Loss"     value={`-${currency(avgLoss)}`}      positive={false} />
      </div>

      {/* P&L by regime */}
      <div className="card p-4">
        <h3 className="text-[10px] text-[#475569] uppercase tracking-widest font-medium mb-4">
          P&L by Regime
        </h3>
        <ResponsiveContainer width="100%" height={200}>
          <BarChart data={regimeBarData} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false} />
            <XAxis
              dataKey="regime"
              tick={{ fill: '#475569', fontSize: 10, fontFamily: 'JetBrains Mono' }}
              axisLine={false}
              tickLine={false}
            />
            <YAxis
              tick={{ fill: '#475569', fontSize: 10, fontFamily: 'JetBrains Mono' }}
              axisLine={false}
              tickLine={false}
              width={52}
              tickFormatter={(v: number) => `$${v >= 0 ? '' : '-'}${Math.abs(v).toFixed(0)}`}
            />
            <ReferenceLine y={0} stroke="#334155" />
            <Tooltip
              contentStyle={{
                background: '#111827', border: '1px solid #1e293b',
                borderRadius: '8px', fontSize: '11px', fontFamily: 'JetBrains Mono', color: '#e2e8f0',
              }}
              formatter={(val: number, _: string, props: { payload: { count: number; winRate: number } }) => [
                `${currency(val)} · ${props.payload.winRate}% WR · ${props.payload.count} trades`,
                'P&L',
              ]}
            />
            <Bar dataKey="pnl" radius={[4, 4, 0, 0]} maxBarSize={40}>
              {regimeBarData.map((entry) => (
                <Cell
                  key={entry.key}
                  fill={REGIME_COLOR[entry.key] ?? '#64748b'}
                  opacity={entry.pnl >= 0 ? 0.85 : 0.5}
                />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>

      {/* P&L distribution */}
      <div className="card p-4">
        <h3 className="text-[10px] text-[#475569] uppercase tracking-widest font-medium mb-4">
          P&L Distribution
        </h3>
        <ResponsiveContainer width="100%" height={160}>
          <BarChart data={buckets} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false} />
            <XAxis
              dataKey="label"
              tick={{ fill: '#475569', fontSize: 9, fontFamily: 'JetBrains Mono' }}
              axisLine={false}
              tickLine={false}
            />
            <YAxis
              tick={{ fill: '#475569', fontSize: 10, fontFamily: 'JetBrains Mono' }}
              axisLine={false}
              tickLine={false}
              width={28}
            />
            <Tooltip
              contentStyle={{
                background: '#111827', border: '1px solid #1e293b',
                borderRadius: '8px', fontSize: '11px', fontFamily: 'JetBrains Mono', color: '#e2e8f0',
              }}
              formatter={(val: number) => [`${val} trades`, 'Count']}
            />
            <Bar dataKey="count" maxBarSize={32} radius={[2, 2, 0, 0]}>
              {buckets.map((b, i) => (
                <Cell key={i} fill={b.positive ? '#00ff87' : '#ff4d6d'} opacity={0.75} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}
