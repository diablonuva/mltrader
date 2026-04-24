import { NextResponse } from 'next/server'
import { readFileSync } from 'fs'
import { join } from 'path'
import yaml from 'js-yaml'

export const dynamic = 'force-dynamic'

const CONFIG_DIR = process.env.CONFIG_DIR || '/app/config'

export async function GET() {
  try {
    const raw = readFileSync(join(CONFIG_DIR, 'settings.yaml'), 'utf-8')
    const config = yaml.load(raw)
    return NextResponse.json(config)
  } catch {
    return NextResponse.json({ error: 'Config not available' }, { status: 503 })
  }
}
