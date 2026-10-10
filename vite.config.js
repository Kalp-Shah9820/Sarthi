import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The backend the dev server forwards /api calls to. Override with SARTHI_API_TARGET if it runs elsewhere.
const apiTarget = process.env.SARTHI_API_TARGET || 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { '/api': { target: apiTarget, changeOrigin: true } },
  },
  preview: {
    proxy: { '/api': { target: apiTarget, changeOrigin: true } },
  },
})
