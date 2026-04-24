import { Trade } from '@/lib/types'
import { currency, pct, shortTime } from '@/lib/format'
import clsx from 'clsx'

interface Props {
  trades: Trade[]
  showAll?: boolean
}

const REGIME_COLOR: Record<string, string> = {
  TRENDING_UP:   'text-[#00ff87]',
  TRENDING_DOWN: 'text-[#ff4d6d]',
  BREAKOUT:      'text-[#60a5fa]',
  SQUEEZE:       'text-[#fbbf24]',
  CHOPPY:        'text-[#f97316]',
  UNKNOWN:       'text-[#475569]',
}

export default function TradeHistory({ trades, showAll = false }: Props) {
  const title = showAll ? 'Full Trade History' : 'Trade History — Today'

  if (trades.length === 0) {
    return (
      <div className="card p-4">
        <h2 className="text-[10px] text-[#475569] uppercase tracking-widest font-medium mb-4">
          {title}
        </h2>
        <p className="text-[#475569] text-sm">{showAll ? 'No trades recorded' : 'No completed trades today'}</p>
      </div>
    )
  }

  const totalPnl = trades.reduce((s, t) => s + t.pnl_dollar, 0)
  const winners = trades.filter((t) => t.pnl_dollar > 0).length
  const winRate = winners / trades.length

  return (
    <div className="card p-4">
      <div className="flex items-center justify-between flex-wrap gap-2 mb-4">
        <h2 className="text-[10px] text-[#475569] uppercase tracking-widest font-medium">
          {title}
        </h2>
        <div className="flex items-center gap-4 text-xs font-mono">
          <span className="text-[#64748b]">
            {winners}W / {trades.length - winners}L{' '}
            <span
              className={clsx(
                'font-semibold',
                winRate >= 0.5 ? 'text-[#00ff87]' : 'text-[#ff4d6d]'
              )}
            >
              ({(winRate * 100).toFixed(0)}%)
            </span>
          </span>
          <span
            className={clsx('font-semibold', totalPnl >= 0 ? 'text-[#00ff87]' : 'text-[#ff4d6d]')}
          >
            {totalPnl >= 0 ? '+' : ''}
            {currency(totalPnl)}
          </span>
        </div>
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-sm min-w-[800px]">
          <thead>
            <tr className="text-[10px] text-[#475569] uppercase tracking-widest">
              {[
                ['Time', 'left'],
                ['Asset', 'left'],
                ['Dir', 'left'],
                ['Entry', 'right'],
                ['Exit', 'right'],
                ['P&L %', 'right'],
                ['P&L $', 'right'],
                ['Regime', 'left'],
                ['Exit Reason', 'left'],
                ['Bars', 'right'],
              ].map(([h, align]) => (
                <th
                  key={h}
                  className={clsx('pb-2 font-medium', align === 'right' ? 'text-right' : 'text-left')}
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody className="divide-y divide-[#1e293b]">
            {[...trades].reverse().map((t, i) => (
              <tr key={i}>
                <td className="py-2 font-mono text-[#475569]">{shortTime(t.exit_time)}</td>
                <td className="py-2 font-semibold text-[#e2e8f0]">{t.asset}</td>
                <td className="py-2">
                  <span
                    className={clsx(
                      'px-1.5 py-0.5 rounded text-[11px] font-mono font-semibold',
                      t.direction === 'LONG'
                        ? 'bg-[#00ff87]/10 text-[#00ff87]'
                        : 'bg-[#ff4d6d]/10 text-[#ff4d6d]'
                    )}
                  >
                    {t.direction === 'LONG' ? '↑' : '↓'} {t.direction}
                  </span>
                </td>
                <td className="py-2 text-right font-mono text-[#94a3b8]">
                  {currency(t.entry_price)}
                </td>
                <td className="py-2 text-right font-mono text-[#94a3b8]">
                  {currency(t.exit_price)}
                </td>
                <td
                  className={clsx(
                    'py-2 text-right font-mono font-semibold',
                    t.pnl_pct >= 0 ? 'text-[#00ff87]' : 'text-[#ff4d6d]'
                  )}
                >
                  {pct(t.pnl_pct)}
                </td>
                <td
                  className={clsx(
                    'py-2 text-right font-mono font-semibold',
                    t.pnl_dollar >= 0 ? 'text-[#00ff87]' : 'text-[#ff4d6d]'
                  )}
                >
                  {t.pnl_dollar >= 0 ? '+' : ''}
                  {currency(t.pnl_dollar)}
                </td>
                <td
                  className={clsx(
                    'py-2 text-[11px] font-mono',
                    REGIME_COLOR[t.regime_at_entry] ?? 'text-[#475569]'
                  )}
                >
                  {(t.regime_at_entry ?? '—').replace('_', ' ')}
                </td>
                <td className="py-2 text-[11px] text-[#475569]">{t.exit_reason}</td>
                <td className="py-2 text-right font-mono text-[#475569]">{t.hold_bars}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
