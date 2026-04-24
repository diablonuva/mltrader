import { RegimeInfo, Signal } from '@/lib/types'
import clsx from 'clsx'

const REGIME_STYLE: Record<string, { text: string; bg: string; border: string }> = {
  TRENDING_UP:   { text: '#00ff87', bg: 'rgba(0,255,135,0.08)',   border: 'rgba(0,255,135,0.25)'   },
  TRENDING_DOWN: { text: '#ff4d6d', bg: 'rgba(255,77,109,0.08)',  border: 'rgba(255,77,109,0.25)'  },
  BREAKOUT:      { text: '#60a5fa', bg: 'rgba(96,165,250,0.08)',  border: 'rgba(96,165,250,0.25)'  },
  SQUEEZE:       { text: '#fbbf24', bg: 'rgba(251,191,36,0.08)',  border: 'rgba(251,191,36,0.25)'  },
  CHOPPY:        { text: '#f97316', bg: 'rgba(249,115,22,0.08)',  border: 'rgba(249,115,22,0.25)'  },
  UNKNOWN:       { text: '#64748b', bg: 'rgba(100,116,139,0.08)', border: 'rgba(100,116,139,0.25)' },
}

interface Props {
  regimeInfo: Record<string, RegimeInfo>
  signals: Signal[]
}

export default function RegimePanel({ regimeInfo, signals }: Props) {
  const assets = Object.entries(regimeInfo)

  return (
    <div className="card p-4 h-full flex flex-col gap-4">
      <h2 className="text-[10px] text-[#475569] uppercase tracking-widest font-medium">
        HMM Regime
      </h2>

      {assets.length === 0 ? (
        <p className="text-[#475569] text-sm">Training in progress…</p>
      ) : (
        <div className="space-y-4">
          {assets.map(([asset, info]) => {
            const s = REGIME_STYLE[info.regime] ?? REGIME_STYLE.UNKNOWN
            const confPct = Math.round(info.confidence * 100)
            return (
              <div key={asset} className="space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-sm font-semibold text-[#e2e8f0]">{asset}</span>
                  <span
                    className="px-2 py-0.5 rounded-md text-[11px] font-mono font-semibold"
                    style={{ background: s.bg, color: s.text, border: `1px solid ${s.border}` }}
                  >
                    {info.regime.replace('_', ' ')}
                  </span>
                </div>
                <div className="flex items-center gap-2">
                  <div className="flex-1 bg-[#1e293b] rounded-full h-1 overflow-hidden">
                    <div
                      className="h-full rounded-full transition-all duration-500"
                      style={{ width: `${confPct}%`, background: s.text }}
                    />
                  </div>
                  <span className="text-[11px] font-mono text-[#64748b] w-8 text-right">
                    {confPct}%
                  </span>
                </div>
                {info.strategy && (
                  <p className="text-[11px] text-[#475569]">
                    Strategy:{' '}
                    <span className="text-[#64748b]">{info.strategy}</span>
                  </p>
                )}
              </div>
            )
          })}
        </div>
      )}

      {/* Recent signals */}
      {signals.length > 0 && (
        <div className="flex-1 flex flex-col gap-2 border-t border-[#1e293b] pt-3 min-h-0">
          <h3 className="text-[10px] text-[#475569] uppercase tracking-widest font-medium shrink-0">
            Recent Signals
          </h3>
          <div className="space-y-1.5 overflow-y-auto">
            {[...signals].reverse().map((sig, i) => (
              <div key={i} className="flex items-center gap-2 text-[11px]">
                <span
                  className={clsx(
                    'w-1.5 h-1.5 rounded-full shrink-0',
                    sig.direction === 'LONG'
                      ? 'bg-[#00ff87]'
                      : sig.direction === 'SHORT'
                      ? 'bg-[#ff4d6d]'
                      : 'bg-[#475569]'
                  )}
                />
                <span className="font-mono text-[#475569] shrink-0">
                  {new Date(sig.ts).toLocaleTimeString('en-US', {
                    hour: '2-digit',
                    minute: '2-digit',
                    hour12: false,
                    timeZone: 'America/New_York',
                  })}
                </span>
                <span className="text-[#94a3b8] shrink-0">{sig.asset ?? '—'}</span>
                <span
                  className={clsx(
                    'font-mono font-medium shrink-0',
                    sig.direction === 'LONG' ? 'text-[#00ff87]' : 'text-[#ff4d6d]'
                  )}
                >
                  {sig.direction ?? '—'}
                </span>
                <span className="text-[#475569] truncate">{sig.reason}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
