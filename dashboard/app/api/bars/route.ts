import { NextResponse } from 'next/server'

export const dynamic = 'force-dynamic'

const DATA_BASE = 'https://data.alpaca.markets/v2/stocks'

export async function GET(request: Request) {
  const { searchParams } = new URL(request.url)
  const symbol    = searchParams.get('symbol')    || 'SPY'
  const timeframe = searchParams.get('timeframe') || '5Min'
  const limit     = searchParams.get('limit')     || '100'

  const apiKey    = process.env.ALPACA_API_KEY    || ''
  const apiSecret = process.env.ALPACA_SECRET_KEY || ''

  if (!apiKey || !apiSecret) {
    return NextResponse.json({ error: 'Alpaca credentials not configured' }, { status: 503 })
  }

  try {
    const url = `${DATA_BASE}/${symbol}/bars?timeframe=${timeframe}&limit=${limit}&feed=iex&adjustment=raw&sort=asc`
    const res = await fetch(url, {
      headers: {
        'APCA-API-KEY-ID':     apiKey,
        'APCA-API-SECRET-KEY': apiSecret,
      },
      // short cache — price data should stay fresh
      next: { revalidate: 30 },
    })

    if (!res.ok) {
      const err = await res.text()
      return NextResponse.json({ error: err }, { status: res.status })
    }

    const data = await res.json()
    return NextResponse.json(data.bars ?? [])
  } catch (e) {
    return NextResponse.json({ error: String(e) }, { status: 500 })
  }
}
