'use client'

import { useEffect, useRef, useState } from 'react'
import { CONFIRMATION_PHRASE } from '@/lib/switch-live'
import clsx from 'clsx'

interface Props {
  equity: number
  onClose: () => void
  onSuccess: () => void
}

type Step = 'warning' | 'confirm' | 'executing' | 'done' | 'error'

const DANGER_DELAY = 8 // seconds before "Continue" becomes available

export default function LiveTradingModal({ equity, onClose, onSuccess }: Props) {
  const [step, setStep]         = useState<Step>('warning')
  const [countdown, setCountdown] = useState(DANGER_DELAY)
  const [phrase, setPhrase]     = useState('')
  const [result, setResult]     = useState<{ message?: string; detail?: string; cmd?: string }>({})
  const inputRef = useRef<HTMLInputElement>(null)

  // Countdown timer for the warning step
  useEffect(() => {
    if (step !== 'warning') return
    if (countdown <= 0) return
    const id = setInterval(() => setCountdown((c) => c - 1), 1000)
    return () => clearInterval(id)
  }, [step, countdown])

  // Focus input when confirmation step appears
  useEffect(() => {
    if (step === 'confirm') inputRef.current?.focus()
  }, [step])

  // Block body scroll while open
  useEffect(() => {
    document.body.style.overflow = 'hidden'
    return () => { document.body.style.overflow = '' }
  }, [])

  const phraseOk = phrase.trim() === CONFIRMATION_PHRASE

  async function execute() {
    if (!phraseOk) return
    setStep('executing')
    try {
      const res = await fetch('/api/control/switch-live', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phrase }),
      })
      const data = await res.json()
      if (!res.ok) {
        setResult({ message: data.error, detail: data.detail, cmd: data.cmd })
        setStep('error')
      } else {
        setResult({ message: data.message, cmd: data.cmd })
        setStep('done')
        onSuccess()
      }
    } catch (e: unknown) {
      setResult({ message: e instanceof Error ? e.message : 'Network error' })
      setStep('error')
    }
  }

  return (
    /* Backdrop */
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: 'rgba(0,0,0,0.85)' }}
      onClick={(e) => { if (e.target === e.currentTarget && step !== 'executing') onClose() }}
    >
      <div
        className="relative w-full max-w-lg rounded-2xl border overflow-hidden"
        style={{
          background: '#0d1117',
          borderColor: step === 'done' ? 'rgba(0,255,135,0.3)' : 'rgba(255,77,109,0.35)',
          boxShadow: step === 'done'
            ? '0 0 40px rgba(0,255,135,0.1)'
            : '0 0 40px rgba(255,77,109,0.15)',
        }}
      >
        {/* ── Step 1: Warning ────────────────────────────────────────────────── */}
        {step === 'warning' && (
          <div className="p-6 space-y-5">
            {/* Header */}
            <div className="flex items-start gap-3">
              <div className="text-2xl mt-0.5 shrink-0">⚠️</div>
              <div>
                <h2 className="text-lg font-bold text-[#ff4d6d]">Switch to Live Trading</h2>
                <p className="text-xs text-[#64748b] mt-0.5">
                  This action uses real money and cannot be reversed without SSH access to the Pi.
                </p>
              </div>
            </div>

            {/* Risk items */}
            <div className="rounded-xl border border-[#ff4d6d]/25 bg-[#ff4d6d]/06 p-4 space-y-2">
              {[
                ['💰', 'Real money at risk', `Current equity: $${equity.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`],
                ['🔄', 'Containers will restart', 'Both trader and dashboard will go offline briefly'],
                ['⚡', 'Orders execute immediately', 'The engine will place real orders at market open'],
                ['🔒', 'No undo button', 'Reverting requires SSH — edit .env manually on the Pi'],
              ].map(([icon, title, sub]) => (
                <div key={title as string} className="flex items-start gap-2.5">
                  <span className="text-sm shrink-0 mt-0.5">{icon}</span>
                  <div>
                    <p className="text-sm font-semibold text-[#e2e8f0]">{title}</p>
                    <p className="text-xs text-[#64748b]">{sub}</p>
                  </div>
                </div>
              ))}
            </div>

            {/* Countdown guard */}
            <p className="text-xs text-[#475569] text-center">
              {countdown > 0
                ? `Read the warnings above — you can continue in ${countdown}s`
                : 'You have acknowledged the risks above.'}
            </p>

            <div className="flex gap-3">
              <button
                onClick={onClose}
                className="flex-1 py-2.5 rounded-lg border border-[#334155] text-sm text-[#94a3b8] hover:border-[#475569] transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={() => setStep('confirm')}
                disabled={countdown > 0}
                className={clsx(
                  'flex-1 py-2.5 rounded-lg text-sm font-semibold transition-all',
                  countdown > 0
                    ? 'bg-[#334155] text-[#475569] cursor-not-allowed'
                    : 'bg-[#ff4d6d]/15 text-[#ff4d6d] border border-[#ff4d6d]/40 hover:bg-[#ff4d6d]/25'
                )}
              >
                {countdown > 0 ? `Continue (${countdown}s)` : 'Continue to Confirmation →'}
              </button>
            </div>
          </div>
        )}

        {/* ── Step 2: Confirmation ────────────────────────────────────────────── */}
        {step === 'confirm' && (
          <div className="p-6 space-y-5">
            <div>
              <h2 className="text-lg font-bold text-[#ff4d6d]">Final Confirmation</h2>
              <p className="text-xs text-[#64748b] mt-0.5">Type the phrase exactly to unlock the execute button.</p>
            </div>

            {/* What will happen */}
            <div className="rounded-xl border border-[#1e293b] bg-[#111827] p-4 space-y-2 text-xs font-mono">
              <p className="text-[#475569] uppercase tracking-widest text-[10px] mb-2">What will execute</p>
              <p className="text-[#94a3b8]">1. Update ALPACA_BASE_URL in .env → live endpoint</p>
              <p className="text-[#94a3b8]">2. Run:</p>
              <p className="text-[#f59e0b] pl-4 break-all">
                docker compose up -d --force-recreate trader dashboard
              </p>
              <p className="text-[#94a3b8]">3. Dashboard will reconnect automatically (~15 seconds)</p>
            </div>

            {/* Phrase input */}
            <div className="space-y-2">
              <label className="text-[10px] text-[#475569] uppercase tracking-widest font-medium">
                Type to confirm:&nbsp;
                <span className="text-[#ff4d6d] font-mono normal-case tracking-normal">
                  {CONFIRMATION_PHRASE}
                </span>
              </label>
              <input
                ref={inputRef}
                type="text"
                value={phrase}
                onChange={(e) => setPhrase(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') execute() }}
                placeholder={CONFIRMATION_PHRASE}
                spellCheck={false}
                autoComplete="off"
                className={clsx(
                  'w-full px-3 py-2.5 rounded-lg text-sm font-mono bg-[#1a2236] border outline-none transition-colors',
                  phraseOk
                    ? 'border-[#ff4d6d]/60 text-[#ff4d6d]'
                    : 'border-[#334155] text-[#e2e8f0]'
                )}
              />
              {phrase.length > 0 && !phraseOk && (
                <p className="text-[10px] text-[#475569]">Keep typing — phrase must match exactly</p>
              )}
              {phraseOk && (
                <p className="text-[10px] text-[#ff4d6d]">✓ Phrase confirmed — execute button is now active</p>
              )}
            </div>

            <div className="flex gap-3">
              <button
                onClick={() => { setStep('warning'); setPhrase('') }}
                className="flex-1 py-2.5 rounded-lg border border-[#334155] text-sm text-[#94a3b8] hover:border-[#475569] transition-colors"
              >
                ← Back
              </button>
              <button
                onClick={execute}
                disabled={!phraseOk}
                className={clsx(
                  'flex-1 py-2.5 rounded-lg text-sm font-bold transition-all',
                  phraseOk
                    ? 'bg-[#ff4d6d] text-white hover:bg-[#e63659] shadow-lg shadow-[#ff4d6d]/20'
                    : 'bg-[#334155] text-[#475569] cursor-not-allowed'
                )}
              >
                Execute — Switch to Live
              </button>
            </div>
          </div>
        )}

        {/* ── Step 3: Executing ───────────────────────────────────────────────── */}
        {step === 'executing' && (
          <div className="p-8 flex flex-col items-center gap-4 text-center">
            <div className="w-12 h-12 rounded-full border-2 border-[#ff4d6d]/30 border-t-[#ff4d6d] animate-spin" />
            <div>
              <p className="font-semibold text-[#e2e8f0]">Switching to Live Trading…</p>
              <p className="text-xs text-[#475569] mt-1">
                Updating .env and running docker compose · please wait up to 2 minutes
              </p>
            </div>
          </div>
        )}

        {/* ── Step 4: Done ────────────────────────────────────────────────────── */}
        {step === 'done' && (
          <div className="p-6 space-y-4">
            <div className="flex items-center gap-3">
              <span className="text-2xl">✅</span>
              <div>
                <h2 className="text-lg font-bold text-[#00ff87]">Switched to Live Trading</h2>
                <p className="text-xs text-[#64748b] mt-0.5">{result.message}</p>
              </div>
            </div>
            <div className="rounded-xl border border-[#00ff87]/20 bg-[#00ff87]/05 p-3 space-y-1 text-xs">
              <p className="text-[#475569] uppercase tracking-widest text-[10px]">Command executed</p>
              <p className="font-mono text-[#64748b] break-all">{result.cmd}</p>
            </div>
            <p className="text-xs text-[#475569]">
              The dashboard will reconnect automatically once the containers restart (~15 seconds).
              If this page does not reload, refresh your browser.
            </p>
            <button
              onClick={onClose}
              className="w-full py-2.5 rounded-lg bg-[#00ff87]/10 text-[#00ff87] border border-[#00ff87]/30 text-sm font-semibold hover:bg-[#00ff87]/20 transition-colors"
            >
              Close
            </button>
          </div>
        )}

        {/* ── Step 5: Error ────────────────────────────────────────────────────── */}
        {step === 'error' && (
          <div className="p-6 space-y-4">
            <div className="flex items-center gap-3">
              <span className="text-2xl">❌</span>
              <div>
                <h2 className="text-lg font-bold text-[#ff4d6d]">Switch Failed</h2>
                <p className="text-xs text-[#64748b] mt-0.5">.env has been rolled back — no changes made</p>
              </div>
            </div>
            <div className="rounded-xl border border-[#ff4d6d]/25 bg-[#ff4d6d]/06 p-3 space-y-2 text-xs font-mono">
              <p className="text-[#ff4d6d]">{result.message}</p>
              {result.detail && (
                <p className="text-[#475569] whitespace-pre-wrap break-all">{result.detail}</p>
              )}
              {result.cmd && (
                <>
                  <p className="text-[#334155] uppercase tracking-widest text-[10px] mt-1">Command attempted</p>
                  <p className="text-[#64748b] break-all">{result.cmd}</p>
                </>
              )}
            </div>
            <p className="text-xs text-[#475569]">
              Common causes: <code className="text-[#94a3b8]">HOST_PROJECT_DIR</code> not set in .env,
              Docker socket not mounted, or dashboard container lacks docker group access.
            </p>
            <div className="flex gap-3">
              <button
                onClick={onClose}
                className="flex-1 py-2.5 rounded-lg border border-[#334155] text-sm text-[#94a3b8]"
              >
                Close
              </button>
              <button
                onClick={() => { setStep('warning'); setPhrase(''); setCountdown(DANGER_DELAY) }}
                className="flex-1 py-2.5 rounded-lg border border-[#ff4d6d]/40 text-sm text-[#ff4d6d] hover:bg-[#ff4d6d]/10 transition-colors"
              >
                Try Again
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
