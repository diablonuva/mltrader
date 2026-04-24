'use client'

import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer, Legend } from 'recharts'
import clsx from 'clsx'

interface RegimeChange {
  ts: string
  asset: string
  old_regime: string
  new_regime: string
  confidence: number
  bar_timestamp?: string
}

interface Props {
  history: RegimeChange[]
}

const REGIME_STYLE: Record<string, { text: string; bg: string; border: string; fill: string }> = {
  TRENDING_UP:   { text: '#00ff87', bg: 'rgba(0,255,135,0.08)',   border: 'rgba(0,255,135,0.25)',  fill: '#00ff87' },
  TRENDING_DOWN: { text: '#ff4d6d', bg: 'rgba(255,77,109,0.08)',  border: 'rgba(255,77,109,0.25)', fill: '#ff4d6d' },
  BREAKOUT:      { text: '#60a5fa', bg: 'rgba(96,165,250,0.08)',  border: 'rgba(96,165,250,0.25)', fill: '#60a5fa' },
  SQUEEZE:       { text: '#fbbf24', bg: 'rgba(251,191,36,0.08)',  border: 'rgba(251,191,36,0.25)', fill: '#fbbf24' },
  CHOPPY:        { text: '#f97316', bg: 'rgba(249,115,22,0.08)',  border: 'rgba(249,115,22,0.25)', fill: '#f97316' },
  UNKNOWN:       { text: '#64748b', bg: 'rgba(100,116,139,0.08)', border: 'rgba(100,116,139,0.2)', fill: '#64748b' },
}

function RegimeBadge({ regime }: { regime: string }) {
  const s = REGIME_STYLE[regime] ?? REGIME_STYLE.UNKNOWN
  return (
    <span
      className="px-2 py-0.5 rounded text-[11px] font-mono font-semibold whitespace-nowrap"
      style={{ background: s.bg, color: s.text, border: `1px solid ${s.border}` }}
    >
      {regime.replace('_', ' ')}
    </span>
  )
}

export default function RegimeTimeline({ history }: Props) {
  // Distribution from recent history (last 200 entries)
  const recent = history.slice(-200)
  const dist: Record<string, number> = {}
  for (const r of recent) {
    const k = r.new_regime
    dist[k] = (dist[k] || 0) + 1
  }
  const pieData = Object.entries(dist)
    .map(([name, value]) => ({ name, value }))
    .sort((a, b) => b.value - a.value)

  const sorted = [...history].reverse().slice(0, 50)

  return (
    <div className="space-y-4">
      {/* Donut chart */}
      {pieData.length > 0 && (
        <div className="card p-4">
          <h3 className="text-[10px] text-[#475569] uppercase tracking-widest font-medium mb-3">
            Regime Distribution (last {recent.length} transitions)
          </h3>
          <div className="flex items-center gap-4">
            <ResponsiveContainer width="100%" height={180}>
              <PieChart>
                <Pie
                  data={pieData}
                  cx="50%"
                  cy="50%"
                  innerRadius={50}
                  outerRadius={80}
                  paddingAngle={2}
                  dataKey="value"
                >
                  {pieData.map((entry) => (
                    <Cell
                      key={entry.name}
                      fill={(REGIME_STYLE[entry.name] ?? REGIME_STYLE.UNKNOWN).fill}
                      opacity={0.85}
                    />
                  ))}
                </Pie>
                <Tooltip
                  contentStyle={{
                    background: '#111827', border: '1px solid #1e293b',
                    borderRadius: '8px', fontSize: '12px', fontFamily: 'JetBrains Mono',
                    color: '#e2e8f0',
                  }}
                  formatter={(val: number, name: string) => [
                    `${val} transitions (${((val / recent.length) * 100).toFixed(0)}%)`,
                    name.replace('_', ' '),
                  ]}
                />
                <Legend
                  formatter={(val) => (
                    <span style={{ color: (REGIME_STYLE[val] ?? REGIME_STYLE.UNKNOWN).text, fontSize: 11 }}>
                      {val.replace('_', ' ')}
                    </span>
                  )}
                />
              </PieChart>
            </ResponsiveContainer>
          </div>
        </div>
      )}

      {/* Recent regime changes table */}
      <div className="card p-4">
        <h3 className="text-[10px] text-[#475569] uppercase tracking-widest font-medium mb-3">
          Recent Regime Changes
        </h3>
        {sorted.length === 0 ? (
          <p className="text-[#475569] text-sm">No regime changes recorded</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm min-w-[540px]">
              <thead>
                <tr className="text-[10px] text-[#475569] uppercase tracking-widest">
                  <th className="text-left pb-2 font-medium">Time (ET)</th>
                  <th className="text-left pb-2 font-medium">Asset</th>
                  <th className="text-left pb-2 font-medium">From</th>
                  <th className="text-left pb-2 font-medium">To</th>
                  <th className="text-right pb-2 font-medium">Confidence</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#1e293b]">
                {sorted.map((r, i) => (
                  <tr key={i}>
                    <td className="py-2 font-mono text-[11px] text-[#475569]">
                      {new Date(r.ts).toLocaleString('en-US', {
                        month: 'short', day: 'numeric',
                        hour: '2-digit', minute: '2-digit',
                        hour12: false, timeZone: 'America/New_York',
                      })}
                    </td>
                    <td className="py-2 font-semibold text-[#e2e8f0]">{r.asset}</td>
                    <td className="py-2"><RegimeBadge regime={r.old_regime} /></td>
                    <td className="py-2"><RegimeBadge regime={r.new_regime} /></td>
                    <td className="py-2 text-right font-mono text-[11px] text-[#64748b]">
                      {(r.confidence * 100).toFixed(0)}%
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
