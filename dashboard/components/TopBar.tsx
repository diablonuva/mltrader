'use client'

import { useEffect, useState } from 'react'
import useSWR from 'swr'
import { Meta } from '@/lib/types'
import { etTime, sastTime, isNyseOpen } from '@/lib/format'
import clsx from 'clsx'

const fetcher = (url: string) => fetch(url).then((r) => r.json())

interface Props {
  onMenuClick: () => void
}

export default function TopBar({ onMenuClick }: Props) {
  const [now, setNow] = useState(new Date())
  const { data: meta } = useSWR<Meta>('/api/meta', fetcher, {
    refreshInterval: 60000,
    revalidateOnFocus: false,
  })

  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(id)
  }, [])

  const nyseOpen = isNyseOpen()
  const mode = meta?.mode ?? 'PAPER'

  return (
    <header className="shrink-0 h-14 bg-[#0d1117] border-b border-[#1e293b] flex items-center px-4 sm:px-6 gap-3">
      {/* Hamburger — mobile only */}
      <button
        onClick={onMenuClick}
        className="lg:hidden text-[#64748b] hover:text-[#94a3b8] p-1 -ml-1"
        aria-label="Open menu"
      >
        <svg width="20" height="20" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
          <path d="M3 6h14M3 10h14M3 14h14" />
        </svg>
      </button>

      {/* Page title slot (hidden on desktop — sidebar shows brand) */}
      <span className="lg:hidden text-sm font-semibold text-[#e2e8f0]">ML Trader</span>

      <div className="flex-1" />

      {/* NYSE badge */}
      <div className="flex items-center gap-1.5 shrink-0">
        <span
          className={clsx(
            'w-2 h-2 rounded-full shrink-0',
            nyseOpen ? 'bg-[#00ff87] live-dot' : 'bg-[#334155]'
          )}
        />
        <span
          className={clsx(
            'text-xs font-mono font-medium hidden sm:inline',
            nyseOpen ? 'text-[#00ff87]' : 'text-[#475569]'
          )}
        >
          NYSE&nbsp;{nyseOpen ? 'OPEN' : 'CLOSED'}
        </span>
      </div>

      {/* Clocks */}
      <div className="hidden md:flex items-center gap-5 shrink-0">
        <Clock label="ET" time={etTime(now)} />
        <Clock label="SAST" time={sastTime(now)} />
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
    </header>
  )
}

function Clock({ label, time }: { label: string; time: string }) {
  return (
    <div className="flex items-center gap-1.5">
      <span className="text-[10px] text-[#334155] uppercase tracking-widest font-medium">{label}</span>
      <span className="text-xs font-mono text-[#64748b]">{time}</span>
    </div>
  )
}
