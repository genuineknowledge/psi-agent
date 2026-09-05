import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/auth": "http://127.0.0.1:8080",
      "/ais": "http://127.0.0.1:8080",
      "/sessions": "http://127.0.0.1:8080",
      "/titles": "http://127.0.0.1:8080",
      "/feishu": "http://127.0.0.1:8080",
      "/defaults": "http://127.0.0.1:8080",
    },
  },
});
