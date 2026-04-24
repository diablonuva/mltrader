'use client'

import {
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  CartesianGrid,
} from 'recharts'
import { shortTime, currency } from '@/lib/format'

interface Props {
  data: [string, number][]
  equity?: number
}

export default function EquityCurve({ data, equity }: Props) {
  const chartData = data.map(([ts, eq]) => ({ time: shortTime(ts), equity: eq }))

  const trend =
    chartData.length >= 2
      ? chartData[chartData.length - 1].equity >= chartData[0].equity
        ? 'up'
        : 'down'
      : 'up'

  const stroke = trend === 'up' ? '#00ff87' : '#ff4d6d'
  const gradId = trend === 'up' ? 'gradGreen' : 'gradRed'

  return (
    <div className="card p-4 h-full">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-[10px] text-[#475569] uppercase tracking-widest font-medium">
          Equity Curve — 30 min window
        </h2>
        {equity !== undefined && (
          <span className="text-sm font-mono font-semibold text-[#e2e8f0]">
            {currency(equity)}
          </span>
        )}
      </div>

      {chartData.length === 0 ? (
        <div className="h-[200px] flex items-center justify-center text-[#475569] text-sm">
          Waiting for bar data…
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={200}>
          <AreaChart data={chartData} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
            <defs>
              <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor={stroke} stopOpacity={0.18} />
                <stop offset="95%" stopColor={stroke} stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false} />
            <XAxis
              dataKey="time"
              tick={{ fill: '#475569', fontSize: 10, fontFamily: 'JetBrains Mono' }}
              axisLine={false}
              tickLine={false}
            />
            <YAxis
              domain={['auto', 'auto']}
              tick={{ fill: '#475569', fontSize: 10, fontFamily: 'JetBrains Mono' }}
              axisLine={false}
              tickLine={false}
              width={52}
              tickFormatter={(v: number) => `$${(v / 1000).toFixed(0)}k`}
            />
            <Tooltip
              contentStyle={{
                background: '#111827',
                border: '1px solid #1e293b',
                borderRadius: '8px',
                fontSize: '12px',
                fontFamily: 'JetBrains Mono',
                color: '#e2e8f0',
                boxShadow: '0 4px 24px rgba(0,0,0,0.4)',
              }}
              formatter={(val: number) => [currency(val), 'Equity']}
              labelStyle={{ color: '#64748b', marginBottom: 4 }}
              cursor={{ stroke: '#334155', strokeDasharray: '4 2' }}
            />
            <Area
              type="monotone"
              dataKey="equity"
              stroke={stroke}
              strokeWidth={2}
              fill={`url(#${gradId})`}
              dot={false}
              activeDot={{ r: 4, fill: stroke, strokeWidth: 0 }}
            />
          </AreaChart>
        </ResponsiveContainer>
      )}
    </div>
  )
}
