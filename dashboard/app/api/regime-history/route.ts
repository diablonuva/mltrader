import { NextResponse } from 'next/server'
import { readFileSync } from 'fs'
import { join } from 'path'

export const dynamic = 'force-dynamic'

const LOGS_DIR = process.env.LOGS_DIR || '/app/logs'

export async function GET() {
  try {
    const raw = readFileSync(join(LOGS_DIR, 'regime.log'), 'utf-8')
    const records = raw
      .split('\n')
      .filter(Boolean)
      .map((line) => {
        try { return JSON.parse(line) } catch { return null }
      })
      .filter(Boolean)
    return NextResponse.json(records)
  } catch {
    return NextResponse.json([])
  }
}
