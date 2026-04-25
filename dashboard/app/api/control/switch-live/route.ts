import { NextResponse } from 'next/server'
import { exec } from 'child_process'
import { readFileSync, writeFileSync } from 'fs'
import { join } from 'path'
import { validatePhrase, updateEnvForLive, isAlreadyLive, buildComposeCommand } from '@/lib/switch-live'

export const dynamic = 'force-dynamic'

const LOGS_DIR        = process.env.LOGS_DIR        || '/app/logs'
const PROJECT_ENV     = '/app/project/.env'
const HOST_PROJECT_DIR = process.env.HOST_PROJECT_DIR || ''

// Simple in-process rate limit: allow one attempt per 90 seconds
let lastAttemptMs = 0

export async function POST(req: Request) {
  // ── Rate limit ──────────────────────────────────────────────────────────────
  const now = Date.now()
  if (now - lastAttemptMs < 90_000) {
    const waitSec = Math.ceil((90_000 - (now - lastAttemptMs)) / 1000)
    return NextResponse.json(
      { error: `Rate limited — wait ${waitSec}s before retrying` },
      { status: 429 }
    )
  }

  // ── Parse body ───────────────────────────────────────────────────────────────
  let body: { phrase?: string }
  try {
    body = await req.json()
  } catch {
    return NextResponse.json({ error: 'Invalid JSON body' }, { status: 400 })
  }

  // ── 1. Validate confirmation phrase ─────────────────────────────────────────
  if (!validatePhrase(body.phrase ?? '')) {
    return NextResponse.json(
      { error: 'Incorrect confirmation phrase — type exactly: SWITCH TO LIVE TRADING' },
      { status: 400 }
    )
  }

  // ── 2. Verify HMM is trained ────────────────────────────────────────────────
  let hmm_trained = false
  try {
    const state = JSON.parse(readFileSync(join(LOGS_DIR, 'shared_state.json'), 'utf-8'))
    hmm_trained = Boolean(state.hmm_trained)
  } catch {
    return NextResponse.json(
      { error: 'Cannot read engine state — is the trader container running?' },
      { status: 503 }
    )
  }

  if (!hmm_trained) {
    return NextResponse.json(
      { error: 'HMM model not trained — complete training before switching to live trading' },
      { status: 403 }
    )
  }

  // ── 3. Read .env ────────────────────────────────────────────────────────────
  let envContent: string
  try {
    envContent = readFileSync(PROJECT_ENV, 'utf-8')
  } catch {
    return NextResponse.json(
      {
        error:
          'Cannot read .env file — ensure HOST_PROJECT_DIR is set and .env is mounted at /app/project/.env',
      },
      { status: 503 }
    )
  }

  if (isAlreadyLive(envContent)) {
    return NextResponse.json(
      { error: 'Already in live trading mode — no change needed' },
      { status: 400 }
    )
  }

  // ── 4. Update .env ──────────────────────────────────────────────────────────
  let updatedEnv: string
  try {
    updatedEnv = updateEnvForLive(envContent)
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : String(e)
    return NextResponse.json({ error: msg }, { status: 500 })
  }

  try {
    writeFileSync(PROJECT_ENV, updatedEnv, 'utf-8')
  } catch {
    return NextResponse.json(
      { error: 'Cannot write .env — check that the file is mounted read-write' },
      { status: 500 }
    )
  }

  // ── 5. docker compose up --force-recreate ───────────────────────────────────
  lastAttemptMs = now
  const cmd = buildComposeCommand(HOST_PROJECT_DIR || undefined)

  return new Promise<NextResponse>((resolve) => {
    exec(cmd, { timeout: 120_000 }, (err, stdout, stderr) => {
      if (err) {
        // Roll back .env on failure so the system isn't left in a broken state
        try { writeFileSync(PROJECT_ENV, envContent, 'utf-8') } catch { /* ignore */ }
        lastAttemptMs = 0 // allow immediate retry after rollback

        resolve(
          NextResponse.json(
            {
              error: `docker compose failed — see details`,
              detail: stderr || err.message,
              cmd,
            },
            { status: 500 }
          )
        )
      } else {
        resolve(
          NextResponse.json({
            success: true,
            message: 'Switched to live trading — containers are restarting',
            cmd,
            stdout: stdout.trim(),
          })
        )
      }
    })
  })
}
