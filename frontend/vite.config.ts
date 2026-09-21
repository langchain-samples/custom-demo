import path from "node:path"
// `vitest/config`, not `vite`: the `test` block below is vitest's, and vite's
// own `defineConfig` does not type it.
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    port: 3000,
    host: "127.0.0.1",
    strictPort: true,
  },
  test: {
    server: {
      deps: {
        // Transformed rather than loaded as an external module, so a test can
        // `vi.mock` what the SDK imports. The MCP Apps bridge lives inside
        // `@langchain/langgraph-sdk` now, and vitest externalises node_modules
        // by default, which puts it out of reach of the mock.
        inline: ["@langchain/langgraph-sdk"],
      },
    },
  },
})
