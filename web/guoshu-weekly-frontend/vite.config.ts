import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// /api goes to the BFF; the browser never sees the Gateway address or key.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8780",
        changeOrigin: false,
      },
    },
  },
});
