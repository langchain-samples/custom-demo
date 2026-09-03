import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import './index.css'
import App from './App.tsx'

/**
 * One cache for every server read (see lib/queries.ts).
 *
 * The defaults are chosen for a tool that gets PRESENTED, which is why the first one is
 * off: react-query normally refetches when a tab regains focus, and alt-tabbing to Slack
 * mid-demo would fire a wave of requests behind the dashboard. Nothing here changes
 * often enough to need it, and every mutation invalidates its own keys anyway.
 */
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      // Reopening the settings panel inside half a minute serves cache instead of
      // visibly reloading the same three lists.
      staleTime: 30_000,
      retry: 1,
    },
  },
})

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <App />
    </QueryClientProvider>
  </StrictMode>,
)
