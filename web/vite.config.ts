import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

const RUNNER_URL = process.env.PREV_RUNNER_URL ?? "http://127.0.0.1:8765";

export default defineConfig({
  plugins: [react()],
  server: {
    // The runner rejects unknown Host headers, so rewrite Host to the runner's own.
    proxy: { "/api": { target: RUNNER_URL, changeOrigin: true } },
  },
  build: { outDir: "dist", emptyOutDir: true },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./src/test/setup.ts"],
  },
});
