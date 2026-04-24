import { NextResponse } from 'next/server'
import { readFileSync } from 'fs'
import { join } from 'path'

export const dynamic = 'force-dynamic'

const LOGS_DIR = process.env.LOGS_DIR || '/app/logs'

export async function GET() {
  try {
    const raw = readFileSync(join(LOGS_DIR, 'trades.log'), 'utf-8')
    const today = new Date().toLocaleDateString('en-CA', {
      timeZone: 'America/New_York',
    })
    const trades = raw
      .split('\n')
      .filter(Boolean)
      .map((line) => {
        try {
          return JSON.parse(line)
        } catch {
          return null
        }
      })
      .filter((t) => t && t.event === 'TRADE_COMPLETED')
      .filter((t) => {
        const d = new Date(t.ts).toLocaleDateString('en-CA', {
          timeZone: 'America/New_York',
        })
        return d === today
      })
    return NextResponse.json(trades)
  } catch {
    return NextResponse.json([])
  }
}
