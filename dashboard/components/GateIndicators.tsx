'use client'

import { useEffect, useState } from 'react'
import type { SharedState, Signal } from '@/lib/types'
import { isNyseOpen, nextMarketEvent } from '@/lib/format'
import { normalizeRegime } from '@/lib/regime'
import clsx from 'clsx'

interface Props {
  state?: SharedState
}

interface Gate {
  label: string
  ok: boolean
  detail?: string
}

const HMM_CONFIDENCE_THRESHOLD = 0.55
const SOFT_CLOSE_MINUTES       = 10
const HARD_CLOSE_MINUTES       = 5

/** Inspect last signal entries to derive whether HMM is confirmed and LGBM is non-flat. */
function deriveSignalGates(signals: Signal[]): { hmmConfirmed: boolean; lgbmDirectional: boolean; lastReason?: string } {
  if (signals.length === 0) return { hmmConfirmed: false, lgbmDirectional: false }
  const recent = signals.slice(-3)
  const reasons = recent.map((s) => (s.reason || '').toUpperCase())
  // A signal with a non-null direction means BOTH gates passed
  const anyEntry = recent.some((s) => s.direction != null)
  const hmmConfirmed   = anyEntry || !reasons.some((r) => r.includes('HMM_NOT_CONFIRMED'))
  const lgbmDirectional = anyEntry || !reasons.some((r) => r.includes('LGBM_FLAT'))
  return { hmmConfirmed, lgbmDirectional, lastReason: reasons[reasons.length - 1] }
}

export default function GateIndicators({ state }: Props) {
  const [now, setNow] = useState(new Date())
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(id)
  }, [])

  const nyseOpen = isNyseOpen(now)
  const market   = nextMarketEvent(now)
  const minutesToClose = market.isOpen ? market.seconds / 60 : Infinity

  // Pull data from shared_state
  const stateAlive  = !!state
  const warmup      = state?.feature_warmup
    ? Object.values(state.feature_warmup).every((w) => w.ready)
    : false
  const hmmTrained  = state?.hmm_trained ?? false
  const regimeInfo  = state?.regime_info ?? {}
  const anyAsset    = Object.values(regimeInfo)[0]
  const regime      = anyAsset ? normalizeRegime(anyAsset.regime) : 'UNKNOWN'
  const conf        = anyAsset?.confidence ?? 0
  const regimeKnown = regime !== 'UNKNOWN'
  const confOk      = conf >= HMM_CONFIDENCE_THRESHOLD

  const { hmmConfirmed, lgbmDirectional } = deriveSignalGates(state?.last_10_signals ?? [])

  const cb         = state?.circuit_breaker_active ?? false
  const positions  = state ? Object.keys(state.positions).length : 0
  const positionsOk = positions === 0  // free slot available (assuming max 1)

  const inSoftClose = nyseOpen && minutesToClose <= SOFT_CLOSE_MINUTES
  const inHardClose = nyseOpen && minutesToClose <= HARD_CLOSE_MINUTES

  const gates: Gate[] = [
    { label: 'Engine',        ok: stateAlive,                detail: stateAlive ? 'live' : 'offline' },
    { label: 'Market',        ok: nyseOpen,                  detail: nyseOpen ? `${minutesToClose.toFixed(0)}m left` : 'closed' },
    { label: 'Warmup',        ok: warmup,                    detail: warmup ? 'ready' : 'in progress' },
    { label: 'HMM',           ok: hmmTrained,                detail: hmmTrained ? 'trained' : 'not trained' },
    { label: 'Regime',        ok: regimeKnown,               detail: regime },
    { label: 'Conf ≥ 55%',    ok: confOk,                    detail: `${(conf * 100).toFixed(0)}%` },
    { label: 'Stable (3 bar)', ok: hmmConfirmed,             detail: hmmConfirmed ? 'confirmed' : 'flickering' },
    { label: 'LGBM dir.',     ok: lgbmDirectional,           detail: lgbmDirectional ? 'directional' : 'flat' },
    { label: 'No CB',         ok: !cb,                       detail: cb ? 'tripped' : 'ok' },
    { label: 'Free slot',     ok: positionsOk,               detail: `${positions} open` },
    { label: 'No soft-close', ok: !inSoftClose,              detail: inSoftClose ? 'EOD soft close' : 'ok' },
    { label: 'No hard-close', ok: !inHardClose,              detail: inHardClose ? 'EOD hard close' : 'ok' },
  ]

  const allOk    = gates.every((g) => g.ok)
  const passCount = gates.filter((g) => g.ok).length

  return (
    <div
      className="rounded-xl px-3 py-2 border"
      style={{
        background: allOk ? 'rgba(0,255,135,0.04)' : 'rgba(245,158,11,0.04)',
        borderColor: allOk ? 'rgba(0,255,135,0.18)' : 'rgba(245,158,11,0.18)',
      }}
    >
      <div className="flex items-center justify-between mb-1.5">
        <span className="text-[9px] uppercase tracking-widest font-semibold text-[#475569]">
          Trade Entry Gates
        </span>
        <span className={clsx('text-[10px] font-mono tabular-nums',
          allOk ? 'text-[#00ff87]' : 'text-[#f59e0b]'
        )}>
          {passCount}/{gates.length} {allOk ? '✓ READY' : 'BLOCKED'}
        </span>
      </div>

      <div className="grid grid-cols-3 sm:grid-cols-4 md:grid-cols-6 lg:grid-cols-12 gap-x-2 gap-y-1">
        {gates.map((g) => (
          <div key={g.label} className="flex items-center gap-1.5 min-w-0" title={g.detail}>
            <span
              className={clsx(
                'w-1.5 h-1.5 rounded-full shrink-0',
                g.ok ? 'bg-[#00ff87] live-dot' : 'bg-[#ff4d6d] red-dot'
              )}
            />
            <span className={clsx(
              'text-[9px] font-mono truncate leading-tight',
              g.ok ? 'text-[#94a3b8]' : 'text-[#64748b]'
            )}>
              {g.label}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}
