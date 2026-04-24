'use client'

import { useEffect, useState } from 'react'
import { SharedState, Meta } from '@/lib/types'
import { etTime, sastTime, isNyseOpen } from '@/lib/format'
import clsx from 'clsx'

interface Props {
  state?: SharedState
  meta?: Meta
}

export default function TopBar({ meta }: Props) {
  const [now, setNow] = useState(new Date())

  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(id)
  }, [])

  const nyseOpen = isNyseOpen()
  const mode = meta?.mode ?? 'PAPER'

  return (
    <header className="sticky top-0 z-50 bg-[#0a0e1a]/95 backdrop-blur-sm border-b border-[#1e293b]">
      <div className="max-w-[1600px] mx-auto px-4 sm:px-6 h-14 flex items-center gap-3 sm:gap-5">

        {/* Brand */}
        <div className="flex items-center gap-2 shrink-0">
          <span className="text-[#00ff87] text-base leading-none select-none">◆</span>
          <span className="font-semibold text-sm text-[#e2e8f0]">
            ML Trader{' '}
            <span className="text-[#64748b] font-normal hidden sm:inline">Diablo v1</span>
          </span>
        </div>

        <div className="flex-1" />

        {/* NYSE badge */}
        <div className="flex items-center gap-1.5 shrink-0">
          <span
            className={clsx(
              'w-2 h-2 rounded-full shrink-0',
              nyseOpen ? 'bg-[#00ff87] live-dot' : 'bg-[#475569]'
            )}
          />
          <span
            className={clsx(
              'text-xs font-mono font-medium',
              nyseOpen ? 'text-[#00ff87]' : 'text-[#475569]'
            )}
          >
            NYSE&nbsp;{nyseOpen ? 'OPEN' : 'CLOSED'}
          </span>
        </div>

        {/* Clocks — hidden on small mobile */}
        <div className="hidden sm:flex items-center gap-4 shrink-0">
          <ClockBlock label="ET" time={etTime(now)} />
          <ClockBlock label="SAST" time={sastTime(now)} />
        </div>

        {/* Mode badge */}
        <span
          className={clsx(
            'px-2.5 py-1 rounded-md text-xs font-mono font-bold shrink-0',
            mode === 'LIVE'
              ? 'bg-[#ff4d6d]/15 text-[#ff4d6d] border border-[#ff4d6d]/30'
              : 'bg-[#f59e0b]/15 text-[#f59e0b] border border-[#f59e0b]/30'
          )}
        >
          {mode}
        </span>
      </div>
    </header>
  )
}

function ClockBlock({ label, time }: { label: string; time: string }) {
  return (
    <div className="flex items-center gap-1.5">
      <span className="text-[10px] text-[#475569] uppercase tracking-widest font-medium">
        {label}
      </span>
      <span className="text-xs font-mono text-[#94a3b8]">{time}</span>
    </div>
  )
}
