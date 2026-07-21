import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const gateway = (env.GATEWAY_ORIGIN || 'http://127.0.0.1:8765').replace(/\/+$/, '')

  return {
    plugins: [react()],
    base: '/spa-v2/',
    build: {
      outDir: 'dist',
      assetsDir: 'assets',
    },
    resolve: {
      alias: {
        '@': fileURLToPath(new URL('./src', import.meta.url)),
      },
    },
    server: {
      port: 5174,
      strictPort: false,
      proxy: {
        // Dev: Vite on :5174, Gateway APIs on GATEWAY_ORIGIN (default :8765)
        '/ais': gateway,
        '/sessions': gateway,
        '/titles': gateway,
        '/workspace': gateway,
        '/ui': gateway,
        '/openapi.json': gateway,
      },
    },
  }
})
