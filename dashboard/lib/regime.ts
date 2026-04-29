/**
 * Strip the "RegimeLabel." prefix that older regime.log entries contain
 * (legacy str(enum) serialization). Returns the raw label key — e.g.
 * "TRENDING_DOWN".
 *
 * Newer entries already store the bare value, so this is a no-op for them.
 */
export function normalizeRegime(raw: string | null | undefined): string {
  if (!raw) return 'UNKNOWN'
  return raw.replace(/^RegimeLabel\./, '').trim() || 'UNKNOWN'
}

/** Human-friendly display: "TRENDING_UP" → "TRENDING UP". */
export function regimeLabel(raw: string | null | undefined): string {
  return normalizeRegime(raw).replace(/_/g, ' ')
}

export const REGIME_COLOR: Record<string, string> = {
  TRENDING_UP:   '#00ff87',
  TRENDING_DOWN: '#ff4d6d',
  BREAKOUT:      '#60a5fa',
  SQUEEZE:       '#fbbf24',
  CHOPPY:        '#f97316',
  UNKNOWN:       '#64748b',
}

export function regimeColor(raw: string | null | undefined): string {
  return REGIME_COLOR[normalizeRegime(raw)] ?? REGIME_COLOR.UNKNOWN
}
