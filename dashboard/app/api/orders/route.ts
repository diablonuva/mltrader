import { NextResponse } from 'next/server'
import { readFileSync } from 'fs'
import { join } from 'path'

export const dynamic = 'force-dynamic'

const LOGS_DIR = process.env.LOGS_DIR || '/app/logs'

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url)
  const today = new Date().toLocaleDateString('en-CA', { timeZone: 'America/New_York' })
  const all = searchParams.get('period') === 'all'

  try {
    const raw = readFileSync(join(LOGS_DIR, 'orders.log'), 'utf-8')
    let records = raw
      .split('\n')
      .filter(Boolean)
      .map((line) => {
        try { return JSON.parse(line) } catch { return null }
      })
      .filter((r) => r && r.event === 'ORDER')

    if (!all) {
      records = records.filter((r) => {
        const d = new Date(r.ts).toLocaleDateString('en-CA', { timeZone: 'America/New_York' })
        return d === today
      })
    }

    return NextResponse.json(records)
  } catch {
    return NextResponse.json([])
  }
}
