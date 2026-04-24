'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import clsx from 'clsx'

const NAV = [
  { href: '/',            icon: '⬡', label: 'Dashboard'    },
  { href: '/performance', icon: '◈', label: 'Performance'  },
  { href: '/hmm',         icon: '◉', label: 'HMM Analysis' },
  { href: '/settings',    icon: '◎', label: 'Settings'     },
]

interface Props {
  collapsed: boolean
  onCollapse: () => void
  onClose: () => void
}

export default function Sidebar({ collapsed, onCollapse, onClose }: Props) {
  const pathname = usePathname()

  return (
    <div className="flex flex-col h-full bg-[#0d1117] border-r border-[#1e293b]">
      {/* Logo */}
      <div className="h-14 flex items-center px-4 border-b border-[#1e293b] gap-3 shrink-0">
        <span className="text-[#00ff87] text-xl shrink-0 leading-none">◆</span>
        {!collapsed && (
          <>
            <div className="min-w-0">
              <p className="text-sm font-bold text-[#e2e8f0] leading-tight">ML Trader</p>
              <p className="text-[10px] text-[#475569] tracking-wide">Diablo v1</p>
            </div>
            <button
              onClick={onClose}
              className="ml-auto text-[#475569] hover:text-[#64748b] lg:hidden p-1"
              aria-label="Close sidebar"
            >
              ✕
            </button>
          </>
        )}
      </div>

      {/* Nav items */}
      <nav className="flex-1 px-2 py-3 space-y-0.5 overflow-y-auto">
        {!collapsed && (
          <p className="px-3 mb-2 text-[10px] text-[#334155] uppercase tracking-widest font-medium">
            Navigation
          </p>
        )}
        {NAV.map((item) => {
          const active = pathname === item.href
          return (
            <Link
              key={item.href}
              href={item.href}
              onClick={onClose}
              title={collapsed ? item.label : undefined}
              className={clsx(
                'flex items-center rounded-lg transition-colors duration-150',
                collapsed ? 'px-0 py-3 justify-center' : 'px-3 py-2.5 gap-3',
                active
                  ? 'bg-[#00ff87]/10 text-[#00ff87]'
                  : 'text-[#64748b] hover:text-[#94a3b8] hover:bg-[#1a2236]'
              )}
            >
              <span className="text-base leading-none shrink-0">{item.icon}</span>
              {!collapsed && (
                <>
                  <span className="text-sm font-medium truncate flex-1">{item.label}</span>
                  {active && (
                    <span className="w-1.5 h-1.5 rounded-full bg-[#00ff87] shrink-0" />
                  )}
                </>
              )}
            </Link>
          )
        })}
      </nav>

      {/* Divider + version */}
      {!collapsed && (
        <div className="px-4 pb-2 hidden lg:block">
          <p className="text-[10px] text-[#1e293b] font-mono">v1.0 · HMM+LGBM</p>
        </div>
      )}

      {/* Collapse toggle — desktop only */}
      <div className="hidden lg:block border-t border-[#1e293b] p-2 shrink-0">
        <button
          onClick={onCollapse}
          className={clsx(
            'w-full flex items-center rounded-lg px-3 py-2 text-[#475569] hover:text-[#64748b] hover:bg-[#1a2236] transition-colors text-xs gap-2',
            collapsed && 'justify-center'
          )}
          title={collapsed ? 'Expand' : 'Collapse'}
        >
          <span className="text-sm">{collapsed ? '›' : '‹'}</span>
          {!collapsed && <span>Collapse</span>}
        </button>
      </div>
    </div>
  )
}
