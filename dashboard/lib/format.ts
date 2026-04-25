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

export function isNyseOpen(now: Date = new Date()): boolean {
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

/**
 * Returns seconds until the next market transition and whether markets are
 * currently open.  All arithmetic is done in "fake ET local" date objects so
 * relative differences are correct regardless of the system timezone.
 */
export function nextMarketEvent(now: Date = new Date()): {
  label: string
  seconds: number
  isOpen: boolean
} {
  // Create a Date whose *local* fields (getHours etc.) are the ET values.
  // Relative differences between two such dates are correct.
  const etNow = new Date(now.toLocaleString('en-US', { timeZone: 'America/New_York' }))
  const day = etNow.getDay()          // 0=Sun … 6=Sat
  const currentSecs =
    etNow.getHours() * 3600 + etNow.getMinutes() * 60 + etNow.getSeconds()
  const openSecs  = 9 * 3600 + 30 * 60  // 09:30
  const closeSecs = 16 * 3600            // 16:00

  const isWeekday = day >= 1 && day <= 5
  const isOpen    = isWeekday && currentSecs >= openSecs && currentSecs < closeSecs

  if (isOpen) {
    return { label: 'closes', seconds: closeSecs - currentSecs, isOpen: true }
  }

  // Find the next 09:30 on a weekday
  const etTarget = new Date(etNow)
  etTarget.setHours(9, 30, 0, 0)

  const beforeOpenToday = isWeekday && currentSecs < openSecs
  if (!beforeOpenToday) {
    // Move to tomorrow and skip over weekend days
    etTarget.setDate(etTarget.getDate() + 1)
    while (etTarget.getDay() === 0 || etTarget.getDay() === 6) {
      etTarget.setDate(etTarget.getDate() + 1)
    }
    etTarget.setHours(9, 30, 0, 0)
  }

  const diffMs = etTarget.getTime() - etNow.getTime()
  return { label: 'opens', seconds: Math.max(Math.floor(diffMs / 1000), 0), isOpen: false }
}

/** Format a raw second count as "Xh XXm XXs" / "XXm XXs" / "Xd Xh" */
export function formatCountdown(s: number): string {
  if (s <= 0) return '00:00:00'
  const h   = Math.floor(s / 3600)
  const m   = Math.floor((s % 3600) / 60)
  const sec = s % 60
  if (h >= 24) {
    const days = Math.floor(h / 24)
    const hrs  = h % 24
    return `${days}d ${hrs}h ${m.toString().padStart(2, '0')}m`
  }
  if (h > 0) {
    return `${h}h ${m.toString().padStart(2, '0')}m ${sec.toString().padStart(2, '0')}s`
  }
  return `${m.toString().padStart(2, '0')}m ${sec.toString().padStart(2, '0')}s`
}
