// @vitest-environment jsdom
import { act, cleanup, renderHook, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Assistant } from "@/lib/api";
import { qk } from "@/lib/queries";
import { setAssistantId } from "@/lib/config";
import { useAssistantSession } from "./useAssistantSession";

const api = vi.hoisted(() => ({ updateAssistant: vi.fn(), listAssistants: vi.fn(), listWorkspaces: vi.fn() }));
vi.mock("@/lib/api", async (original) => ({ ...(await original<typeof import("@/lib/api")>()), ...api }));
vi.mock("@/lib/branding", async (original) => ({ ...(await original<typeof import("@/lib/branding")>()), applyBrand: vi.fn() }));
vi.mock("@/lib/fonts", async (original) => ({ ...(await original<typeof import("@/lib/fonts")>()), applyTypography: vi.fn(async () => ({ heading: "curated", body: "curated" })) }));

const A = "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa";
const B = "bbbbbbbb-bbbb-4bbb-bbbb-bbbbbbbbbbbb";
const assistants: Assistant[] = [
  { assistant_id: A, graph_id: "dashboard_agent", name: "Acme", context: { agent_repo: "acme-agent", ls_workspace: "ws", model: "old", unknown: "keep" }, metadata: { display_name: "Acme GPT", actions: [{ label: "Sales", question: "Sales?" }], voice: { voice_name: "old", extra: "keep" }, unknown: "keep" } },
  { assistant_id: B, graph_id: "dashboard_agent", name: "Bravo", context: { agent_repo: "bravo-agent", ls_workspace: "other" } },
];

function session(onWorkspaceReset?: () => void) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
  client.setQueryData(qk.assistants(), structuredClone(assistants));
  client.setQueryData(qk.workspaces(), { workspaces: [{ id: "ws", name: "WS" }, { id: "other", name: "Other" }], organization: "Org" });
  api.updateAssistant.mockImplementation(async (id, patch) => ({
    ...client.getQueryData<Assistant[]>(qk.assistants())!.find((a) => a.assistant_id === id), ...patch,
  }));
  const reset = vi.fn();
  const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;
  return { ...renderHook(() => {
    const current = useAssistantSession(reset);
    useEffect(() => {
      if (current.workspaceReset) onWorkspaceReset?.();
    }, [current.workspaceReset]);
    return current;
  }, { wrapper }), client, reset };
}

beforeEach(() => {
  const storage = new Map<string, string>();
  vi.stubGlobal("localStorage", {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => storage.set(key, value),
    removeItem: (key: string) => storage.delete(key),
  });
  localStorage.setItem("dashboardWorkspace", "ws");
  setAssistantId(A);
  vi.clearAllMocks();
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

describe("app-owned assistant session", () => {
  it("restores identity, branding and guards without mounting Settings", async () => {
    const { result, reset } = session();
    await waitFor(() => expect(result.current.runContext.agent_repo).toBe("acme-agent"));
    expect(result.current.selectedId).toBe(A);
    expect(result.current.activeAssistant?.metadata?.display_name).toBe("Acme GPT");
    expect(result.current.guards).toEqual({ hasAssistant: true, hasWorkspace: true, hasPrompt: true });
    expect(reset).not.toHaveBeenCalled();
  });

  it("keeps prompt previews temporary while saved context remains the eval authority", async () => {
    const { result } = session();
    await waitFor(() => expect(result.current.draft.agentRepo).toBe("acme-agent"));
    act(() => result.current.previewPrompt("preview-agent"));
    expect(result.current.runContext.agent_repo).toBe("preview-agent");
    expect(result.current.activeAssistant?.context?.agent_repo).toBe("acme-agent");
    expect(api.updateAssistant).not.toHaveBeenCalled();
  });

  it("persists model, empty tools and branding together without discarding unknown fields", async () => {
    const { result, client } = session();
    await waitFor(() => expect(result.current.draft.agentRepo).toBe("acme-agent"));
    vi.useFakeTimers();
    act(() => {
      result.current.editModel("");
      result.current.editTools([]);
      result.current.editBranding({ name: "Preview", voiceName: "new" });
    });
    expect(result.current.activeAssistant?.metadata?.display_name).toBe("Preview");
    expect(result.current.runContext.enabled_tools).toEqual([]);
    await act(async () => { await vi.advanceTimersByTimeAsync(600); });
    const saved = client.getQueryData<Assistant[]>(qk.assistants())![0];
    expect(saved.context).toEqual({ agent_repo: "acme-agent", ls_workspace: "ws", unknown: "keep", enabled_tools: [] });
    expect(saved.metadata?.voice).toEqual({ voice_name: "new", extra: "keep" });
    expect(saved.metadata?.unknown).toBe("keep");
  });

  it("resets on selection and preserves explicit workspace-switch semantics", async () => {
    const { result, reset } = session();
    await waitFor(() => expect(result.current.draft.agentRepo).toBe("acme-agent"));
    act(() => result.current.selectAssistant(B));
    expect(result.current.selectedId).toBe(B);
    expect(result.current.draft.lsWorkspace).toBe("ws");
    expect(result.current.runContext.agent_repo).toBe("bravo-agent");
    act(() => result.current.selectWorkspace("ws"));
    expect(result.current.selectedId).toBe("");
    expect(result.current.activeAssistant).toBeNull();
    expect(reset).toHaveBeenCalledTimes(2);
  });

  it("reopens workspace recovery after dismissal and a second workspace invalidation", async () => {
    let recoveryOpen = false;
    const openRecovery = vi.fn(() => { recoveryOpen = true; });
    const { result, client } = session(openRecovery);
    await waitFor(() => expect(result.current.draft.agentRepo).toBe("acme-agent"));
    act(() => client.setQueryData(qk.workspaces(), {
      workspaces: [{ id: "other", name: "Other" }], organization: "Org",
    }));
    await waitFor(() => expect(openRecovery).toHaveBeenCalledTimes(1));
    expect(result.current.draft.lsWorkspace).toBe("");
    expect(recoveryOpen).toBe(true);

    recoveryOpen = false;
    act(() => result.current.selectWorkspace("other"));
    expect(result.current.draft.lsWorkspace).toBe("other");
    expect(recoveryOpen).toBe(false);
    act(() => client.setQueryData(qk.workspaces(), {
      workspaces: [{ id: "replacement", name: "Replacement" }], organization: "Org",
    }));
    await waitFor(() => expect(result.current.draft.lsWorkspace).toBe(""));
    expect(openRecovery).toHaveBeenCalledTimes(2);
    expect(recoveryOpen).toBe(true);
  });

  it("an edit queued before selection still targets its original assistant", async () => {
    const { result, client } = session();
    await waitFor(() => expect(result.current.draft.agentRepo).toBe("acme-agent"));
    vi.useFakeTimers();
    act(() => { result.current.editModel("new"); result.current.selectAssistant(B); result.current.editTools([]); });
    await act(async () => { await vi.advanceTimersByTimeAsync(600); });
    const saved = client.getQueryData<Assistant[]>(qk.assistants())!;
    expect(saved[0].context?.model).toBe("new");
    expect(saved[1].context?.enabled_tools).toEqual([]);
    expect(saved[1].context?.model).toBeUndefined();
    expect(result.current.runContext.agent_repo).toBe("bravo-agent");
  });
});
