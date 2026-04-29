'use client'

import { PieChart, Pie, Cell, Tooltip, ResponsiveContainer, Legend } from 'recharts'
import { normalizeRegime, regimeLabel, REGIME_COLOR } from '@/lib/regime'

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

const REGIME_BG: Record<string, string> = {
  TRENDING_UP:   'rgba(0,255,135,0.10)',
  TRENDING_DOWN: 'rgba(255,77,109,0.10)',
  BREAKOUT:      'rgba(96,165,250,0.10)',
  SQUEEZE:       'rgba(251,191,36,0.10)',
  CHOPPY:        'rgba(249,115,22,0.10)',
  UNKNOWN:       'rgba(100,116,139,0.10)',
}

function RegimeBadge({ regime }: { regime: string }) {
  const key = normalizeRegime(regime)
  const color = REGIME_COLOR[key] ?? REGIME_COLOR.UNKNOWN
  const bg    = REGIME_BG[key]    ?? REGIME_BG.UNKNOWN
  return (
    <span
      className="px-2 py-0.5 rounded text-[10px] font-mono font-semibold whitespace-nowrap"
      style={{ background: bg, color, border: `1px solid ${color}40` }}
    >
      {regimeLabel(regime)}
    </span>
  )
}

export default function RegimeTimeline({ history }: Props) {
  // Distribution from recent history (last 200 entries) — normalize keys first
  const recent = history.slice(-200)
  const dist: Record<string, number> = {}
  for (const r of recent) {
    const k = normalizeRegime(r.new_regime)
    dist[k] = (dist[k] || 0) + 1
  }
  const pieData = Object.entries(dist)
    .map(([key, value]) => ({ key, name: key.replace(/_/g, ' '), value }))
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
          {/* Smaller height on mobile, larger on desktop */}
          <ResponsiveContainer width="100%" height={140}>
            <PieChart>
              <Pie
                data={pieData}
                cx="50%"
                cy="50%"
                innerRadius={36}
                outerRadius={62}
                paddingAngle={2}
                dataKey="value"
                stroke="none"
              >
                {pieData.map((entry) => (
                  <Cell
                    key={entry.key}
                    fill={REGIME_COLOR[entry.key] ?? REGIME_COLOR.UNKNOWN}
                    opacity={0.9}
                  />
                ))}
              </Pie>
              <Tooltip
                contentStyle={{
                  background: '#111827', border: '1px solid #1e293b',
                  borderRadius: '8px', fontSize: '11px', fontFamily: 'JetBrains Mono',
                  color: '#e2e8f0', padding: '6px 10px',
                }}
                formatter={(val: number, name: string) => [
                  `${val} transitions (${((val / recent.length) * 100).toFixed(0)}%)`,
                  name,
                ]}
              />
              <Legend
                wrapperStyle={{ fontSize: 10, paddingTop: 4 }}
                iconSize={8}
                formatter={(val: string, entry) => {
                  const key = (entry?.payload as { key?: string } | undefined)?.key ?? val
                  return (
                    <span style={{
                      color: REGIME_COLOR[key] ?? REGIME_COLOR.UNKNOWN,
                      fontSize: 10,
                      fontFamily: 'JetBrains Mono',
                    }}>
                      {val}
                    </span>
                  )
                }}
              />
            </PieChart>
          </ResponsiveContainer>
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
          <div className="overflow-x-auto -mx-4 sm:mx-0">
            <table className="w-full text-sm min-w-[480px] sm:min-w-0">
              <thead>
                <tr className="text-[10px] text-[#475569] uppercase tracking-widest">
                  <th className="text-left pb-2 pl-4 sm:pl-0 font-medium">Time (ET)</th>
                  <th className="text-left pb-2 font-medium">Asset</th>
                  <th className="text-left pb-2 font-medium">From</th>
                  <th className="text-left pb-2 font-medium">To</th>
                  <th className="text-right pb-2 pr-4 sm:pr-0 font-medium">Conf</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#1e293b]">
                {sorted.map((r, i) => (
                  <tr key={i}>
                    <td className="py-2 pl-4 sm:pl-0 font-mono text-[10px] text-[#475569] whitespace-nowrap">
                      {new Date(r.ts).toLocaleString('en-US', {
                        month: 'short', day: 'numeric',
                        hour: '2-digit', minute: '2-digit',
                        hour12: false, timeZone: 'America/New_York',
                      })}
                    </td>
                    <td className="py-2 font-semibold text-xs text-[#e2e8f0]">{r.asset}</td>
                    <td className="py-2"><RegimeBadge regime={r.old_regime} /></td>
                    <td className="py-2"><RegimeBadge regime={r.new_regime} /></td>
                    <td className="py-2 pr-4 sm:pr-0 text-right font-mono text-[10px] text-[#64748b]">
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
