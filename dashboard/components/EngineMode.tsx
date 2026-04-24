import { SharedState } from '@/lib/types'

interface Props {
  state?: SharedState
}

type Mode = 'offline' | 'waiting' | 'training' | 'trading'

function resolveMode(state?: SharedState): Mode {
  if (!state) return 'offline'
  if (state.hmm_trained) return 'trading'
  if (state.training_bars > 0) return 'training'
  return 'waiting'
}

const MODES: Record<
  Mode,
  { label: string; color: string; bg: string; border: string; dot: string }
> = {
  offline: {
    label: 'Engine Offline',
    color: '#64748b',
    bg: 'rgba(100,116,139,0.07)',
    border: 'rgba(100,116,139,0.2)',
    dot: 'bg-[#64748b]',
  },
  waiting: {
    label: 'Waiting — Market Closed',
    color: '#f59e0b',
    bg: 'rgba(245,158,11,0.07)',
    border: 'rgba(245,158,11,0.2)',
    dot: 'amber-dot bg-[#f59e0b]',
  },
  training: {
    label: 'Accumulating Training Data',
    color: '#f59e0b',
    bg: 'rgba(245,158,11,0.07)',
    border: 'rgba(245,158,11,0.2)',
    dot: 'amber-dot bg-[#f59e0b]',
  },
  trading: {
    label: 'Auto-Trading ✦ Active',
    color: '#00ff87',
    bg: 'rgba(0,255,135,0.06)',
    border: 'rgba(0,255,135,0.2)',
    dot: 'live-dot bg-[#00ff87]',
  },
}

export default function EngineMode({ state }: Props) {
  const mode = resolveMode(state)
  const cfg = MODES[mode]
  const bars = state?.training_bars ?? 0
  const needed = state?.training_needed ?? 390
  const pctRaw = needed > 0 ? Math.min((bars / needed) * 100, 100) : 100

  return (
    <div
      className="rounded-xl p-4 border"
      style={{ background: cfg.bg, borderColor: cfg.border }}
    >
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div className="flex items-center gap-3">
          <span className={`w-2.5 h-2.5 rounded-full shrink-0 ${cfg.dot}`} />
          <span className="font-semibold text-sm" style={{ color: cfg.color }}>
            {cfg.label}
            {mode === 'training' && (
              <span className="font-mono font-normal ml-2 text-xs opacity-75">
                {bars} / {needed} bars ({pctRaw.toFixed(0)}%)
              </span>
            )}
          </span>
        </div>
        {mode === 'trading' && (
          <span className="text-xs font-mono text-[#64748b]">HMM trained · live signals</span>
        )}
        {mode === 'offline' && (
          <span className="text-xs text-[#64748b]">Waiting for shared_state.json</span>
        )}
      </div>

      {mode === 'training' && (
        <div className="mt-3 space-y-1">
          <div className="w-full bg-[#1e293b] rounded-full h-1.5 overflow-hidden">
            <div
              className="h-full rounded-full transition-all duration-700 ease-out"
              style={{ width: `${pctRaw}%`, background: cfg.color }}
            />
          </div>
        </div>
      )}
    </div>
  )
}
