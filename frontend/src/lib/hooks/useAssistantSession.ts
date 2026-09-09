import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { updateAssistant, type Assistant, type McpServerConfig, type SetupInput, type Workspace } from "@/lib/api";
import { getAssistantId, isAssistantId, setAssistantId } from "@/lib/config";
import { qk, useAssistants, useRefetchAssistants, useWorkspaces } from "@/lib/queries";
import { traceProject } from "@/lib/trace";
import { AssistantEdits } from "@/lib/assistantEdits";
import { prepareAndPublishAssistant, retireAssistant, type CleanupFailure } from "@/lib/assistantLifecycle";
import {
  blankDraft, brandingMetadata, draftFromAssistant, previewAssistant, sessionRunContext,
  readSessionPreference, writeSessionPreference, WORKSPACE_LS_KEY, LAST_OWNER_LS_KEY,
  type AssistantDraft, type BrandingDraft,
} from "@/lib/assistantSession";
import { useAssistantAppearance } from "./useAssistantAppearance";

const EMPTY_ASSISTANTS: Assistant[] = [];
const EMPTY_WORKSPACES: Workspace[] = [];

/** App-level authority for selection, presenter previews and acknowledged assistant edits. */
export function useAssistantSession(onResetConversation: () => void) {
  const client = useQueryClient();
  const assistantsQuery = useAssistants();
  const assistants = assistantsQuery.data ?? EMPTY_ASSISTANTS;
  const workspacesQuery = useWorkspaces();
  const workspaces = workspacesQuery.data?.workspaces ?? EMPTY_WORKSPACES;
  const refetchAssistants = useRefetchAssistants();
  const [selectedId, setSelectedId] = useState(() => {
    const saved = getAssistantId();
    return isAssistantId(saved) ? saved : "";
  });
  const [draft, setDraft] = useState(() => blankDraft(readSessionPreference(WORKSPACE_LS_KEY)));
  const [workspaceReset, setWorkspaceReset] = useState(false);
  const draftRef = useRef(draft);
  draftRef.current = draft;
  const selectedIdRef = useRef(selectedId);
  selectedIdRef.current = selectedId;
  const resetRef = useRef(onResetConversation);
  resetRef.current = onResetConversation;
  const restoredRef = useRef(false);

  const edits = useMemo(() => new AssistantEdits({
    get: (id) => client.getQueryData<Assistant[]>(qk.assistants())?.find((a) => a.assistant_id === id),
    save: updateAssistant,
    saved: (updated) => client.setQueryData<Assistant[]>(qk.assistants(), (list) =>
      (list ?? []).map((a) => a.assistant_id === updated.assistant_id ? updated : a)),
  }), [client]);
  useEffect(() => () => edits.dispose(), [edits]);

  const setPreview = useCallback((next: AssistantDraft) => {
    draftRef.current = next;
    setDraft(next);
  }, []);

  const applySelection = useCallback((id: string, list: Assistant[], reset: boolean) => {
    selectedIdRef.current = id;
    setSelectedId(id);
    setAssistantId(id);
    const assistant = list.find((a) => a.assistant_id === id);
    const workspace = draftRef.current.lsWorkspace;
    setPreview(assistant ? draftFromAssistant(assistant, workspace) : blankDraft(workspace));
    if (reset) resetRef.current();
  }, [setPreview]);

  useEffect(() => {
    if (restoredRef.current || !assistants.length) return;
    const assistant = assistants.find((a) => a.assistant_id === selectedIdRef.current);
    if (!assistant) return;
    restoredRef.current = true;
    setPreview(draftFromAssistant(assistant, draftRef.current.lsWorkspace));
  }, [assistants, setPreview]);

  useEffect(() => {
    if (!draft.lsWorkspace || !workspaces.length || workspaces.some((w) => w.id === draft.lsWorkspace)) return;
    setPreview({ ...draftRef.current, lsWorkspace: "" });
    writeSessionPreference(WORKSPACE_LS_KEY, "");
    setWorkspaceReset(true);
  }, [draft.lsWorkspace, workspaces, setPreview]);

  const selectWorkspace = useCallback((workspace: string) => {
    if (workspaces.some((w) => w.id === workspace)) setWorkspaceReset(false);
    setPreview({ ...draftRef.current, lsWorkspace: workspace });
    writeSessionPreference(WORKSPACE_LS_KEY, workspace);
    const list = client.getQueryData<Assistant[]>(qk.assistants()) ?? EMPTY_ASSISTANTS;
    const current = list.find((a) => a.assistant_id === selectedIdRef.current);
    const owner = current?.context?.ls_workspace;
    if (current && typeof owner === "string" && owner && owner !== workspace) {
      applySelection("", list, true);
    }
  }, [client, applySelection, setPreview, workspaces]);

  const selectAssistant = useCallback((id: string) => {
    applySelection(id, client.getQueryData<Assistant[]>(qk.assistants()) ?? EMPTY_ASSISTANTS, true);
  }, [applySelection, client]);

  const editBranding = useCallback((patch: Partial<BrandingDraft>) => {
    const next = { ...draftRef.current, ...patch };
    setPreview(next);
    const id = selectedIdRef.current;
    if (isAssistantId(id)) {
      edits.schedule(id, "branding", (current) => ({ metadata: brandingMetadata(current.metadata, next) }), 600);
    }
  }, [edits, setPreview]);

  const previewPrompt = useCallback((agentRepo: string) => {
    setPreview({ ...draftRef.current, agentRepo });
  }, [setPreview]);

  const editTools = useCallback((enabledTools: string[]) => {
    setPreview({ ...draftRef.current, enabledTools });
    const id = selectedIdRef.current;
    if (isAssistantId(id)) {
      edits.schedule(id, "tools", (current) => ({ context: { ...current.context, enabled_tools: enabledTools } }), 600);
    }
  }, [edits, setPreview]);

  const editModel = useCallback((model: string) => {
    setPreview({ ...draftRef.current, model });
    const id = selectedIdRef.current;
    if (isAssistantId(id)) {
      edits.schedule(id, "model", (current) => {
        const context = { ...current.context };
        if (model) context.model = model;
        else delete context.model;
        return { context };
      }, 600);
    }
  }, [edits, setPreview]);

  const editMcpServers = useCallback((mcpServers: McpServerConfig[]) => {
    setPreview({ ...draftRef.current, mcpServers });
    const id = selectedIdRef.current;
    if (isAssistantId(id)) {
      edits.schedule(id, "mcp", (current) => ({ context: { ...current.context, mcp_servers: mcpServers } }), 800);
    }
  }, [edits, setPreview]);

  const create = useCallback(async (input: SetupInput) => {
    if (input.owner) writeSessionPreference(LAST_OWNER_LS_KEY, input.owner);
    const prepared = await prepareAndPublishAssistant(input);
    const list = await refetchAssistants();
    applySelection(prepared.assistant.assistant_id, list, true);
    return prepared;
  }, [refetchAssistants, applySelection]);

  const remove = useCallback(async (report: (failed: CleanupFailure[]) => void) => {
    const id = selectedIdRef.current;
    if (!isAssistantId(id)) return;
    const saved = client.getQueryData<Assistant[]>(qk.assistants())?.find((a) => a.assistant_id === id) ?? null;
    await retireAssistant(id, saved, report);
    applySelection("", await refetchAssistants(), false);
  }, [client, refetchAssistants, applySelection]);

  const getRunContext = useCallback(() => {
    const id = selectedIdRef.current;
    const saved = client.getQueryData<Assistant[]>(qk.assistants())?.find((a) => a.assistant_id === id) ?? null;
    return sessionRunContext(draftRef.current, traceProject(saved, id));
  }, [client]);

  const selectedAssistant = assistants.find((a) => a.assistant_id === selectedId) ?? null;
  const activeAssistant = useMemo(() => previewAssistant(selectedAssistant, draft), [selectedAssistant, draft]);
  const guards = { hasAssistant: isAssistantId(selectedId), hasWorkspace: !!draft.lsWorkspace, hasPrompt: !!draft.agentRepo };
  const fontStatus = useAssistantAppearance(draft, !!selectedAssistant, guards.hasAssistant);
  const visibleAssistants = draft.lsWorkspace
    ? assistants.filter((a) => !a.context?.ls_workspace || a.context.ls_workspace === draft.lsWorkspace || a.assistant_id === selectedId)
    : assistants;

  return {
    selectedId, selectedAssistant, activeAssistant, draft, guards,
    runContext: sessionRunContext(draft, traceProject(selectedAssistant, selectedId)),
    getRunContext, assistantsPending: assistantsQuery.isPending,
    visibleAssistants, workspaces, organization: workspacesQuery.data?.organization ?? "", workspaceReset,
    fontStatus, selectWorkspace, selectAssistant, editBranding, previewPrompt, editTools, editModel, editMcpServers,
    create, remove,
  };
}

export type AssistantSession = ReturnType<typeof useAssistantSession>;
