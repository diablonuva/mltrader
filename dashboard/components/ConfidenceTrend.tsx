'use client'

import {
  ComposedChart, Line, Bar, XAxis, YAxis, Tooltip,
  ResponsiveContainer, CartesianGrid, Cell,
} from 'recharts'

interface RegimeChange {
  ts: string
  asset: string
  new_regime: string
  confidence: number
}

interface Props {
  history: RegimeChange[]
}

const REGIME_FILL: Record<string, string> = {
  TRENDING_UP:   '#00ff87',
  TRENDING_DOWN: '#ff4d6d',
  BREAKOUT:      '#60a5fa',
  SQUEEZE:       '#fbbf24',
  CHOPPY:        '#f97316',
  UNKNOWN:       '#64748b',
}

export default function ConfidenceTrend({ history }: Props) {
  if (history.length === 0) {
    return (
      <div className="card p-4 text-center text-[#475569] text-sm">
        No regime history recorded yet
      </div>
    )
  }

  const data = history.slice(-60).map((r, i) => ({
    i,
    confidence: Math.round(r.confidence * 100),
    regime: r.new_regime,
    label: new Date(r.ts).toLocaleString('en-US', {
      month: 'short', day: 'numeric',
      hour: '2-digit', minute: '2-digit',
      hour12: false, timeZone: 'America/New_York',
    }),
  }))

  return (
    <div className="card p-4">
      <h3 className="text-[10px] text-[#475569] uppercase tracking-widest font-medium mb-4">
        Confidence Trend — Last {data.length} Regime Transitions
      </h3>
      <ResponsiveContainer width="100%" height={200}>
        <ComposedChart data={data} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" vertical={false} />
          <XAxis
            dataKey="i"
            tick={false}
            axisLine={false}
            tickLine={false}
          />
          <YAxis
            domain={[0, 100]}
            tick={{ fill: '#475569', fontSize: 10, fontFamily: 'JetBrains Mono' }}
            axisLine={false}
            tickLine={false}
            width={32}
            tickFormatter={(v: number) => `${v}%`}
          />
          <Tooltip
            contentStyle={{
              background: '#111827', border: '1px solid #1e293b',
              borderRadius: '8px', fontSize: '11px', fontFamily: 'JetBrains Mono', color: '#e2e8f0',
            }}
            formatter={(val: number, _: string, props: { payload?: { regime?: string; label?: string } }) => [
              `${val}% confidence · ${(props.payload?.regime ?? '').replace('_', ' ')}`,
              props.payload?.label ?? '',
            ]}
            labelFormatter={() => ''}
          />
          <Bar dataKey="confidence" maxBarSize={16} radius={[2, 2, 0, 0]} opacity={0.7}>
            {data.map((entry, i) => (
              <Cell key={i} fill={REGIME_FILL[entry.regime] ?? '#64748b'} />
            ))}
          </Bar>
          <Line
            type="monotone"
            dataKey="confidence"
            dot={false}
            stroke="#94a3b8"
            strokeWidth={1.5}
            strokeDasharray="0"
          />
        </ComposedChart>
      </ResponsiveContainer>
      <div className="flex flex-wrap gap-3 mt-2">
        {Object.entries(REGIME_FILL).filter(([k]) => k !== 'UNKNOWN').map(([regime, color]) => (
          <span key={regime} className="flex items-center gap-1 text-[10px] font-mono text-[#475569]">
            <span className="w-2 h-2 rounded-sm inline-block" style={{ background: color }} />
            {regime.replace('_', ' ')}
          </span>
        ))}
      </div>
    </div>
  )
}
