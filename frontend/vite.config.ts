import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Every backend route the browser calls, proxied to uvicorn in dev so the app
// only ever talks to one origin (the backend configures no CORS, and in
// production FastAPI serves this build itself from the same origin).
// Deliberately not a blanket '/': that would swallow Vite's own index.html and
// HMR client. '/webhooks' is absent because only RazorpayX calls it.
const BACKEND = 'http://127.0.0.1:8000'
const API_ROUTES = [
  '/health',
  '/integration',
  '/pipeline',
  '/funnel',
  '/analytics',
  '/audit',
  '/exceptions',
  '/actions',
  '/assistant',
]

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: { '@': new URL('./src', import.meta.url).pathname },
  },
  server: {
    proxy: Object.fromEntries(API_ROUTES.map((route) => [route, BACKEND])),
  },
  build: {
    outDir: '../app/static/dist',
    emptyOutDir: true,
  },
})
