'use client'

import useSWR from 'swr'
import type { SharedState } from '@/lib/types'
import RegimeTimeline from '@/components/RegimeTimeline'
import clsx from 'clsx'

const fetcher = (url: string) => fetch(url).then((r) => r.json())

const REGIME_STYLE: Record<string, { text: string; bg: string; border: string }> = {
  TRENDING_UP:   { text: '#00ff87', bg: 'rgba(0,255,135,0.08)',   border: 'rgba(0,255,135,0.25)'  },
  TRENDING_DOWN: { text: '#ff4d6d', bg: 'rgba(255,77,109,0.08)',  border: 'rgba(255,77,109,0.25)' },
  BREAKOUT:      { text: '#60a5fa', bg: 'rgba(96,165,250,0.08)',  border: 'rgba(96,165,250,0.25)' },
  SQUEEZE:       { text: '#fbbf24', bg: 'rgba(251,191,36,0.08)',  border: 'rgba(251,191,36,0.25)' },
  CHOPPY:        { text: '#f97316', bg: 'rgba(249,115,22,0.08)',  border: 'rgba(249,115,22,0.25)' },
  UNKNOWN:       { text: '#64748b', bg: 'rgba(100,116,139,0.08)', border: 'rgba(100,116,139,0.2)' },
}

export default function HmmPage() {
  const { data: state }   = useSWR<SharedState>('/api/state', fetcher, { refreshInterval: 5000, revalidateOnFocus: false })
  const { data: history = [] } = useSWR('/api/regime-history', fetcher, { refreshInterval: 30000, revalidateOnFocus: false })

  const regimeInfo   = state?.regime_info ?? {}
  const assets       = Object.entries(regimeInfo)
  const trained      = state?.hmm_trained ?? false
  const trainBars    = state?.training_bars ?? 0
  const trainNeeded  = state?.training_needed ?? 390
  const trainPct     = state?.training_pct ?? 0

  return (
    <div className="max-w-[1600px] mx-auto px-4 sm:px-6 py-5 space-y-4">
      <div>
        <h1 className="text-lg font-semibold text-[#e2e8f0]">HMM Regime Analysis</h1>
        <p className="text-xs text-[#475569] mt-0.5">
          Hidden Markov Model · real-time regime detection per asset
        </p>
      </div>

      {/* Training status */}
      <div
        className="rounded-xl p-4 border"
        style={
          trained
            ? { background: 'rgba(0,255,135,0.06)', borderColor: 'rgba(0,255,135,0.2)' }
            : { background: 'rgba(245,158,11,0.06)', borderColor: 'rgba(245,158,11,0.2)' }
        }
      >
        <div className="flex items-center gap-3 mb-2">
          <span
            className={clsx('w-2.5 h-2.5 rounded-full', trained ? 'live-dot bg-[#00ff87]' : 'amber-dot bg-[#f59e0b]')}
          />
          <span className="font-semibold text-sm" style={{ color: trained ? '#00ff87' : '#f59e0b' }}>
            {trained
              ? 'HMM Trained — live regime classification active'
              : `Accumulating training data: ${trainBars} / ${trainNeeded} bars (${trainPct.toFixed(0)}%)`}
          </span>
        </div>
        {!trained && (
          <div className="w-full bg-[#1e293b] rounded-full h-1.5 overflow-hidden">
            <div
              className="h-full rounded-full transition-all duration-700"
              style={{ width: `${Math.min(trainPct, 100)}%`, background: '#f59e0b' }}
            />
          </div>
        )}
        {trained && (
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 mt-3 text-xs font-mono text-[#64748b]">
            <div><span className="text-[#475569]">Model</span><br />HMM + LGBM</div>
            <div><span className="text-[#475569]">Assets</span><br />{Object.keys(regimeInfo).join(', ') || '—'}</div>
            <div><span className="text-[#475569]">Retrain every</span><br />390 bars (1 session)</div>
            <div><span className="text-[#475569]">Conf. threshold</span><br />0.55</div>
          </div>
        )}
      </div>

      {/* Current regimes */}
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
                      style={{ color: s.text, background: `${s.bg}`, border: `1px solid ${s.border}` }}
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

      {/* Regime distribution + history */}
      <RegimeTimeline history={history} />
    </div>
  )
}
