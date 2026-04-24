import { Position } from '@/lib/types'
import { currency, pct } from '@/lib/format'
import clsx from 'clsx'

interface Props {
  positions: Record<string, Position>
}

export default function PositionsTable({ positions }: Props) {
  const rows = Object.entries(positions)

  return (
    <div className="card p-4">
      <h2 className="text-[10px] text-[#475569] uppercase tracking-widest font-medium mb-4">
        Open Positions
        {rows.length > 0 && (
          <span className="ml-2 text-[#94a3b8] normal-case text-xs">{rows.length}</span>
        )}
      </h2>

      {rows.length === 0 ? (
        <p className="text-[#475569] text-sm">No open positions</p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-sm min-w-[640px]">
            <thead>
              <tr className="text-[10px] text-[#475569] uppercase tracking-widest">
                {['Asset', 'Dir', 'Entry', 'Current', 'Unreal. P&L', 'Stop', 'TP', 'Bars'].map(
                  (h, i) => (
                    <th
                      key={h}
                      className={clsx('pb-2 font-medium', i >= 2 ? 'text-right' : 'text-left')}
                    >
                      {h}
                    </th>
                  )
                )}
              </tr>
            </thead>
            <tbody className="divide-y divide-[#1e293b]">
              {rows.map(([asset, pos]) => (
                <tr key={asset}>
                  <td className="py-2.5 font-semibold text-[#e2e8f0]">{asset}</td>
                  <td className="py-2.5">
                    <span
                      className={clsx(
                        'px-2 py-0.5 rounded text-[11px] font-mono font-semibold',
                        pos.direction === 'LONG'
                          ? 'bg-[#00ff87]/10 text-[#00ff87]'
                          : 'bg-[#ff4d6d]/10 text-[#ff4d6d]'
                      )}
                    >
                      {pos.direction === 'LONG' ? '↑' : '↓'} {pos.direction}
                    </span>
                  </td>
                  <td className="py-2.5 text-right font-mono text-[#94a3b8]">
                    {currency(pos.entry_price)}
                  </td>
                  <td className="py-2.5 text-right font-mono text-[#e2e8f0]">
                    {currency(pos.current_price)}
                  </td>
                  <td
                    className={clsx(
                      'py-2.5 text-right font-mono font-semibold',
                      pos.unrealised_pnl_pct >= 0 ? 'text-[#00ff87]' : 'text-[#ff4d6d]'
                    )}
                  >
                    {pct(pos.unrealised_pnl_pct)}
                  </td>
                  <td className="py-2.5 text-right font-mono text-[#475569]">
                    {currency(pos.stop_price)}
                  </td>
                  <td className="py-2.5 text-right font-mono text-[#475569]">
                    {currency(pos.take_profit_price)}
                  </td>
                  <td className="py-2.5 text-right font-mono text-[#475569]">{pos.bars_held}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
