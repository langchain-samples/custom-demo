/**
 * SETTINGS panel, ported from the SPA's gear panel into a shadcn Sheet.
 *
 * Sections, top-to-bottom (the whole config block below the assistant selector
 * is hidden until a real assistant — a UUID — is selected):
 *   1. WORKSPACE  — required <Select> from GET /workspaces; scopes prompts.
 *   2. ASSISTANT  — <Select> of assistants + inline "+ New" create flow
 *                   (assistant_setup graph → POST /assistants → select).
 *   3. VISUAL     — display name / accent / logo / quick actions; branding lives
 *                   in the assistant metadata and is debounce-PATCHed on edit.
 *   4. AGENT CFG  — [Prompt Hub | Prompt] toggle, withheld data, synthetic prompt.
 *   5. DELETE     — danger delete with confirm.
 *
 * All data goes through src/lib/api.ts. The selected assistant + resolved run
 * context are surfaced to the parent (App/ChatPanel) via `onActiveAssistantChange`
 * and the imperative `getRunContext()`/`getGuards()` handle. Send-guards are
 * enforced by the parent; this panel only exposes the state they need.
 */
import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from "react";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet";
import {
  cleanupAssistantArtifacts,
  createAssistant,
  deleteAssistant,
  runEvalExperiment,
  runSetup,
  updateAssistant,
  type Assistant,
  type AssistantMetadata,
  type QuickAction,
  type RunContext,
  type ToolSpec,
  type Workspace,
} from "@/lib/api";
import {
  useAgents,
  useAssistants,
  useHubPrompts,
  useRefetchAssistants,
  useReplaceAssistantInCache,
  useTools,
  useWorkspaces,
} from "@/lib/queries";
import { getAssistantId, isAssistantId, setAssistantId } from "@/lib/config";
import { WorkspaceSelect } from "./settings/WorkspaceSelect";
import { AssistantSelect } from "./settings/AssistantSelect";
import { NewAssistantDialog, type NewAssistantValues } from "./settings/NewAssistantDialog";
import { DemoBriefDialog, type DemoBrief } from "./settings/DemoBriefDialog";
import { VisualSection } from "./settings/VisualSection";
import { BrandSection } from "./settings/BrandSection";
import { TypographySection } from "./settings/TypographySection";
import { AgentConfig } from "./settings/AgentConfig";
import { ToolsSection } from "./settings/ToolsSection";
import { DeleteAssistant } from "./settings/DeleteAssistant";
import { DemoTraffic } from "./settings/DemoTraffic";
import type { PanelConfig, PromptMode } from "./settings/types";
import { VoicePicker } from "@/components/settings/VoicePicker";
import { coerceTheme } from "@/lib/theme";
import type { Theme } from "@/lib/theme";
import { applyBrand, DEFAULT_TINT } from "@/lib/branding";
import { traceProject } from "@/lib/trace";
import { applyTypography, DEFAULT_CURATED, type FontStatus } from "@/lib/fonts";

/* --------------------------- Public prop surface --------------------------- */

/** Send-guard flags the parent (App/ChatPanel) enforces before a run. */
export interface SettingsGuards {
  /** A real assistant (UUID) is selected. */
  hasAssistant: boolean;
  /** A workspace is chosen. */
  hasWorkspace: boolean;
  /** A system prompt is available (Hub handle selected, or inline text present). */
  hasPrompt: boolean;
}

/** Imperative handle for send-time resolution (parent holds a ref). */
export interface SettingsHandle {
  /** Resolve the per-run context to send in the run body (non-empty fields only). */
  getRunContext: () => RunContext;
  /** Current send-guard flags. */
  getGuards: () => SettingsGuards;
  /** Set + persist the active assistant's theme (light/dark). */
  setTheme: (theme: Theme) => void;
}

export interface SettingsPanelProps {
  /** Whether the settings Sheet is open. */
  open: boolean;
  /** Open/close the Sheet. */
  onOpenChange: (open: boolean) => void;
  /**
   * Fires whenever the active assistant changes OR its branding is edited. The
   * assistant's metadata carries the live branding (display_name / accent / logo
   * / actions) the header + presets should reflect. `null` when no real
   * assistant is selected.
   */
  onActiveAssistantChange?: (assistant: Assistant | null) => void;
  /**
   * Fires when the assistant is switched or a new one is created — the parent
   * should reset the dashboard + chat and mint a fresh thread.
   */
  onResetConversation?: () => void;
}

/* ------------------------------- Defaults -------------------------------- */

const DEFAULT_ACTIONS: QuickAction[] = [
  {
    label: "Donor: impact of aid in Egypt last quarter",
    question:
      "What is the impact of humanitarian aid in Egypt over the last quarter, according to the latest reports?",
  },
  {
    label: "Affected: resources for displaced families in Iran",
    question:
      "What are the available resources for displaced families in Iran as outlined in the latest situation report?",
  },
  {
    label: "NGO: water & sanitation needs in Canada",
    question:
      "Can you provide the latest data on water scarcity and sanitation needs in Canada from relevant assessments?",
  },
];

const DEFAULT_NAME = "Dashboard Agent - Humanitarian Insights";
const DEFAULT_ACCENT = "#0072BC";
const DEFAULT_LOGO = "";

const WORKSPACE_LS_KEY = "dashboardWorkspace";

/**
 * Stable fallbacks for a query that has not resolved. `?? []` inline would allocate a
 * new array on every render, which re-runs every effect and memo keyed on the list.
 */
const EMPTY_ASSISTANTS: Assistant[] = [];
const EMPTY_WORKSPACES: Workspace[] = [];
const EMPTY_NAMES: string[] = [];
const LAST_OWNER_LS_KEY = "lastOwner";

/* ------------------------------- Helpers --------------------------------- */


function readLS(key: string): string {
  try {
    return localStorage.getItem(key) || "";
  } catch {
    return "";
  }
}
function writeLS(key: string, value: string): void {
  try {
    if (value) localStorage.setItem(key, value);
    else localStorage.removeItem(key);
  } catch {
    /* ignore */
  }
}

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}


/** Branding+config derived from an assistant's metadata/context (keep workspace). */
function configFromAssistant(a: Assistant, workspace: string): PanelConfig {
  const m = a.metadata || {};
  const ctx = a.context || {};
  return {
    lsWorkspace: workspace,
    name: m.display_name || DEFAULT_NAME,
    accent: m.accent || DEFAULT_ACCENT,
    accent2: m.accent2 || "",
    brandNeutral: (m.brand_neutral as string) || "",
    brandTint: typeof m.brand_tint === "number" ? m.brand_tint : DEFAULT_TINT,
    logo: m.logo || DEFAULT_LOGO,
    actions: Array.isArray(m.actions) && m.actions.length ? m.actions : DEFAULT_ACTIONS,
    theme: coerceTheme(m.theme),
    voiceName: ((m.voice as { voice_name?: string } | undefined)?.voice_name as string) || "",
    fontHeading: (m.font_heading as string) || "",
    fontHeadingFallback: (m.font_heading_fallback as string) || DEFAULT_CURATED,
    fontBody: (m.font_body as string) || "",
    fontBodyFallback: (m.font_body_fallback as string) || DEFAULT_CURATED,
    fontSource: m.font_source === "curated" ? "curated" : "google",
    promptName: (ctx.prompt_name as string) || "",
    agentRepo: (ctx.agent_repo as string) || "",
    systemPrompt: (ctx.prompt as string) || "",
    dataPrompt: (ctx.data_prompt as string) || "",
    dataGap: (ctx.data_gap as string) || "",
    // null = no saved selection (backend defaults); [] = everything optional off.
    enabledTools: Array.isArray(ctx.enabled_tools) ? (ctx.enabled_tools as string[]) : null,
    // Reflect whichever prompt source the assistant is configured with.
    promptMode: ctx.prompt ? "inline" : ctx.agent_repo ? "context_hub" : "prompt_hub",
  };
}

/** Blank config (no assistant selected), preserving the chosen workspace. */
function blankConfig(workspace: string): PanelConfig {
  return {
    lsWorkspace: workspace,
    name: "",
    accent: DEFAULT_ACCENT,
    accent2: "",
    brandNeutral: "",
    brandTint: DEFAULT_TINT,
    voiceName: "",
    logo: "",
    actions: [],
    theme: "dark",
    fontHeading: "",
    fontHeadingFallback: DEFAULT_CURATED,
    fontBody: "",
    fontBodyFallback: DEFAULT_CURATED,
    fontSource: "google",
    promptMode: "prompt_hub",
    promptName: "",
    agentRepo: "",
    systemPrompt: "",
    dataGap: "",
    dataPrompt: "",
    enabledTools: null,
  };
}

/** Resolve the per-run context — mirrors the SPA's `runContext()`. */
function resolveRunContext(cfg: PanelConfig, project: string): RunContext {
  const ctx: RunContext = {};
  if (cfg.promptMode === "inline") {
    if (cfg.systemPrompt) ctx.prompt = cfg.systemPrompt;
  } else if (cfg.promptMode === "context_hub") {
    if (cfg.agentRepo) ctx.agent_repo = cfg.agentRepo;
  } else if (cfg.promptName) {
    ctx.prompt_name = cfg.promptName;
  }
  if (cfg.dataPrompt) ctx.data_prompt = cfg.dataPrompt;
  if (cfg.dataGap) ctx.data_gap = cfg.dataGap;
  if (cfg.lsWorkspace) ctx.ls_workspace = cfg.lsWorkspace;
  // Not user-editable here; see traceProject() for how it's derived.
  if (project) ctx.ls_project = project;
  // Deliberately a null check, NOT a length check: [] means "every optional tool
  // off" and must reach the backend. Omitting it would restore the defaults.
  if (cfg.enabledTools !== null) ctx.enabled_tools = cfg.enabledTools;
  return ctx;
}

/* ------------------------------- Component ------------------------------- */

export const SettingsPanel = forwardRef<SettingsHandle, SettingsPanelProps>(
  function SettingsPanel(
    { open, onOpenChange, onActiveAssistantChange, onResetConversation },
    ref,
  ) {
    /**
     * Server state now lives in react-query (see lib/queries.ts), not in useState. The
     * local `const`s below keep the ~15 read sites in this file unchanged, and the
     * mutations further down invalidate rather than re-fetching by hand.
     */
    const assistantsQuery = useAssistants();
    const assistants = assistantsQuery.data ?? EMPTY_ASSISTANTS;
    const workspacesQuery = useWorkspaces();
    const workspaces = workspacesQuery.data?.workspaces ?? EMPTY_WORKSPACES;
    const organization = workspacesQuery.data?.organization ?? "";
    // Org the workspaces belong to, for labelling the create form's picker.

    // Set when a saved workspace id turned out not to exist any more, so the create
    // form can say why it is asking for one instead of looking arbitrary.
    const [workspaceReset, setWorkspaceReset] = useState(false);

    // Width (px) of the Customize panel — drag-resizable from its left edge,
    // mirroring the chat rail (see App.tsx). Persisted across sessions.
    const [panelWidth, setPanelWidth] = useState<number>(() => {
      try {
        const v = Number(localStorage.getItem("settingsPanelWidth"));
        return v >= 320 && v <= 760 ? v : 380;
      } catch {
        return 380;
      }
    });
    const [resizing, setResizing] = useState(false);

    const startResize = useCallback((e: React.PointerEvent) => {
      e.preventDefault();
      setResizing(true);
      const onMove = (ev: PointerEvent) => {
        // Panel is pinned to the right edge, so width grows as the pointer
        // moves left. Clamp so it never fully covers the app.
        const w = Math.min(
          Math.max(window.innerWidth - ev.clientX, 320),
          Math.min(760, window.innerWidth - 120),
        );
        setPanelWidth(w);
      };
      const onUp = () => {
        setResizing(false);
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    }, []);

    // Persist the width once a drag settles.
    useEffect(() => {
      if (resizing) return;
      try {
        localStorage.setItem("settingsPanelWidth", String(panelWidth));
      } catch {
        /* ignore */
      }
    }, [resizing, panelWidth]);

    const [toolSpecsFallback] = useState<ToolSpec[]>([]);
    const [fontStatus, setFontStatus] = useState<{ heading: FontStatus; body: FontStatus }>({
      heading: "curated",
      body: "curated",
    });
    const [selectedId, setSelectedId] = useState<string>(() => {
      const saved = getAssistantId();
      return isAssistantId(saved) ? saved : "";
    });
    const [cfg, setCfg] = useState<PanelConfig>(() =>
      blankConfig(readLS(WORKSPACE_LS_KEY)),
    );
    // Workspace-scoped reads. Keyed on the workspace, which is what replaced calling a
    // `loadHubPrompts(id)` by hand from every path that could change it.
    const hubPromptsQuery = useHubPrompts(cfg.lsWorkspace);
    const hubPrompts = hubPromptsQuery.data ?? EMPTY_NAMES;
    const agentsQuery = useAgents(cfg.lsWorkspace);
    const agents = agentsQuery.data ?? EMPTY_NAMES;
    const toolsQuery = useTools();
    // Cache writers for the mutation sites below. The debounced saves patch one entry;
    // create and delete invalidate, which is what replaced their manual re-fetch.
    const replaceAssistant = useReplaceAssistantInCache();
    const refetchAssistants = useRefetchAssistants();
    const toolSpecs = toolsQuery.data ?? toolSpecsFallback;

    const [showNewForm, setShowNewForm] = useState(false);
    const [creating, setCreating] = useState(false);
    // Post-setup presenter brief popup (null = hidden).
    const [demoBrief, setDemoBrief] = useState<DemoBrief | null>(null);
    // First run (no owner name saved yet) opens the create form, once.
    const onboardingCheckedRef = useRef(false);

    // Latest-value refs for async callbacks (debounced save, create/delete).
    const cfgRef = useRef(cfg);
    cfgRef.current = cfg;
    const assistantsRef = useRef(assistants);
    assistantsRef.current = assistants;
    const selectedIdRef = useRef(selectedId);
    selectedIdRef.current = selectedId;
    const brandingTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

    /* ---- Data loading ---- */

    // Apply a selection: load its config, persist the id, notify the parent, and
    // optionally reset the conversation (on switch/create, not delete/restore).
    const applySelection = useCallback(
      (id: string, list: Assistant[], reset: boolean) => {
        setSelectedId(id);
        setAssistantId(id); // persist (blank clears it)
        const a = id ? list.find((x) => x.assistant_id === id) || null : null;
        setCfg((c) => (a ? configFromAssistant(a, c.lsWorkspace) : blankConfig(c.lsWorkspace)));
        if (reset) onResetConversation?.();
      },
      [onResetConversation],
    );

    /**
     * A saved workspace id outlives the org it belonged to. After the move to a new org
     * every stored id was still sent to the setup graph, which asked LangSmith about a
     * workspace this key cannot see and failed the whole run with a raw "403 Forbidden
     * on /settings" 30 seconds in. An id absent from the list is not recoverable, so
     * drop it and let the picker ask again.
     */
    useEffect(() => {
      if (!cfg.lsWorkspace || workspaces.length === 0) return;
      if (workspaces.some((w) => w.id === cfg.lsWorkspace)) return;
      setCfg((c) => ({ ...c, lsWorkspace: "" }));
      writeLS(WORKSPACE_LS_KEY, "");
      setWorkspaceReset(true);
      setShowNewForm(true);
    }, [cfg.lsWorkspace, workspaces]);

    /**
     * First run (no owner name saved) opens the ordinary create form, once. There used
     * to be a separate onboarding dialog: the same form with fewer fields plus a
     * workspace picker, two things to keep in step, and a new user's first screen was
     * the one nobody ever edited. The picker moved into this form instead.
     */
    useEffect(() => {
      if (assistantsQuery.isPending || onboardingCheckedRef.current) return;
      onboardingCheckedRef.current = true;
      if (!readLS(LAST_OWNER_LS_KEY)) setShowNewForm(true);
    }, [assistantsQuery.isPending]);

    /** Restore the saved assistant's config once the list arrives (no reset). */
    const restoredRef = useRef(false);
    useEffect(() => {
      if (restoredRef.current || assistants.length === 0) return;
      const saved = selectedIdRef.current;
      const a = isAssistantId(saved)
        ? assistants.find((x) => x.assistant_id === saved)
        : undefined;
      if (!a) return;
      restoredRef.current = true;
      setCfg((c) => configFromAssistant(a, c.lsWorkspace));
    }, [assistants]);

    /* ---- Live branding → parent (header/presets) + accent CSS vars ---- */
    useEffect(() => {
      const base = selectedId ? assistants.find((a) => a.assistant_id === selectedId) : null;
      if (base) {
        const merged: Assistant = {
          ...base,
          metadata: {
            ...(base.metadata || {}),
            display_name: cfg.name,
            accent: cfg.accent,
            accent2: cfg.accent2,
            logo: cfg.logo,
            actions: cfg.actions,
            theme: cfg.theme,
            // From `cfg`, not `base`, so a voice change applies to the next session
            // immediately rather than 600ms later when the PATCH lands.
            voice: { ...((base.metadata?.voice as object) || {}), voice_name: cfg.voiceName },
          },
        };
        // One call writes every brand token (seeds, contrast foreground, derived
        // chart series). See lib/branding.ts for the rules it follows.
        applyBrand({
          primary: cfg.accent,
          secondary: cfg.accent2,
          neutral: cfg.brandNeutral,
          tint: cfg.brandTint,
        });
        onActiveAssistantChange?.(merged);
      } else {
        applyBrand(null); // clear overrides → the unbranded defaults in index.css
        onActiveAssistantChange?.(null);
      }
    }, [selectedId, assistants, cfg.name, cfg.accent, cfg.accent2, cfg.brandNeutral, cfg.brandTint, cfg.logo, cfg.actions, cfg.theme, cfg.voiceName, onActiveAssistantChange]);

    /* ---- Typography: async (may hit the font CDN), so kept separate ---- */
    useEffect(() => {
      let cancelled = false;
      const active = isAssistantId(selectedId);
      void applyTypography(
        active
          ? {
              heading: { family: cfg.fontHeading, fallback: cfg.fontHeadingFallback },
              body: { family: cfg.fontBody, fallback: cfg.fontBodyFallback },
              source: cfg.fontSource,
            }
          : null,
      ).then((status) => {
        // A rapid edit can resolve out of order; only the latest wins.
        if (!cancelled) setFontStatus(status);
      });
      return () => {
        cancelled = true;
      };
    }, [selectedId, cfg.fontHeading, cfg.fontHeadingFallback, cfg.fontBody, cfg.fontBodyFallback, cfg.fontSource]);

    /* ---- Imperative handle: defined below, after editBranding ---- */

    /* ---- Branding edits: update state + debounced metadata PATCH ---- */
    const scheduleBrandingSave = useCallback((next: PanelConfig) => {
      const id = selectedIdRef.current;
      if (!isAssistantId(id)) return;
      clearTimeout(brandingTimer.current);
      brandingTimer.current = setTimeout(async () => {
        const src = assistantsRef.current.find((a) => a.assistant_id === id);
        const meta: AssistantMetadata = {
          ...(src?.metadata || {}),
          display_name: next.name,
          accent: next.accent,
          accent2: next.accent2,
          brand_neutral: next.brandNeutral,
          brand_tint: next.brandTint,
          font_heading: next.fontHeading,
          font_heading_fallback: next.fontHeadingFallback,
          font_body: next.fontBody,
          font_body_fallback: next.fontBodyFallback,
          font_source: next.fontSource,
          logo: next.logo,
          actions: next.actions,
          theme: next.theme,
          // Merge, never replace: whatever else setup wrote under `voice` (nothing today,
          // but this is the one metadata key another writer is likely to extend) survives.
          voice: { ...((src?.metadata?.voice as object) || {}), voice_name: next.voiceName },
        };
        try {
          replaceAssistant(await updateAssistant(id, { metadata: meta }));
        } catch {
          /* non-fatal: branding still applied locally */
        }
      }, 600);
    }, [replaceAssistant]);

    const editBranding = useCallback(
      (patch: Partial<PanelConfig>) => {
        setCfg((c) => {
          const next = { ...c, ...patch };
          scheduleBrandingSave(next);
          return next;
        });
      },
      [scheduleBrandingSave],
    );

    // Agent-config edits feed the run context only (not saved to the assistant).
    const editConfig = useCallback((patch: Partial<PanelConfig>) => {
      setCfg((c) => ({ ...c, ...patch }));
    }, []);

    /* ---- Tool selection: run context AND persisted onto the assistant ---- */
    const toolsTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

    const editTools = useCallback((ids: string[]) => {
      setCfg((c) => ({ ...c, enabledTools: ids }));
      const id = selectedIdRef.current;
      if (!isAssistantId(id)) return;
      clearTimeout(toolsTimer.current);
      toolsTimer.current = setTimeout(async () => {
        const src = assistantsRef.current.find((a) => a.assistant_id === id);
        try {
          // PATCH replaces `context` wholesale — spread the existing one or this
          // wipes prompt_name / ls_workspace / data_gap.
          replaceAssistant(
            await updateAssistant(id, {
              context: { ...(src?.context || {}), enabled_tools: ids },
            }),
          );
        } catch {
          /* non-fatal: the selection still applies to this session's runs */
        }
      }, 600);
    }, [replaceAssistant]);

    /* ---- Imperative handle (send-time context + guards + theme) ---- */
    useImperativeHandle(
      ref,
      () => ({
        getRunContext: () => {
          const c = cfgRef.current;
          const id = selectedIdRef.current;
          const a = assistantsRef.current.find((x) => x.assistant_id === id) || null;
          return resolveRunContext(c, traceProject(a, id));
        },
        getGuards: () => {
          const c = cfgRef.current;
          return {
            hasAssistant: isAssistantId(selectedIdRef.current),
            hasWorkspace: !!c.lsWorkspace,
            hasPrompt:
              c.promptMode === "inline"
                ? !!c.systemPrompt.trim()
                : c.promptMode === "context_hub"
                  ? !!c.agentRepo
                  : !!c.promptName,
          };
        },
        // Persist a theme choice into the active assistant's metadata (so it
        // becomes that brand's default) and reflect it live via onActiveAssistantChange.
        setTheme: (t) => editBranding({ theme: t }),
      }),
      [editBranding],
    );

    /* ---- Workspace ---- */
    const handleWorkspace = useCallback((id: string) => {
      setCfg((c) => ({ ...c, lsWorkspace: id }));
      writeLS(WORKSPACE_LS_KEY, id);
      // No explicit refetch: the prompt and agent queries are keyed on the workspace, so
      // changing it here is the fetch. That is the whole reason they are queries.
    }, []);

    /* ---- Assistant selection / create / delete ---- */
    const handleSelectAssistant = useCallback(
      (id: string) => {
        setShowNewForm(false);
        applySelection(id, assistantsRef.current, true);
      },
      [applySelection],
    );

    // Shared create path: run the setup agent, create the assistant, select it,
    // and surface the presenter brief. Used by both the Settings "+ New" form and
    // the first-run onboarding. Throws on failure so callers can react.
    const runCreate = useCallback(
      async (v: NewAssistantValues, workspace: string) => {
        if (v.owner) writeLS(LAST_OWNER_LS_KEY, v.owner);
        // The deployed setup agent fetches branding + generates quick actions
        // (and optionally pushes prompts); we then create the assistant from it.
        const result = await runSetup({
          workspace,
          customer: v.customer,
          owner: v.owner,
          website: v.website,
          use_case: v.useCase,
          failure_mode: v.failureMode,
          prompt_source: v.promptSource === "prompt_hub" ? "prompt_hub" : "context_hub",
          push_prompts: true,
          // Off unless the presenter asked for it: it fills the customer's project
          // with runs they never made, priced like they did.
          demo_traffic: v.demoTraffic,
        });
        // Hoisted so the baseline experiment below runs against the SAME context
        // the assistant was created with.
        const assistantContext = result.context || { ls_workspace: workspace };
        const a = await createAssistant({
          name: v.customer,
          context: assistantContext,
          metadata: result.metadata || { owner_name: v.owner, customer: v.customer },
        });
        // Kick off the BASELINE experiment over the dataset setup just planted,
        // so the presenter finds a score already waiting (red, 2/3, for the
        // hallucination demo). Fire-and-forget on purpose: it is three real
        // agent turns, and nothing here may delay or break the create path —
        // runEvalExperiment resolves with { ok: false } instead of throwing, so
        // there is no rejection to swallow. Skipped when setup planted no
        // dataset (best-effort creation, or a failure mode without evals).
        const evalDataset = (result.metadata || a.metadata)?.ls_artifacts?.eval_dataset;
        if (evalDataset) {
          void runEvalExperiment({
            assistant_id: a.assistant_id,
            dataset: evalDataset,
            workspace,
            // Without this the baseline grades a default agent and the planted
            // bug never fires — the run would come back a meaningless 3/3.
            context: assistantContext,
          });
        }
        // refetch rather than invalidate-then-fetch: applySelection needs the fresh
        // list in hand, and this is one request instead of two.
        const list = await refetchAssistants();
        applySelection(a.assistant_id, list, true);
        // Surface the presenter brief the setup agent generated. Close the
        // settings sheet so it lands front-and-centre over the fresh demo.
        const meta = result.metadata || a.metadata || {};
        const briefLines = meta.demo_brief || [];
        const flowLines = meta.demo_flow || [];
        if (briefLines.length || flowLines.length) {
          onOpenChange(false);
          setDemoBrief({ customer: v.customer, brief: briefLines, flow: flowLines });
        }
      },
      [applySelection, onOpenChange, refetchAssistants],
    );

    const handleCreate = useCallback(
      async (v: NewAssistantValues) => {
        // The dialog only offers a workspace when the panel has none, so its value wins
        // when present and the panel's is the steady-state answer.
        const workspace = v.workspace || cfgRef.current.lsWorkspace;
        if (!v.customer) {
          window.alert("Customer is required - it's used as the assistant name.");
          return;
        }
        if (!workspace) {
          window.alert("Pick a Workspace first (top of the panel) - setup needs it.");
          return;
        }
        // Persist it and load that workspace's prompts, exactly as picking it in the panel
        // would have - otherwise a first-run user creates into a workspace the panel does
        // not know it is pointed at.
        if (v.workspace) handleWorkspace(v.workspace);
        setCreating(true);
        try {
          await runCreate(v, workspace);
          setShowNewForm(false);
        } catch (e) {
          window.alert("Setup failed: " + errMsg(e));
        } finally {
          setCreating(false);
        }
      },
      [runCreate, handleWorkspace],
    );

    const handleDelete = useCallback(async () => {
      const id = selectedIdRef.current;
      if (!isAssistantId(id)) return;
      try {
        // Cascade-delete the LangSmith artifacts this assistant created (trace
        // project, prompt/agent repo, skills) before dropping the record. Best
        // effort: report any that couldn't be removed, but still delete the
        // assistant so a permission gap can't leave it undeletable.
        const src = assistantsRef.current.find((a) => a.assistant_id === id);
        const artifacts = src?.metadata?.ls_artifacts;
        if (artifacts) {
          const { failed } = await cleanupAssistantArtifacts(artifacts);
          if (failed.length) {
            window.alert(
              "Some LangSmith artifacts could not be deleted:\n" +
                failed.map((f) => `- ${f.artifact}: ${f.error}`).join("\n"),
            );
          }
        }
        await deleteAssistant(id);
        const list = await refetchAssistants();
        applySelection("", list, false);
      } catch (e) {
        window.alert("Delete failed: " + errMsg(e));
      }
    }, [applySelection, refetchAssistants]);

    /* ---- Derived ---- */
    const hasAssistant = isAssistantId(selectedId);
    const selectedAssistant = hasAssistant
      ? assistants.find((a) => a.assistant_id === selectedId) || null
      : null;
    const deleteLabel =
      (selectedAssistant &&
        (selectedAssistant.metadata?.display_name || selectedAssistant.name)) ||
      selectedId;

    // Scope the assistant dropdown to the chosen workspace (there's no server-side
    // filter, so we match on the ls_workspace we record in each assistant's
    // context). Keep legacy assistants that never recorded one, and always keep
    // the active selection so it can't vanish mid-edit.
    const visibleAssistants = cfg.lsWorkspace
      ? assistants.filter((a) => {
          const ws = (a.context?.ls_workspace as string) || "";
          return !ws || ws === cfg.lsWorkspace || a.assistant_id === selectedId;
        })
      : assistants;

    return (
      <>
      <DemoBriefDialog brief={demoBrief} onClose={() => setDemoBrief(null)} />
      {/* Outside the Sheet on purpose: a modal here covers the settings panel and the
          page, so the only editable thing on screen is the customer being created. */}
      {showNewForm && (
        <NewAssistantDialog
          initialOwner={readLS(LAST_OWNER_LS_KEY)}
          initialWorkspace={cfg.lsWorkspace}
          workspaces={workspaces}
          organization={organization}
          workspaceReset={workspaceReset}
          creating={creating}
          onCreate={handleCreate}
          onCancel={() => setShowNewForm(false)}
        />
      )}
      <Sheet open={open} onOpenChange={onOpenChange}>
        <SheetContent
          style={{ width: panelWidth, maxWidth: panelWidth }}
          className={"gap-0 p-0" + (resizing ? " select-none" : "")}
        >
          {/* Drag the left edge to resize the panel (mirrors the chat rail). */}
          <div
            onPointerDown={startResize}
            title="Drag to resize"
            className="absolute top-0 left-0 z-20 h-full w-1.5 cursor-col-resize transition-colors hover:bg-[var(--brand-primary)]/40"
          />
          <SheetHeader className="p-4 pb-2">
            <SheetTitle>Customize</SheetTitle>
          </SheetHeader>

          <div className="flex min-h-0 flex-1 flex-col gap-3.5 overflow-y-auto p-4 pt-0">
            {/* 1. Workspace */}
            <WorkspaceSelect
              value={cfg.lsWorkspace}
              workspaces={workspaces}
              onChange={handleWorkspace}
            />

            <div className="border-t border-border" />

            {/* 2. Assistant */}
            <AssistantSelect
              value={selectedId}
              assistants={visibleAssistants}
              onChange={handleSelectAssistant}
              onNewClick={() => setShowNewForm((s) => !s)}
            />

            {/* 3–5. Config block — hidden until a real assistant is selected */}
            {hasAssistant && (
              <>
                <VisualSection
                  name={cfg.name}
                  logo={cfg.logo}
                  actions={cfg.actions}
                  theme={cfg.theme}
                  onName={(v) => editBranding({ name: v })}
                  onLogo={(v) => editBranding({ logo: v })}
                  onActions={(a) => editBranding({ actions: a })}
                  onTheme={(t) => editBranding({ theme: t })}
                />

                <BrandSection
                  accent={cfg.accent}
                  accent2={cfg.accent2}
                  neutral={cfg.brandNeutral}
                  tint={cfg.brandTint}
                  onAccent={(v) => editBranding({ accent: v })}
                  onAccent2={(v) => editBranding({ accent2: v })}
                  onNeutral={(v) => editBranding({ brandNeutral: v })}
                  onTint={(v) => editBranding({ brandTint: v })}
                >
                  {/* Every assistant can be spoken to, so this always applies. */}
                  <VoicePicker
                    value={cfg.voiceName}
                    onChange={(v) => editBranding({ voiceName: v })}
                  />
                  <TypographySection
                    headingFont={cfg.fontHeading}
                    headingFallback={cfg.fontHeadingFallback}
                    bodyFont={cfg.fontBody}
                    bodyFallback={cfg.fontBodyFallback}
                    useGoogle={cfg.fontSource === "google"}
                    status={fontStatus}
                    onHeadingFont={(v) => editBranding({ fontHeading: v })}
                    onHeadingFallback={(v) => editBranding({ fontHeadingFallback: v })}
                    onBodyFont={(v) => editBranding({ fontBody: v })}
                    onBodyFallback={(v) => editBranding({ fontBodyFallback: v })}
                    onUseGoogle={(v) => editBranding({ fontSource: v ? "google" : "curated" })}
                  />
                </BrandSection>

                <AgentConfig
                  promptMode={cfg.promptMode}
                  promptName={cfg.promptName}
                  agentRepo={cfg.agentRepo}
                  systemPrompt={cfg.systemPrompt}
                  dataGap={cfg.dataGap}
                  dataPrompt={cfg.dataPrompt}
                  hubPrompts={hubPrompts}
                  agents={agents}
                  onPromptMode={(m: PromptMode) => editConfig({ promptMode: m })}
                  onPromptName={(v) => editConfig({ promptName: v })}
                  onAgentRepo={(v) => editConfig({ agentRepo: v })}
                  onSystemPrompt={(v) => editConfig({ systemPrompt: v })}
                  onDataGap={(v) => editConfig({ dataGap: v })}
                  onDataPrompt={(v) => editConfig({ dataPrompt: v })}
                />

                <ToolsSection
                  specs={toolSpecs}
                  enabled={cfg.enabledTools}
                  onChange={editTools}
                />

                <DemoTraffic
                  target={
                    selectedAssistant
                      ? {
                          project:
                            (selectedAssistant.context?.ls_project as string) ||
                            selectedAssistant.metadata?.customer ||
                            selectedAssistant.name ||
                            "",
                          workspace: selectedAssistant.context?.ls_workspace as string,
                          context: selectedAssistant.context,
                          actions: selectedAssistant.metadata?.actions,
                          data_gap: selectedAssistant.context?.data_gap as string,
                          customer: selectedAssistant.metadata?.customer,
                        }
                      : null
                  }
                />

                <DeleteAssistant label={deleteLabel} onDelete={handleDelete} />
              </>
            )}
          </div>
        </SheetContent>
      </Sheet>
      </>
    );
  },
);
