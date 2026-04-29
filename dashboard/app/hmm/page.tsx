'use client'

import { useEffect, useState } from 'react'
import useSWR from 'swr'
import type { SharedState } from '@/lib/types'
import RegimeTimeline from '@/components/RegimeTimeline'
import ConfidenceTrend from '@/components/ConfidenceTrend'
import { isNyseOpen, nextMarketEvent, formatCountdown } from '@/lib/format'
import { normalizeRegime, regimeLabel } from '@/lib/regime'
import clsx from 'clsx'
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer,
  CartesianGrid, Cell,
} from 'recharts'

const fetcher = (url: string) => fetch(url).then((r) => r.json())

const REGIME_STYLE: Record<string, { text: string; bg: string; border: string }> = {
  TRENDING_UP:   { text: '#00ff87', bg: 'rgba(0,255,135,0.08)',   border: 'rgba(0,255,135,0.25)'  },
  TRENDING_DOWN: { text: '#ff4d6d', bg: 'rgba(255,77,109,0.08)',  border: 'rgba(255,77,109,0.25)' },
  BREAKOUT:      { text: '#60a5fa', bg: 'rgba(96,165,250,0.08)',  border: 'rgba(96,165,250,0.25)' },
  SQUEEZE:       { text: '#fbbf24', bg: 'rgba(251,191,36,0.08)',  border: 'rgba(251,191,36,0.25)' },
  CHOPPY:        { text: '#f97316', bg: 'rgba(249,115,22,0.08)',  border: 'rgba(249,115,22,0.25)' },
  UNKNOWN:       { text: '#64748b', bg: 'rgba(100,116,139,0.08)', border: 'rgba(100,116,139,0.2)' },
}

const REGIME_FILL: Record<string, string> = {
  TRENDING_UP: '#00ff87', TRENDING_DOWN: '#ff4d6d', BREAKOUT: '#60a5fa',
  SQUEEZE: '#fbbf24', CHOPPY: '#f97316', UNKNOWN: '#64748b',
}

type TrainingPhase = 'offline' | 'collecting' | 'ready' | 'trained'

function resolvePhase(state?: SharedState, open?: boolean): TrainingPhase {
  if (!state) return 'offline'
  if (state.hmm_trained) return 'trained'
  const bars    = state.training_bars   ?? 0
  const needed  = state.training_needed ?? 390
  if (bars >= needed) return 'ready'
  if (open && bars > 0) return 'collecting'
  return 'collecting'
}

export default function HmmPage() {
  const [now, setNow] = useState(new Date())
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(id)
  }, [])

  const { data: state } = useSWR<SharedState>('/api/state', fetcher, {
    refreshInterval: 5000, revalidateOnFocus: false,
  })
  const { data: history = [] } = useSWR('/api/regime-history', fetcher, {
    refreshInterval: 30000, revalidateOnFocus: false,
  })

  const nyseOpen    = isNyseOpen(now)
  const market      = nextMarketEvent(now)
  const countdown   = formatCountdown(market.seconds)

  const trained     = state?.hmm_trained ?? false
  const trainBars   = state?.training_bars   ?? 0
  const trainNeeded = state?.training_needed ?? 390
  const remaining   = Math.max(0, trainNeeded - trainBars)
  const trainPct    = trainNeeded > 0 ? Math.min((trainBars / trainNeeded) * 100, 100) : 100
  const phase       = resolvePhase(state, nyseOpen)

  const regimeInfo  = state?.regime_info ?? {}
  const assets      = Object.entries(regimeInfo)

  // Next market open as a human-readable string
  const nextOpenStr = (() => {
    const d = new Date(now.getTime() + market.seconds * 1000)
    return d.toLocaleString('en-US', {
      weekday: 'long', month: 'long', day: 'numeric',
      hour: '2-digit', minute: '2-digit',
      hour12: false, timeZone: 'America/New_York',
    }) + ' ET'
  })()

  // Sessions remaining estimate (390 bars ≈ 1 full trading session)
  const sessionsRemaining = trainNeeded > 0 ? (remaining / trainNeeded) : 0

  // Training roadmap bar data
  const roadmapData = [
    { label: 'Collected', value: Math.min(trainBars, trainNeeded), max: trainNeeded, positive: true },
    { label: 'Remaining', value: remaining, max: trainNeeded, positive: false },
  ]

  // Regime stats from history
  const recentHistory = history.slice(-200)
  const regimeDist: Record<string, { count: number; totalConf: number }> = {}
  for (const r of recentHistory) {
    const k = normalizeRegime(r.new_regime as string)
    if (!regimeDist[k]) regimeDist[k] = { count: 0, totalConf: 0 }
    regimeDist[k].count++
    regimeDist[k].totalConf += r.confidence as number
  }
  const regimeStatRows = Object.entries(regimeDist)
    .map(([regime, s]) => ({
      regime,
      count: s.count,
      avgConf: s.count > 0 ? (s.totalConf / s.count) * 100 : 0,
      pct: recentHistory.length > 0 ? (s.count / recentHistory.length) * 100 : 0,
    }))
    .sort((a, b) => b.count - a.count)

  const mostCommon = regimeStatRows[0]?.regime ?? '—'
  const totalTransitions = history.length

  // Phase banner config
  const phaseCfg = {
    offline: {
      color: '#64748b', bg: 'rgba(100,116,139,0.07)', border: 'rgba(100,116,139,0.2)',
      dot: 'bg-[#64748b]', dotAnim: '',
      title: 'Engine Offline',
      sub: 'Waiting for shared_state.json — is the trader container running?',
    },
    collecting: {
      color: '#f59e0b', bg: 'rgba(245,158,11,0.07)', border: 'rgba(245,158,11,0.2)',
      dot: 'bg-[#f59e0b]', dotAnim: 'amber-dot',
      title: nyseOpen
        ? `Accumulating Training Data · ${trainBars} / ${trainNeeded} bars`
        : `Market Closed · ${trainBars} / ${trainNeeded} bars collected`,
      sub: nyseOpen
        ? `${remaining} bars remaining · ~${sessionsRemaining.toFixed(1)} session${sessionsRemaining !== 1 ? 's' : ''}`
        : `HMM will continue collecting at next open · ${countdown} until market opens`,
    },
    ready: {
      color: '#f59e0b', bg: 'rgba(245,158,11,0.07)', border: 'rgba(245,158,11,0.2)',
      dot: 'bg-[#f59e0b]', dotAnim: 'amber-dot',
      title: 'Ready to Activate · Awaiting Next Market Open',
      sub: nyseOpen
        ? 'HMM will train on the next full session close'
        : `Training triggers at next open · ${nextOpenStr}`,
    },
    trained: {
      color: '#00ff87', bg: 'rgba(0,255,135,0.06)', border: 'rgba(0,255,135,0.2)',
      dot: 'bg-[#00ff87]', dotAnim: 'live-dot',
      title: 'HMM Trained · Live Regime Classification Active',
      sub: `${Object.keys(regimeInfo).join(', ') || '—'} · retrained every 390 bars · confidence threshold 0.55`,
    },
  }[phase]

  return (
    <div className="max-w-[1600px] mx-auto px-4 sm:px-6 py-5 space-y-4">
      <div>
        <h1 className="text-lg font-semibold text-[#e2e8f0]">HMM Regime Analysis</h1>
        <p className="text-xs text-[#475569] mt-0.5">
          Hidden Markov Model · real-time regime detection per asset
        </p>
      </div>

      {/* Phase status banner */}
      <div className="rounded-xl p-4 border" style={{ background: phaseCfg.bg, borderColor: phaseCfg.border }}>
        <div className="flex items-start gap-3">
          <span className={clsx('w-2.5 h-2.5 rounded-full shrink-0 mt-0.5', phaseCfg.dot, phaseCfg.dotAnim)} />
          <div className="flex-1 min-w-0">
            <p className="font-semibold text-sm" style={{ color: phaseCfg.color }}>{phaseCfg.title}</p>
            <p className="text-xs text-[#64748b] mt-0.5">{phaseCfg.sub}</p>
          </div>
          {!nyseOpen && phase !== 'trained' && phase !== 'offline' && (
            <div className="text-right shrink-0">
              <p className="text-[10px] text-[#475569] uppercase tracking-widest">Opens in</p>
              <p className="text-sm font-mono font-semibold text-[#f59e0b] tabular-nums">{countdown}</p>
            </div>
          )}
          {phase === 'trained' && nyseOpen && (
            <div className="text-right shrink-0">
              <p className="text-[10px] text-[#475569] uppercase tracking-widest">NYSE</p>
              <p className="text-sm font-mono font-semibold text-[#00ff87]">OPEN</p>
            </div>
          )}
        </div>

        {/* Progress bar — only when actively collecting */}
        {(phase === 'collecting' || phase === 'ready') && (
          <div className="mt-3">
            <div className="flex justify-between text-[10px] font-mono text-[#475569] mb-1">
              <span>{trainBars.toLocaleString()} bars collected</span>
              <span>{trainNeeded.toLocaleString()} needed</span>
            </div>
            <div className="w-full bg-[#1e293b] rounded-full h-2 overflow-hidden">
              <div
                className="h-full rounded-full transition-all duration-700 ease-out"
                style={{
                  width: `${trainPct}%`,
                  background: phase === 'ready' ? '#00ff87' : '#f59e0b',
                }}
              />
            </div>
            {phase === 'ready' && (
              <p className="text-[10px] font-mono text-[#00ff87] mt-1">
                ✓ All data collected — HMM ready to train
              </p>
            )}
          </div>
        )}
      </div>

      {/* Feature Engineer Warmup — visible whenever warmup data exists */}
      {state?.feature_warmup && Object.keys(state.feature_warmup).length > 0 && (
        <div className="card p-4 space-y-3">
          <div className="flex items-center justify-between">
            <h3 className="text-[10px] text-[#475569] uppercase tracking-widest font-medium">
              Feature Engineer Warmup
            </h3>
            <span className="text-[10px] text-[#475569]">
              required before HMM inference can begin
            </span>
          </div>
          <div className="space-y-3">
            {Object.entries(state.feature_warmup).map(([asset, w]) => {
              const pct = w.needed > 0 ? Math.min((w.bars / w.needed) * 100, 100) : 100
              const remaining = Math.max(0, w.needed - w.bars)
              return (
                <div key={asset}>
                  <div className="flex items-center justify-between mb-1.5">
                    <div className="flex items-center gap-2">
                      <span className="text-sm font-semibold text-[#e2e8f0]">{asset}</span>
                      {w.ready ? (
                        <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-[#00ff87]/10 text-[#00ff87] border border-[#00ff87]/25">
                          ✓ READY
                        </span>
                      ) : (
                        <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-[#f59e0b]/10 text-[#f59e0b] border border-[#f59e0b]/25">
                          ⏳ IN PROGRESS
                        </span>
                      )}
                    </div>
                    <span className="text-[10px] font-mono text-[#64748b] tabular-nums">
                      {w.bars} / {w.needed} bars ({pct.toFixed(0)}%)
                    </span>
                  </div>
                  <div className="w-full bg-[#1e293b] rounded-full h-1.5 overflow-hidden">
                    <div
                      className="h-full rounded-full transition-all duration-700 ease-out"
                      style={{
                        width: `${pct}%`,
                        background: w.ready ? '#00ff87' : '#f59e0b',
                      }}
                    />
                  </div>
                  {!w.ready && (
                    <p className="text-[10px] font-mono text-[#475569] mt-1">
                      {remaining} bar{remaining === 1 ? '' : 's'} remaining · rolling windows fill from streamed bars
                    </p>
                  )}
                </div>
              )
            })}
          </div>
          {trained && Object.values(state.feature_warmup).some((w) => !w.ready) && (
            <div className="rounded-lg p-2 bg-[#1e293b] border border-[#334155]">
              <p className="text-[10px] text-[#94a3b8] leading-relaxed">
                <span className="text-[#f59e0b]">Note:</span>{' '}
                HMM is trained but no trades will fire until warmup completes — inference needs the feature
                engineer&apos;s rolling windows (vol-ratio, realized-vol, VWAP) to be filled.
              </p>
            </div>
          )}
        </div>
      )}

      {/* Training roadmap — only before HMM is trained */}
      {phase !== 'trained' && phase !== 'offline' && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {/* Bar chart: collected vs remaining */}
          <div className="card p-4">
            <h3 className="text-[10px] text-[#475569] uppercase tracking-widest font-medium mb-4">
              Training Progress — Bars
            </h3>
            <ResponsiveContainer width="100%" height={160}>
              <BarChart data={roadmapData} layout="vertical" margin={{ top: 0, right: 16, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" horizontal={false} />
                <XAxis
                  type="number"
                  domain={[0, trainNeeded]}
                  tick={{ fill: '#475569', fontSize: 10, fontFamily: 'JetBrains Mono' }}
                  axisLine={false}
                  tickLine={false}
                />
                <YAxis
                  type="category"
                  dataKey="label"
                  tick={{ fill: '#94a3b8', fontSize: 11, fontFamily: 'JetBrains Mono' }}
                  axisLine={false}
                  tickLine={false}
                  width={72}
                />
                <Tooltip
                  contentStyle={{
                    background: '#111827', border: '1px solid #1e293b',
                    borderRadius: '8px', fontSize: '11px', fontFamily: 'JetBrains Mono', color: '#e2e8f0',
                  }}
                  formatter={(val: number) => [`${val} bars`, '']}
                />
                <Bar dataKey="value" radius={[0, 4, 4, 0]} maxBarSize={32}>
                  {roadmapData.map((entry, i) => (
                    <Cell key={i} fill={entry.positive ? '#00ff87' : '#334155'} opacity={0.8} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>

          {/* Activation estimate */}
          <div className="card p-4 flex flex-col justify-between">
            <h3 className="text-[10px] text-[#475569] uppercase tracking-widest font-medium mb-3">
              Activation Estimate
            </h3>
            <div className="space-y-3">
              <div>
                <p className="text-[10px] text-[#475569] uppercase tracking-wider">Status</p>
                <p className="text-sm font-semibold mt-0.5" style={{ color: phase === 'ready' ? '#00ff87' : '#f59e0b' }}>
                  {phase === 'ready' ? '✓ Data collection complete' : `${remaining} bars still needed`}
                </p>
              </div>
              <div>
                <p className="text-[10px] text-[#475569] uppercase tracking-wider">
                  {phase === 'ready' ? 'Trains at next open' : 'Sessions remaining'}
                </p>
                <p className="text-sm font-mono font-semibold text-[#e2e8f0] mt-0.5">
                  {phase === 'ready'
                    ? nextOpenStr
                    : `~${Math.ceil(sessionsRemaining)} session${Math.ceil(sessionsRemaining) !== 1 ? 's' : ''}`}
                </p>
              </div>
              {phase !== 'ready' && (
                <div>
                  <p className="text-[10px] text-[#475569] uppercase tracking-wider">Est. activation date</p>
                  <p className="text-sm font-mono text-[#94a3b8] mt-0.5">
                    {(() => {
                      const sessionsLeft = Math.ceil(sessionsRemaining)
                      const d = new Date(now)
                      let added = 0
                      while (added < sessionsLeft) {
                        d.setDate(d.getDate() + 1)
                        const day = d.toLocaleDateString('en-US', { weekday: 'long', timeZone: 'America/New_York' })
                        if (day !== 'Saturday' && day !== 'Sunday') added++
                      }
                      return d.toLocaleDateString('en-US', {
                        weekday: 'short', month: 'short', day: 'numeric',
                        timeZone: 'America/New_York',
                      })
                    })()}
                  </p>
                </div>
              )}
              <div>
                <p className="text-[10px] text-[#475569] uppercase tracking-wider">Market</p>
                <p className={clsx('text-sm font-mono font-semibold mt-0.5', nyseOpen ? 'text-[#00ff87]' : 'text-[#475569]')}>
                  NYSE {nyseOpen ? 'OPEN' : 'CLOSED'} · {market.label} {countdown}
                </p>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Current regime cards — only once trained */}
      {assets.length > 0 && (
        <div>
          <h2 className="text-[10px] text-[#475569] uppercase tracking-widest font-medium mb-3">
            Current Regime State
          </h2>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {assets.map(([asset, info]) => {
              const s = REGIME_STYLE[info.regime] ?? REGIME_STYLE.UNKNOWN
              const confPct = Math.round(info.confidence * 100)
              return (
                <div
                  key={asset}
                  className="rounded-xl p-5 border space-y-3"
                  style={{ background: s.bg, borderColor: s.border }}
                >
                  <div className="flex items-center justify-between">
                    <span className="text-2xl font-bold text-[#e2e8f0]">{asset}</span>
                    <span
                      className="px-3 py-1 rounded-lg text-sm font-mono font-bold"
                      style={{ color: s.text, background: s.bg, border: `1px solid ${s.border}` }}
                    >
                      {info.regime.replace('_', ' ')}
                    </span>
                  </div>
                  <div>
                    <div className="flex items-center justify-between text-xs font-mono text-[#64748b] mb-1.5">
                      <span>Confidence</span>
                      <span style={{ color: s.text }}>{confPct}%</span>
                    </div>
                    <div className="w-full bg-[#1e293b] rounded-full h-2 overflow-hidden">
                      <div
                        className="h-full rounded-full transition-all duration-500"
                        style={{ width: `${confPct}%`, background: s.text }}
                      />
                    </div>
                  </div>
                  {info.strategy && (
                    <div className="text-xs text-[#64748b]">
                      Strategy: <span className="text-[#94a3b8]">{info.strategy}</span>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Confidence trend */}
      {history.length > 0 && <ConfidenceTrend history={history} />}

      {/* Regime statistics */}
      {regimeStatRows.length > 0 && (
        <div className="card p-4">
          <h3 className="text-[10px] text-[#475569] uppercase tracking-widest font-medium mb-4">
            Regime Statistics · Last {recentHistory.length} Transitions
          </h3>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mb-4">
            <div className="space-y-0.5">
              <p className="text-[10px] text-[#475569] uppercase tracking-wider">Total Transitions</p>
              <p className="text-xl font-semibold font-mono text-[#e2e8f0]">{totalTransitions}</p>
            </div>
            <div className="space-y-0.5 min-w-0">
              <p className="text-[10px] text-[#475569] uppercase tracking-wider">Most Common</p>
              <p
                className="text-sm font-semibold font-mono mt-1 truncate"
                style={{ color: REGIME_FILL[mostCommon] ?? '#64748b' }}
                title={regimeLabel(mostCommon)}
              >
                {regimeLabel(mostCommon)}
              </p>
            </div>
            <div className="space-y-0.5">
              <p className="text-[10px] text-[#475569] uppercase tracking-wider">Regimes Detected</p>
              <p className="text-xl font-semibold font-mono text-[#e2e8f0]">{regimeStatRows.length}</p>
            </div>
            <div className="space-y-0.5">
              <p className="text-[10px] text-[#475569] uppercase tracking-wider">Avg Confidence</p>
              <p className="text-xl font-semibold font-mono text-[#e2e8f0]">
                {recentHistory.length > 0
                  ? `${(recentHistory.reduce((s: number, r: { confidence: number }) => s + r.confidence, 0) / recentHistory.length * 100).toFixed(0)}%`
                  : '—'}
              </p>
            </div>
          </div>

          {/* Per-regime breakdown — horizontal scroll on narrow screens */}
          <div className="overflow-x-auto -mx-4 sm:mx-0">
            <table className="w-full text-sm min-w-[420px] sm:min-w-0">
              <thead>
                <tr className="text-[10px] text-[#475569] uppercase tracking-widest">
                  <th className="text-left pb-2 pl-4 sm:pl-0 font-medium">Regime</th>
                  <th className="text-right pb-2 font-medium">Trans.</th>
                  <th className="text-right pb-2 font-medium">Share</th>
                  <th className="text-right pb-2 font-medium">Avg Conf</th>
                  <th className="pl-3 pb-2 pr-4 sm:pr-0 font-medium hidden sm:table-cell w-32">Distribution</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-[#1e293b]">
                {regimeStatRows.map((row) => {
                  const color = REGIME_FILL[row.regime] ?? '#64748b'
                  return (
                    <tr key={row.regime}>
                      <td className="py-2 pl-4 sm:pl-0">
                        <span
                          className="px-2 py-0.5 rounded text-[10px] font-mono font-semibold whitespace-nowrap"
                          style={{
                            color,
                            background: `${color}18`,
                            border: `1px solid ${color}40`,
                          }}
                        >
                          {regimeLabel(row.regime)}
                        </span>
                      </td>
                      <td className="py-2 text-right font-mono text-[10px] text-[#94a3b8]">{row.count}</td>
                      <td className="py-2 text-right font-mono text-[10px] text-[#94a3b8]">{row.pct.toFixed(0)}%</td>
                      <td className="py-2 text-right font-mono text-[10px] text-[#94a3b8] pr-4 sm:pr-0">{row.avgConf.toFixed(0)}%</td>
                      <td className="py-2 pl-3 hidden sm:table-cell">
                        <div className="w-full bg-[#1e293b] rounded-full h-1.5 overflow-hidden">
                          <div
                            className="h-full rounded-full"
                            style={{ width: `${row.pct}%`, background: color, opacity: 0.75 }}
                          />
                        </div>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* HMM config */}
      <div className="card p-4">
        <h2 className="text-[10px] text-[#475569] uppercase tracking-widest font-medium mb-4">
          HMM Model Configuration
        </h2>
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-4 text-sm">
          {[
            ['Components (candidates)', '3, 4, 5'],
            ['Default components', '4'],
            ['Covariance type', 'full'],
            ['Max iterations', '200'],
            ['Convergence tol', '1e-4'],
            ['Init restarts', '10'],
            ['Min train bars', '390 (1 NYSE session)'],
            ['Retrain interval', '390 bars (daily)'],
            ['Stability confirm', '3 bars'],
            ['Confidence threshold', '0.55'],
            ['Flicker window', '20 bars'],
            ['Flicker rate limit', '2.0 changes/window'],
          ].map(([label, value]) => (
            <div key={label} className="space-y-0.5">
              <p className="text-[10px] text-[#475569] uppercase tracking-wider">{label}</p>
              <p className="text-[#94a3b8] font-mono text-xs">{value}</p>
            </div>
          ))}
        </div>
      </div>

      {/* Regime distribution + history timeline */}
      <RegimeTimeline history={history} />
    </div>
  )
}
