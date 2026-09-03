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
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  cleanupAssistantArtifacts,
  createAssistant,
  createProject,
  deleteAssistant,
  getDemoTrafficStatus,
  getEvalStatus,
  listAgents,
  listAssistants,
  listHubPrompts,
  listProjects,
  listTools,
  listWorkspaces,
  updateAssistant,
} from "@/lib/api";
import type {
  Assistant,
  CreateAssistantInput,
  EvalTarget,
  LsArtifacts,
  UpdateAssistantInput,
} from "@/lib/api";

/**
 * Every cache key in one place, so no call site hand-writes one. A typo in a key string
 * does not fail loudly: it silently creates a second cache entry that no mutation ever
 * invalidates.
 */
export const qk = {
  assistants: () => ["assistants"] as const,
  workspaces: () => ["workspaces"] as const,
  tools: () => ["tools"] as const,
  hubPrompts: (workspace: string) => ["hub-prompts", workspace] as const,
  agents: (workspace: string) => ["agents", workspace] as const,
  projects: (workspace: string) => ["projects", workspace] as const,
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
 * Prompt Hub names in one workspace.
 *
 * Keyed on the workspace, which is the whole reason this is a query: switching workspace
 * used to mean calling a `loadHubPrompts(id)` by hand and hoping every path that changes
 * the workspace remembered to.
 */
export function useHubPrompts(workspace: string) {
  return useQuery({
    queryKey: qk.hubPrompts(workspace),
    queryFn: () => listHubPrompts(workspace),
    enabled: !!workspace,
  });
}

/** Context Hub agent repos in one workspace. */
export function useAgents(workspace: string) {
  return useQuery({
    queryKey: qk.agents(workspace),
    queryFn: () => listAgents(workspace),
    enabled: !!workspace,
  });
}

/** Tracing project names in one workspace. */
export function useProjects(workspace: string) {
  return useQuery({
    queryKey: qk.projects(workspace),
    queryFn: () => listProjects(workspace),
    enabled: !!workspace,
  });
}

/* ------------------------------ polling --------------------------------- */

/** How often a running job is re-checked. Was a POLL_MS in each polling component. */
const EVAL_POLL_MS = 4000;
const TRAFFIC_POLL_MS = 5000;

/**
 * Eval status, polled only while something is running.
 *
 * `refetchInterval` replaces a `setInterval` plus an `alive` ref in EvalRunner. The ref
 * was there because a bare effect cannot cancel an in-flight fetch on unmount, and
 * StrictMode's double mount made that visible; the library owns that now.
 */
export function useEvalStatus(target: EvalTarget | null, poll: boolean) {
  return useQuery({
    queryKey: qk.evalStatus(target ?? ({} as EvalTarget)),
    queryFn: () => getEvalStatus(target as EvalTarget),
    enabled: !!target?.assistant_id,
    refetchInterval: poll ? EVAL_POLL_MS : false,
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
    refetchInterval: poll ? TRAFFIC_POLL_MS : false,
    staleTime: 0,
  });
}

/* ----------------------------- mutations -------------------------------- */

/**
 * Invalidate the assistant list.
 *
 * Every assistant mutation ends here, which is what replaced the `void loadAll()` calls
 * scattered after each one - and the one in the panel's `open` effect, which refetched
 * three lists every time the panel was opened.
 */
export function useInvalidateAssistants() {
  const qc = useQueryClient();
  return () => qc.invalidateQueries({ queryKey: qk.assistants() });
}

/**
 * Patch ONE assistant into the cached list without refetching.
 *
 * The debounced branding and tool saves used to do exactly this with
 * `setAssistants(list => list.map(...))`. Invalidating instead would refetch the whole
 * list on every 600ms save, so the local patch is deliberately preserved - the change
 * is only where the list now lives.
 */
export function useReplaceAssistantInCache() {
  const qc = useQueryClient();
  return (updated: Assistant) =>
    qc.setQueryData(qk.assistants(), (list: Assistant[] | undefined) =>
      (list ?? []).map((a) => (a.assistant_id === updated.assistant_id ? updated : a)),
    );
}

/**
 * Refetch the assistant list and hand it back.
 *
 * Create and delete both need the fresh list in hand (to reselect), so they refetch
 * rather than invalidate-and-refetch, which would be two requests. Returned as a stable
 * callback because the query object itself is new every render, and depending on that
 * from a useCallback churns it on every keystroke elsewhere in the panel.
 */
export function useRefetchAssistants() {
  const qc = useQueryClient();
  return useCallback(
    () => qc.fetchQuery({ queryKey: qk.assistants(), queryFn: () => listAssistants() }),
    [qc],
  );
}

export function useCreateAssistant() {
  const invalidate = useInvalidateAssistants();
  return useMutation({
    mutationFn: (input: CreateAssistantInput) => createAssistant(input),
    onSuccess: invalidate,
  });
}

export function useUpdateAssistant() {
  const invalidate = useInvalidateAssistants();
  return useMutation({
    mutationFn: ({ id, body }: { id: string; body: UpdateAssistantInput }) =>
      updateAssistant(id, body),
    onSuccess: invalidate,
  });
}

export function useDeleteAssistant() {
  const invalidate = useInvalidateAssistants();
  return useMutation({
    mutationFn: (id: string) => deleteAssistant(id),
    onSuccess: invalidate,
  });
}

/** Cascade-deletes an assistant's LangSmith resources. Never throws; check `failed`. */
export function useCleanupArtifacts() {
  return useMutation({ mutationFn: (refs: LsArtifacts) => cleanupAssistantArtifacts(refs) });
}

export function useCreateProject() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ name, workspace }: { name: string; workspace: string }) =>
      createProject(name, workspace),
    onSuccess: (_data, { workspace }) =>
      qc.invalidateQueries({ queryKey: qk.projects(workspace) }),
  });
}
