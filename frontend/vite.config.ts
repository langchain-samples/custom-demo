import path from "node:path"
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
      // Vendored until it ships from npm. Source, not a build: it is ours,
      // and a source alias keeps it in the SPA's typecheck and lint.
      "@langchain/react": path.resolve(__dirname, "./packages/langchain-react/src"),
    },
  },
  server: {
    port: 3000,
    host: "127.0.0.1",
    strictPort: true,
  },
})
