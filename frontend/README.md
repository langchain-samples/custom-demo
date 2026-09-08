# Dashboard Agent SPA

The user interface for the demo platform in the repository root: a chat pane, a live
dashboard the agent writes widgets into, an HTML-artifact tab, a sandbox file browser,
and the settings sheet where an assistant is created and configured.

See the [root README](../README.md) to run the whole thing. This directory alone:

```bash
npm ci
npm run dev            # Vite dev server on :3000, expects the agent on :2024
npm run lint           # oxlint + the em-dash check
npm test               # vitest
npm run build          # tsc -b && vite build, the exact build Vercel runs
```

Point it at a different backend with `VITE_LG_URL` and `VITE_LG_API_KEY`, or at runtime
through the gear panel.

## Layout

| path | what lives there |
|---|---|
| `src/lib/` | API client, streaming, branding and chart derivation, voice session |
| `src/components/` | `ChatPanel`, `DashboardCanvas`, `HtmlArtifact`, `SettingsPanel` |
| `src/components/chat/` | message rows, tool chips, typed result cards |
| `src/components/settings/` | the sections of the settings sheet |
| `src/components/agents/`, `motion/`, `ui/` | shared presentational primitives |

Colors come from CSS custom properties in `src/index.css`, derived per assistant from a
brand seed. Read them with `resolveColor()` from `src/lib/branding.ts` rather than
`getComputedStyle`, which hands back color spaces that Chart.js and html2canvas cannot
parse.
