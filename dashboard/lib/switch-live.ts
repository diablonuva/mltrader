export const CONFIRMATION_PHRASE = 'SWITCH TO LIVE TRADING'

/** True only when the exact phrase matches (trimmed, case-sensitive). */
export function validatePhrase(phrase: string): boolean {
  return phrase.trim() === CONFIRMATION_PHRASE
}

/**
 * Replaces the Alpaca paper URL with the live URL inside raw .env content.
 * Throws if the paper URL is not found (e.g., already live or wrong env layout).
 */
export function updateEnvForLive(envContent: string): string {
  if (!envContent.includes('paper-api.alpaca.markets')) {
    throw new Error(
      'ALPACA_BASE_URL with paper-api.alpaca.markets not found in .env — already live or env not mounted'
    )
  }
  return envContent.replace(
    /ALPACA_BASE_URL=https:\/\/paper-api\.alpaca\.markets/g,
    'ALPACA_BASE_URL=https://api.alpaca.markets'
  )
}

/**
 * Returns true when the env file is already pointing at the live endpoint.
 * Used to prevent a double-switch.
 */
export function isAlreadyLive(envContent: string): boolean {
  return !envContent.includes('paper-api.alpaca.markets')
}

/**
 * Build the docker compose recreate command.
 * When HOST_PROJECT_DIR is known we pass --project-directory so Docker resolves
 * host-side relative volume paths correctly.
 */
export function buildComposeCommand(hostProjectDir?: string): string {
  if (hostProjectDir) {
    const f = `${hostProjectDir}/docker-compose.yml`
    return `docker compose -f "${f}" --project-directory "${hostProjectDir}" up -d --force-recreate trader dashboard`
  }
  // Fallback: compose file is mounted at /app/project/docker-compose.yml inside the container
  return 'docker compose -f "/app/project/docker-compose.yml" up -d --force-recreate trader dashboard'
}
