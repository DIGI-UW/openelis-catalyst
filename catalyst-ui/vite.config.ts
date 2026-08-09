import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  /*
   * Without a proxy the dev server answers /v1/catalyst/* with index.html --
   * the SPA fallback -- so the app parses HTML as JSON and every list comes
   * back empty, with a 200 and no failed request to explain it. It only ever
   * looked fine under Playwright, which intercepts the API before the server
   * is reached. Point CATALYST_GATEWAY_URL at another stack to develop
   * against it.
   */
  server: {
    host: "127.0.0.1",
    port: 4173,
    proxy: {
      "/v1/catalyst": {
        target: process.env.CATALYST_GATEWAY_URL ?? "http://127.0.0.1:18000",
        changeOrigin: true,
      },
    },
  },
  preview: {
    host: "127.0.0.1",
    port: 4173,
    proxy: {
      "/v1/catalyst": {
        target: process.env.CATALYST_GATEWAY_URL ?? "http://127.0.0.1:18000",
        changeOrigin: true,
      },
    },
  },
  test: {
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
    environment: "jsdom",
    globals: true,
    setupFiles: "./src/test/setup.ts",
    css: true,
    coverage: {
      reporter: ["text", "html"],
    },
  },
});
