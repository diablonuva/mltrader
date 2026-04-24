import { NextResponse } from 'next/server'

export const dynamic = 'force-dynamic'

export async function GET() {
  const url = process.env.ALPACA_BASE_URL ?? ''
  return NextResponse.json({
    mode: url.includes('paper') ? 'PAPER' : 'LIVE',
    assets: (process.env.ASSETS ?? 'SPY').split(',').map((s) => s.trim()),
  })
}
