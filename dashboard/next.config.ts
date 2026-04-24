import type { NextConfig } from 'next'

const config: NextConfig = {
  output: 'standalone',
  images: { unoptimized: true },
  eslint: { ignoreDuringBuilds: true },
}

export default config
