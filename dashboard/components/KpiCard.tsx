import clsx from 'clsx'

interface Props {
  label: string
  value: string
  sub?: string
  positive?: boolean
  mono?: boolean
  accent?: string
}

export default function KpiCard({ label, value, sub, positive, mono, accent }: Props) {
  const valueColor =
    accent ??
    (positive === true
      ? '#00ff87'
      : positive === false
      ? '#ff4d6d'
      : '#e2e8f0')

  return (
    <div className="card p-4 space-y-1.5">
      <p className="text-[10px] text-[#475569] uppercase tracking-widest font-medium">{label}</p>
      <p
        className={clsx('text-2xl font-semibold leading-none tracking-tight', mono && 'font-mono')}
        style={{ color: valueColor }}
      >
        {value}
      </p>
      {sub && <p className="text-xs text-[#64748b]">{sub}</p>}
    </div>
  )
}
