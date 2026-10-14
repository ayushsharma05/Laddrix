/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the Python server runs on :8000 (python -m mocktrade.tools.demo
// --origin http://localhost:5173); the dev server forwards the API, the stream and the
// broker-login callback to it, keeping the browser's Origin header.
const server = "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: server },
      "/kite": { target: server },
      "/ws": { target: server.replace("http", "ws"), ws: true },
    },
  },
  build: { outDir: "dist", sourcemap: false },
  test: {
    environment: "jsdom",
    globals: true,
    include: ["src/**/*.test.{ts,tsx}"], // the desktop app (ui/desktop) runs its own tests
    setupFiles: ["src/test/setup.ts"],
    css: false,
  },
});
