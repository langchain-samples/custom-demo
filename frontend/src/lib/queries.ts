/**
 * React Query layer over the fetchers in `api.ts`.
 *
 * The point is a single owner for cache, loading state and invalidation. Before this,
 * every read was `fetch` + `useState` + a hand-written effect, so nothing distinguished
 * "no assistants" from "not asked yet" and each call site invented its own answer (or
 * forgot to, and told people with a dozen assistants that they had none).
 *
 * These hooks WRAP `api.ts` rather than replacing it. Those functions already normalise
 * errors and shapes, and several deliberately never throw - `listWorkspaces` returns an
 * empty list on failure, `getEvalStatus` returns `{ error }` - which callers rely on.
 * Rewriting them as raw fetches inside queries would quietly change that contract.
 *
 * What is NOT here, on purpose: `runStream` (an SSE generator), thread lifecycle
 * (`ensureThread` / `getThreadState`), `runSetup` (long-running, owns its own progress
 * UI), voice tokens, and `getTraceUrl`. Those are actions and streams, not cached server
 * state.
 */
import { useCallback } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  getDemoTrafficStatus,
  getEvalStatus,
  listAgents,
  listAssistants,
  listTools,
  listWorkspaces,
} from "@/lib/api";
import type { EvalTarget } from "@/lib/api";

/**
 * Every cache key in one place, so no call site hand-writes one. A typo in a key string
 * does not fail loudly: it silently creates a second cache entry that no mutation ever
 * invalidates.
 */
export const qk = {
  assistants: () => ["assistants"] as const,
  workspaces: () => ["workspaces"] as const,
  tools: () => ["tools"] as const,
  agents: (workspace: string) => ["agents", workspace] as const,
  evalStatus: (target: EvalTarget) => ["eval-status", target] as const,
  demoTraffic: (project: string, workspace?: string) =>
    ["demo-traffic", project, workspace ?? ""] as const,
};

/* ------------------------------- reads ---------------------------------- */

/** Every assistant on the deployment. `isPending` is the app's "still asking". */
export function useAssistants() {
  return useQuery({ queryKey: qk.assistants(), queryFn: () => listAssistants() });
}

/** Workspaces the routing key can see, plus the org they belong to. */
export function useWorkspaces() {
  return useQuery({ queryKey: qk.workspaces(), queryFn: listWorkspaces });
}

/** The selectable tool catalogue. Effectively static for the life of a deployment. */
export function useTools() {
  return useQuery({ queryKey: qk.tools(), queryFn: listTools, staleTime: 5 * 60_000 });
}

/**
 * Context Hub agent repos in one workspace.
 *
 * Keyed on the workspace, which is the whole reason this is a query: changing the key IS
 * the refetch. Do NOT call a loader by hand instead, because then every path that changes
 * the workspace has to remember to.
 */
export function useAgents(workspace: string) {
  return useQuery({
    queryKey: qk.agents(workspace),
    queryFn: () => listAgents(workspace),
    enabled: !!workspace,
  });
}


/* ------------------------------ polling --------------------------------- */

/** How often a running job is re-checked. Centralised here, not a POLL_MS per component. */
const EVAL_POLL_MS = 4000;
const TRAFFIC_POLL_MS = 5000;

/**
 * Eval status, polled only while something is running.
 *
 * `refetchInterval` owns the poll. Do NOT hand-roll it as a `setInterval` plus an `alive`
 * ref in EvalRunner: a bare effect cannot cancel an in-flight fetch on unmount, and React
 * 19 StrictMode's double mount is what exposes it.
 */
export function useEvalStatus(target: EvalTarget | null, poll: boolean) {
  return useQuery({
    queryKey: qk.evalStatus(target ?? ({} as EvalTarget)),
    queryFn: () => getEvalStatus(target as EvalTarget),
    enabled: !!target?.assistant_id,
    // Two reasons to keep polling: the caller says a run was just started (LangSmith has
    // nothing to report for the first few seconds), or LangSmith itself says one is
    // running. Reading the query's own data here is what avoids the caller having to
    // derive "still running" from data that only arrives because it is polling.
    refetchInterval: (query) => (poll || query.state.data?.running ? EVAL_POLL_MS : false),
    // Status is the one thing that must not be served stale: it is the answer to "is it
    // finished yet".
    staleTime: 0,
  });
}

/** Demo-traffic backfill status, polled only while a backfill is running. */
export function useDemoTrafficStatus(project: string, workspace: string | undefined, poll: boolean) {
  return useQuery({
    queryKey: qk.demoTraffic(project, workspace),
    queryFn: () => getDemoTrafficStatus(project, workspace),
    enabled: !!project,
    // Poll while the caller has just started a backfill, or while the server says one is
    // running. Same reasoning as useEvalStatus.
    refetchInterval: (query) => (poll || query.state.data?.running ? TRAFFIC_POLL_MS : false),
    staleTime: 0,
  });
}

/* ----------------------------- mutations -------------------------------- */

/**
 * Invalidate the assistant list.
 *
 * Every assistant mutation ends here. Do NOT scatter a `void loadAll()` after each one,
 * and do NOT put one in the panel's `open` effect: that refetches three lists every time
 * the panel is opened.
 */
export function useInvalidateAssistants() {
  const qc = useQueryClient();
  return () => qc.invalidateQueries({ queryKey: qk.assistants() });
}

/**
 * Refetch the assistant list and hand it back.
 *
 * Create and delete both need the fresh list in hand (to reselect), so they refetch
 * rather than invalidate-and-refetch, which would be two requests. Returned as a stable
 * callback because the query object itself is new every render, and depending on that
 * from a useCallback churns it on every keystroke elsewhere in the panel.
 *
 * `staleTime: 0` is load-bearing and is NOT the client default (30s). fetchQuery serves
 * the cache outright while data is fresh, so without it deleting an assistant gets back
 * the list that still contains it: the row stays on screen, a second delete 404s, and a
 * refresh - the one path that bypasses the cache - makes it vanish. Every caller here
 * has just changed the thing it is asking about, so a cached answer is never the right
 * one, however few seconds old it is.
 */
export function useRefetchAssistants() {
  const qc = useQueryClient();
  return useCallback(
    () =>
      qc.fetchQuery({
        queryKey: qk.assistants(),
        queryFn: () => listAssistants(),
        staleTime: 0,
      }),
    [qc],
  );
}





