export function currency(n: number): string {
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(n)
}

export function pct(n: number, decimals = 2): string {
  const sign = n >= 0 ? '+' : ''
  return `${sign}${(n * 100).toFixed(decimals)}%`
}

export function signed(n: number): string {
  const sign = n >= 0 ? '+' : '-'
  return `${sign}$${Math.abs(n).toFixed(2)}`
}

export function etTime(d: Date = new Date()): string {
  return d.toLocaleTimeString('en-US', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
    timeZone: 'America/New_York',
  })
}

export function sastTime(d: Date = new Date()): string {
  return d.toLocaleTimeString('en-US', {
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hour12: false,
    timeZone: 'Africa/Johannesburg',
  })
}

export function shortTime(iso: string): string {
  return new Date(iso).toLocaleTimeString('en-US', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: 'America/New_York',
  })
}

export function isNyseOpen(): boolean {
  const now = new Date()
  const day = now.toLocaleDateString('en-US', {
    weekday: 'long',
    timeZone: 'America/New_York',
  })
  if (day === 'Saturday' || day === 'Sunday') return false
  const t = now.toLocaleTimeString('en-US', {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
    timeZone: 'America/New_York',
  })
  return t >= '09:30' && t < '16:00'
}
