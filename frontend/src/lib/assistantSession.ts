import type { Assistant, AssistantMetadata, McpServerConfig, QuickAction, RunContext } from "./api";
import { DEFAULT_TINT } from "./branding";
import { DEFAULT_CURATED } from "./fonts";
import { coerceTheme, type Theme } from "./theme";

/** Editable presenter state, distinct from the assistant's acknowledged server record. */
export interface AssistantDraft {
  lsWorkspace: string;
  name: string;
  accent: string;
  accent2: string;
  brandNeutral: string;
  brandTint: number;
  logo: string;
  actions: QuickAction[];
  theme: Theme;
  voiceName: string;
  fontHeading: string;
  fontHeadingFallback: string;
  fontBody: string;
  fontBodyFallback: string;
  fontSource: "google" | "curated";
  /** Temporary prompt override, never persisted by the editor. */
  agentRepo: string;
  model: string;
  /** null inherits defaults; [] explicitly disables optional catalogue tools. */
  enabledTools: string[] | null;
  mcpServers: McpServerConfig[];
}

export type BrandingDraft = Omit<AssistantDraft, "lsWorkspace" | "agentRepo" | "model" | "enabledTools" | "mcpServers">;

export const WORKSPACE_LS_KEY = "dashboardWorkspace";
export const LAST_OWNER_LS_KEY = "lastOwner";

export function readSessionPreference(key: string): string {
  try {
    return localStorage.getItem(key) || "";
  } catch {
    return "";
  }
}

export function writeSessionPreference(key: string, value: string): void {
  try {
    if (value) localStorage.setItem(key, value);
    else localStorage.removeItem(key);
  } catch {
    // Session state remains usable when browser persistence is unavailable.
  }
}

export function blankDraft(workspace: string): AssistantDraft {
  return {
    lsWorkspace: workspace, name: "", accent: "#0072BC", accent2: "", brandNeutral: "",
    brandTint: DEFAULT_TINT, logo: "", actions: [], theme: "dark", voiceName: "",
    fontHeading: "", fontHeadingFallback: DEFAULT_CURATED,
    fontBody: "", fontBodyFallback: DEFAULT_CURATED, fontSource: "google",
    agentRepo: "", model: "", enabledTools: null, mcpServers: [],
  };
}

/** Keep the presenter's workspace choice when restoring or selecting an assistant. */
export function draftFromAssistant(assistant: Assistant, workspace: string): AssistantDraft {
  const m = assistant.metadata || {};
  const ctx = assistant.context || {};
  return {
    lsWorkspace: workspace,
    name: m.display_name || "AI Assistant",
    accent: m.accent || "#0072BC",
    accent2: m.accent2 || "",
    brandNeutral: (m.brand_neutral as string) || "",
    brandTint: typeof m.brand_tint === "number" ? m.brand_tint : DEFAULT_TINT,
    logo: m.logo || "",
    actions: Array.isArray(m.actions) && m.actions.length ? m.actions : [],
    theme: coerceTheme(m.theme),
    voiceName: ((m.voice as { voice_name?: string } | undefined)?.voice_name as string) || "",
    fontHeading: (m.font_heading as string) || "",
    fontHeadingFallback: (m.font_heading_fallback as string) || DEFAULT_CURATED,
    fontBody: (m.font_body as string) || "",
    fontBodyFallback: (m.font_body_fallback as string) || DEFAULT_CURATED,
    fontSource: m.font_source === "curated" ? "curated" : "google",
    agentRepo: (ctx.agent_repo as string) || "",
    model: (ctx.model as string) || "",
    enabledTools: Array.isArray(ctx.enabled_tools) ? (ctx.enabled_tools as string[]) : null,
    mcpServers: Array.isArray(ctx.mcp_servers) ? (ctx.mcp_servers as McpServerConfig[]) : [],
  };
}

/** Omit unset run overrides; retain explicit empty tool selections. */
export function sessionRunContext(draft: AssistantDraft, project: string): RunContext {
  const context: RunContext = {};
  if (draft.agentRepo) context.agent_repo = draft.agentRepo;
  if (draft.model) context.model = draft.model;
  if (draft.lsWorkspace) context.ls_workspace = draft.lsWorkspace;
  if (project) context.ls_project = project;
  if (draft.enabledTools !== null) context.enabled_tools = draft.enabledTools;
  const servers = draft.mcpServers.filter((s) => s.enabled !== false && s.url.trim());
  if (servers.length) context.mcp_servers = servers;
  return context;
}

export function brandingMetadata(current: AssistantMetadata | undefined, draft: AssistantDraft): AssistantMetadata {
  return {
    ...current,
    display_name: draft.name, accent: draft.accent, accent2: draft.accent2,
    brand_neutral: draft.brandNeutral, brand_tint: draft.brandTint,
    font_heading: draft.fontHeading, font_heading_fallback: draft.fontHeadingFallback,
    font_body: draft.fontBody, font_body_fallback: draft.fontBodyFallback,
    font_source: draft.fontSource, logo: draft.logo, actions: draft.actions, theme: draft.theme,
    voice: { ...((current?.voice as object) || {}), voice_name: draft.voiceName },
  };
}

/** Display changes are immediate; context stays saved so evals do not grade a preview. */
export function previewAssistant(saved: Assistant | null, draft: AssistantDraft): Assistant | null {
  if (!saved) return null;
  return {
    ...saved,
    metadata: {
      ...saved.metadata,
      display_name: draft.name, accent: draft.accent, accent2: draft.accent2,
      logo: draft.logo, actions: draft.actions, theme: draft.theme,
      voice: { ...((saved.metadata?.voice as object) || {}), voice_name: draft.voiceName },
    },
  };
}
