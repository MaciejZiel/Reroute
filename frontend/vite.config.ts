import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Dev only: set API_PROXY_TARGET (e.g. http://localhost:8000) and leave VITE_API_URL empty
// to call /api on the dev server's own origin without CORS set-up.
const target = process.env.API_PROXY_TARGET;

export default defineConfig({
  plugins: [react()],
  server: target ? { proxy: { "/api": { target, changeOrigin: true } } } : undefined,
  test: { environment: "node", include: ["src/**/*.test.ts"] },
});
