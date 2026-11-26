import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vite'

// Dev server proxies the API; point it at the club server with
// API_TARGET=http://192.168.50.14:8190 npm run dev
const target = process.env.API_TARGET ?? 'http://127.0.0.1:8000'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      '/api': { target, ws: true },
      '/outputs': target,
      '/uploads': target,
    },
  },
})
