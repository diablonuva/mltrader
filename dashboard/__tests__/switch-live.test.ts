import {
  CONFIRMATION_PHRASE,
  validatePhrase,
  updateEnvForLive,
  isAlreadyLive,
  buildComposeCommand,
} from '../lib/switch-live'

// ── validatePhrase ───────────────────────────────────────────────────────────

describe('validatePhrase', () => {
  it('accepts the exact confirmation phrase', () => {
    expect(validatePhrase(CONFIRMATION_PHRASE)).toBe(true)
  })

  it('rejects an empty string', () => {
    expect(validatePhrase('')).toBe(false)
  })

  it('rejects a partial phrase', () => {
    expect(validatePhrase('SWITCH TO LIVE')).toBe(false)
  })

  it('rejects wrong casing (case-sensitive)', () => {
    expect(validatePhrase('switch to live trading')).toBe(false)
    expect(validatePhrase('Switch To Live Trading')).toBe(false)
  })

  it('rejects a phrase with extra words', () => {
    expect(validatePhrase('SWITCH TO LIVE TRADING NOW')).toBe(false)
  })

  it('strips leading/trailing whitespace before comparing', () => {
    expect(validatePhrase(`  ${CONFIRMATION_PHRASE}  `)).toBe(true)
    expect(validatePhrase(`\t${CONFIRMATION_PHRASE}\n`)).toBe(true)
  })
})

// ── updateEnvForLive ─────────────────────────────────────────────────────────

const PAPER_ENV = [
  'ALPACA_API_KEY=PKTEST123',
  'ALPACA_SECRET_KEY=secret456',
  'ALPACA_BASE_URL=https://paper-api.alpaca.markets',
  'ASSETS=SPY,QQQ',
  'TRADING_MODE=paper',
].join('\n')

const LIVE_ENV = [
  'ALPACA_API_KEY=PKTEST123',
  'ALPACA_SECRET_KEY=secret456',
  'ALPACA_BASE_URL=https://api.alpaca.markets',
  'ASSETS=SPY,QQQ',
  'TRADING_MODE=paper',
].join('\n')

describe('updateEnvForLive', () => {
  it('replaces the paper URL with the live URL', () => {
    const result = updateEnvForLive(PAPER_ENV)
    expect(result).toContain('ALPACA_BASE_URL=https://api.alpaca.markets')
  })

  it('removes the paper-api subdomain entirely', () => {
    const result = updateEnvForLive(PAPER_ENV)
    expect(result).not.toContain('paper-api.alpaca.markets')
  })

  it('preserves all other env vars unchanged', () => {
    const result = updateEnvForLive(PAPER_ENV)
    expect(result).toContain('ALPACA_API_KEY=PKTEST123')
    expect(result).toContain('ALPACA_SECRET_KEY=secret456')
    expect(result).toContain('ASSETS=SPY,QQQ')
    expect(result).toContain('TRADING_MODE=paper')
  })

  it('throws if the paper URL is not present (already live or wrong env)', () => {
    expect(() => updateEnvForLive(LIVE_ENV)).toThrow(
      /paper-api\.alpaca\.markets not found/
    )
  })

  it('replaces all occurrences of the exact ALPACA_BASE_URL key', () => {
    // In a malformed .env with the key duplicated, both should be replaced
    const doubled = PAPER_ENV + '\nALPACA_BASE_URL=https://paper-api.alpaca.markets'
    const result  = updateEnvForLive(doubled)
    expect(result).not.toContain('paper-api.alpaca.markets')
  })

  it('is idempotent — calling twice on live env throws on second call', () => {
    const once = updateEnvForLive(PAPER_ENV)
    expect(() => updateEnvForLive(once)).toThrow()
  })
})

// ── isAlreadyLive ────────────────────────────────────────────────────────────

describe('isAlreadyLive', () => {
  it('returns false for paper env', () => {
    expect(isAlreadyLive(PAPER_ENV)).toBe(false)
  })

  it('returns true for live env', () => {
    expect(isAlreadyLive(LIVE_ENV)).toBe(true)
  })

  it('returns true for an env with no ALPACA_BASE_URL at all', () => {
    expect(isAlreadyLive('ALPACA_API_KEY=test')).toBe(true)
  })

  it('is consistent with updateEnvForLive output', () => {
    const updated = updateEnvForLive(PAPER_ENV)
    expect(isAlreadyLive(updated)).toBe(true)
  })
})

// ── buildComposeCommand ──────────────────────────────────────────────────────

describe('buildComposeCommand', () => {
  it('includes --force-recreate trader dashboard', () => {
    const cmd = buildComposeCommand('/home/pi/mltrader')
    expect(cmd).toContain('--force-recreate trader dashboard')
  })

  it('includes -d flag for detached mode', () => {
    const cmd = buildComposeCommand('/home/pi/mltrader')
    expect(cmd).toContain('up -d')
  })

  it('uses HOST_PROJECT_DIR when provided', () => {
    const cmd = buildComposeCommand('/home/pi/mltrader')
    expect(cmd).toContain('/home/pi/mltrader')
    expect(cmd).toContain('--project-directory')
  })

  it('uses fallback /app/project path when no host dir given', () => {
    const cmd = buildComposeCommand()
    expect(cmd).toContain('/app/project/docker-compose.yml')
    expect(cmd).not.toContain('--project-directory')
  })

  it('quotes paths to handle spaces', () => {
    const cmd = buildComposeCommand('/home/pi/my project')
    expect(cmd).toContain('"/home/pi/my project"')
  })
})

// ── Integration: paper → live transition ────────────────────────────────────

describe('paper-to-live transition flow', () => {
  it('full happy path: validate → update → confirm live', () => {
    // Step 1: confirm the phrase is valid
    expect(validatePhrase(CONFIRMATION_PHRASE)).toBe(true)

    // Step 2: confirm we are in paper mode
    expect(isAlreadyLive(PAPER_ENV)).toBe(false)

    // Step 3: perform the env update
    const updated = updateEnvForLive(PAPER_ENV)

    // Step 4: verify result is now live
    expect(isAlreadyLive(updated)).toBe(true)

    // Step 5: verify a second switch attempt would be caught
    expect(isAlreadyLive(updated)).toBe(true) // guard check passes
    expect(() => updateEnvForLive(updated)).toThrow()
  })
})
