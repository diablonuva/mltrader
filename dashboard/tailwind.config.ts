import type { Config } from 'tailwindcss'

const config: Config = {
  content: [
    './app/**/*.{js,ts,jsx,tsx,mdx}',
    './components/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        background: '#0a0e1a',
        surface: '#111827',
        'surface-2': '#1a2236',
        border: '#1e293b',
        foreground: '#e2e8f0',
        muted: '#64748b',
        'accent-green': '#00ff87',
        'accent-red': '#ff4d6d',
        'accent-amber': '#f59e0b',
        'accent-blue': '#60a5fa',
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['JetBrains Mono', 'Courier New', 'monospace'],
      },
    },
  },
  plugins: [],
}

export default config
