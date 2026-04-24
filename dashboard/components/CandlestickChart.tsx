'use client'

import { useEffect, useRef } from 'react'
import {
  createChart,
  CrosshairMode,
  type IChartApi,
  type ISeriesApi,
  type CandlestickData,
  type SeriesMarker,
  type Time,
} from 'lightweight-charts'
import { currency } from '@/lib/format'

interface AlpacaBar {
  t: string
  o: number
  h: number
  l: number
  c: number
  v: number
  vw?: number
}

interface Trade {
  entry_time: string
  exit_time: string
  direction: string
  entry_price: number
  exit_price: number
  pnl_dollar: number
}

interface Props {
  bars: AlpacaBar[]
  trades?: Trade[]
  symbol?: string
  timeframe?: string
  loading?: boolean
}

const REGIME_COLORS: Record<string, string> = {
  TRENDING_UP:   '#00ff87',
  TRENDING_DOWN: '#ff4d6d',
  BREAKOUT:      '#60a5fa',
  SQUEEZE:       '#fbbf24',
  CHOPPY:        '#f97316',
  UNKNOWN:       '#64748b',
}

function toUnixSec(iso: string): number {
  return Math.floor(new Date(iso).getTime() / 1000)
}

export default function CandlestickChart({
  bars,
  trades = [],
  symbol = 'SPY',
  timeframe = '5Min',
  loading = false,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null)
  const chartRef = useRef<IChartApi | null>(null)
  const candleRef = useRef<ISeriesApi<'Candlestick'> | null>(null)
  const volumeRef = useRef<ISeriesApi<'Histogram'> | null>(null)

  useEffect(() => {
    if (!containerRef.current) return

    const chart = createChart(containerRef.current, {
      layout: {
        background: { color: '#111827' },
        textColor: '#64748b',
        fontFamily: "'JetBrains Mono', monospace",
        fontSize: 11,
      },
      grid: {
        vertLines: { color: '#1e293b' },
        horzLines: { color: '#1e293b' },
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: '#334155', labelBackgroundColor: '#1e293b' },
        horzLine: { color: '#334155', labelBackgroundColor: '#1e293b' },
      },
      rightPriceScale: {
        borderColor: '#1e293b',
        scaleMargins: { top: 0.1, bottom: 0.3 },
      },
      timeScale: {
        borderColor: '#1e293b',
        timeVisible: true,
        secondsVisible: false,
        fixLeftEdge: true,
        fixRightEdge: true,
      },
      autoSize: true,
    })
    chartRef.current = chart

    // Candlestick series
    const candles = chart.addCandlestickSeries({
      upColor:        '#00ff87',
      downColor:      '#ff4d6d',
      borderUpColor:  '#00ff87',
      borderDownColor:'#ff4d6d',
      wickUpColor:    '#00ff87',
      wickDownColor:  '#ff4d6d',
    })
    candleRef.current = candles

    // Volume histogram (bottom pane)
    const volume = chart.addHistogramSeries({
      color: '#1e293b',
      priceFormat: { type: 'volume' },
      priceScaleId: 'volume',
    })
    chart.priceScale('volume').applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 },
    })
    volumeRef.current = volume

    return () => {
      chart.remove()
      chartRef.current = null
    }
  }, [])

  // Update data when bars change
  useEffect(() => {
    if (!candleRef.current || !volumeRef.current || !bars?.length) return

    const candleData: CandlestickData[] = bars.map((b) => ({
      time: toUnixSec(b.t) as Time,
      open:  b.o,
      high:  b.h,
      low:   b.l,
      close: b.c,
    }))
    candleRef.current.setData(candleData)

    const volData = bars.map((b) => ({
      time:  toUnixSec(b.t) as Time,
      value: b.v,
      color: b.c >= b.o ? 'rgba(0,255,135,0.25)' : 'rgba(255,77,109,0.25)',
    }))
    volumeRef.current.setData(volData)

    // Trade markers
    if (trades.length > 0) {
      const barTimes = new Set(candleData.map((c) => c.time as number))
      const snap = (iso: string) => {
        const t = toUnixSec(iso)
        // find closest bar time
        let best = candleData[0].time as number
        let bestDiff = Math.abs(t - best)
        for (const d of candleData) {
          const diff = Math.abs(t - (d.time as number))
          if (diff < bestDiff) { bestDiff = diff; best = d.time as number }
        }
        return best as Time
      }

      const markers: SeriesMarker<Time>[] = []
      for (const tr of trades) {
        markers.push({
          time:     snap(tr.entry_time),
          position: tr.direction === 'LONG' ? 'belowBar' : 'aboveBar',
          color:    '#00ff87',
          shape:    tr.direction === 'LONG' ? 'arrowUp' : 'arrowDown',
          text:     `▶ ${tr.direction}`,
          size:     1,
        })
        markers.push({
          time:     snap(tr.exit_time),
          position: tr.direction === 'LONG' ? 'aboveBar' : 'belowBar',
          color:    tr.pnl_dollar >= 0 ? '#00ff87' : '#ff4d6d',
          shape:    'circle',
          text:     `${tr.pnl_dollar >= 0 ? '+' : ''}${currency(tr.pnl_dollar)}`,
          size:     1,
        })
      }
      markers.sort((a, b) => (a.time as number) - (b.time as number))
      candleRef.current.setMarkers(markers)
    }

    chartRef.current?.timeScale().fitContent()
  }, [bars, trades])

  return (
    <div className="card p-4 h-full">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2">
          <h2 className="text-[10px] text-[#475569] uppercase tracking-widest font-medium">
            {symbol} · {timeframe} Bars
          </h2>
          {loading && (
            <span className="text-[10px] text-[#f59e0b] animate-pulse">Loading…</span>
          )}
        </div>
        <div className="flex items-center gap-3 text-[10px] font-mono text-[#475569]">
          <span className="flex items-center gap-1">
            <span className="w-2 h-2 rounded-sm bg-[#00ff87] inline-block" />Bull
          </span>
          <span className="flex items-center gap-1">
            <span className="w-2 h-2 rounded-sm bg-[#ff4d6d] inline-block" />Bear
          </span>
          <span className="flex items-center gap-1">
            <span className="text-[#00ff87]">▲</span>Entry
          </span>
          <span className="flex items-center gap-1">
            <span className="text-[#ff4d6d]">●</span>Exit
          </span>
        </div>
      </div>

      {!bars?.length && !loading ? (
        <div className="h-[300px] flex items-center justify-center text-[#475569] text-sm">
          No bar data — market may be closed or credentials missing
        </div>
      ) : (
        <div ref={containerRef} className="w-full" style={{ height: 320 }} />
      )}
    </div>
  )
}
