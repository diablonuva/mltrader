'use client'

import {
  ComposedChart, Area, Bar, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid, ReferenceLine, Legend,
} from 'recharts'
import { currency } from '@/lib/format'

interface PnlRecord {
  date: string
  pnl_dollar: number
  pnl_pct: number
  equity: number
}

interface Props {
  records: PnlRecord[]
}

export default function PnlCurve({ records }: Props) {
  if (!records.length) {
    return (
      <div className="card p-4">
        <h2 className="text-[10px] text-[#475569] uppercase tracking-widest font-medium mb-4">
          Cumulative P&L
        </h2>
        <div className="h-[220px] flex items-center justify-center text-[#475569] text-sm">
          No session data yet
        </div>
      </div>
    )
  }

  // Build cumulative P&L
  let cumulative = 0
  const data = records.map((r) => {
    cumulative += r.pnl_dollar
    return {
      date: r.date,
      daily: r.pnl_dollar,
      cumulative,
      equity: r.equity,
    }
  })

  const isPositive = cumulative >= 0

  return (
    <div className="card p-4">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-[10px] text-[#475569] uppercase tracking-widest font-medium">
          Cumulative P&L — All Sessions
        </h2>
        <span
          className="text-sm font-mono font-semibold"
          style={{ color: isPositive ? '#00ff87' : '#ff4d6d' }}
        >
          {isPositive ? '+' : ''}{currency(cumulative)}
        </span>
      </div>
      <ResponsiveContainer width="100%" height={220}>
        <ComposedChart data={data} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
          <defs>
            <linearGradient id="cumulativeGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%"  stopColor={isPositive ? '#00ff87' : '#ff4d6d'} stopOpacity={0.2} />
              <stop offset="95%" stopColor={isPositive ? '#00ff87' : '#ff4d6d'} stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false} />
          <XAxis
            dataKey="date"
            tick={{ fill: '#475569', fontSize: 10, fontFamily: 'JetBrains Mono' }}
            axisLine={false}
            tickLine={false}
          />
          <YAxis
            yAxisId="left"
            tick={{ fill: '#475569', fontSize: 10, fontFamily: 'JetBrains Mono' }}
            axisLine={false}
            tickLine={false}
            width={56}
            tickFormatter={(v: number) => `$${v >= 0 ? '' : '-'}${Math.abs(v / 1000).toFixed(1)}k`}
          />
          <YAxis
            yAxisId="right"
            orientation="right"
            tick={{ fill: '#475569', fontSize: 10, fontFamily: 'JetBrains Mono' }}
            axisLine={false}
            tickLine={false}
            width={48}
            tickFormatter={(v: number) => `$${(v / 1000).toFixed(0)}k`}
          />
          <ReferenceLine yAxisId="left" y={0} stroke="#334155" strokeDasharray="4 2" />
          <Tooltip
            contentStyle={{
              background: '#111827', border: '1px solid #1e293b',
              borderRadius: '8px', fontSize: '12px', fontFamily: 'JetBrains Mono', color: '#e2e8f0',
            }}
            labelStyle={{ color: '#64748b', marginBottom: 4 }}
            formatter={(val: number, name: string) => [
              currency(val),
              name === 'cumulative' ? 'Cumulative' : name === 'daily' ? 'Daily' : 'Equity',
            ]}
          />
          <Legend
            wrapperStyle={{ fontSize: 10, color: '#64748b' }}
            formatter={(val) => val === 'cumulative' ? 'Cumulative P&L' : val === 'daily' ? 'Daily P&L' : val}
          />
          <Bar
            yAxisId="left"
            dataKey="daily"
            fill="#1e293b"
            radius={[2, 2, 0, 0]}
            maxBarSize={24}
          />
          <Area
            yAxisId="left"
            type="monotone"
            dataKey="cumulative"
            stroke={isPositive ? '#00ff87' : '#ff4d6d'}
            strokeWidth={2}
            fill="url(#cumulativeGrad)"
            dot={false}
            activeDot={{ r: 4, strokeWidth: 0 }}
          />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  )
}
