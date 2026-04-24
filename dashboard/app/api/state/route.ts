import { NextResponse } from 'next/server'
import { readFileSync } from 'fs'
import { join } from 'path'

export const dynamic = 'force-dynamic'

const LOGS_DIR = process.env.LOGS_DIR || '/app/logs'

export async function GET() {
  try {
    const raw = readFileSync(join(LOGS_DIR, 'shared_state.json'), 'utf-8')
    return NextResponse.json(JSON.parse(raw))
  } catch {
    return NextResponse.json(
      { error: 'Engine offline — waiting for shared_state.json' },
      { status: 503 }
    )
  }
}
