import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

// Scope to this frontend only — the repo root hosts unrelated test suites.
export default defineConfig({
  root: fileURLToPath(new URL(".", import.meta.url)),
  test: {
    include: ["src/**/*.test.ts"],
  },
});
