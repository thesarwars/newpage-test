import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./", import.meta.url)) },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    // `e2e/` is Playwright's, not vitest's. Both use `describe`/`test`, so
    // vitest happily collects a Playwright spec and then fails inside it with
    // "did not expect test.describe() to be called here" — which reads as a
    // broken test rather than as the wrong runner.
    exclude: ["node_modules/**", ".next/**", "e2e/**"],
  },
});
