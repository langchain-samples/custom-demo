import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { PersistQueryClientProvider } from '@tanstack/react-query-persist-client'
import { createSyncStoragePersister } from '@tanstack/query-sync-storage-persister'
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

/**
 * Keep the cache across page loads.
 *
 * react-query's cache is in memory, so it survives components unmounting and panels
 * closing but NOT a reload - a reload is a new JS context, and soft versus hard makes no
 * difference to that. Without this, every refresh started cold and showed "Loading your
 * assistants" before it could show anything real.
 *
 * Only the stable lists are persisted. Job status is deliberately excluded: restoring a
 * day-old `running: true` from disk would show a spinner for an experiment that finished
 * long ago, which is worse than asking.
 */
const PERSISTED = new Set(["assistants", "workspaces", "tools", "hub-prompts", "agents", "projects"])

/** Bump to discard every stored cache, e.g. after changing a query's shape. */
const CACHE_VERSION = "v1"

/**
 * localStorage is not always there (private windows, blocked site data), and a throwing
 * persister must not take the app down with it - a cold cache is a fine outcome.
 */
function makePersister() {
  try {
    window.localStorage.getItem("probe")
    return createSyncStoragePersister({ storage: window.localStorage, key: "da-query-cache" })
  } catch {
    return undefined
  }
}

const persister = makePersister()

const tree = (
  <StrictMode>
    {persister ? (
      <PersistQueryClientProvider
        client={queryClient}
        persistOptions={{
          persister,
          // A day: long enough that yesterday's demo opens instantly, short enough that
          // a deleted assistant does not linger indefinitely before the revalidate.
          maxAge: 24 * 60 * 60 * 1000,
          buster: CACHE_VERSION,
          dehydrateOptions: {
            shouldDehydrateQuery: (query) => PERSISTED.has(String(query.queryKey[0])),
          },
        }}
      >
        <App />
      </PersistQueryClientProvider>
    ) : (
      // No storage to persist to. Still needs a provider, or every useQuery throws.
      <QueryClientProvider client={queryClient}>
        <App />
      </QueryClientProvider>
    )}
  </StrictMode>
)

createRoot(document.getElementById('root')!).render(tree)
