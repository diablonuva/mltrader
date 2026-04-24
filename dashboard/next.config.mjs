/** @type {import('next').NextConfig} */
const config = {
  output: 'standalone',
  images: { unoptimized: true },
  eslint: { ignoreDuringBuilds: true },
}

export default config
