import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// Dev server proxies API calls to the running psi-agent web channel.
// Override the target with VITE_API_TARGET if the channel runs elsewhere.
const API_TARGET = process.env.VITE_API_TARGET || 'http://127.0.0.1:8848'

export default defineConfig({
  plugins: [vue()],
  server: {
    host: true,
    port: 5173,
    proxy: {
      '/api': { target: API_TARGET, changeOrigin: true },
    },
  },
})
