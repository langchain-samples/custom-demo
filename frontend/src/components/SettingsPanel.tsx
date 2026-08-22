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
  listAssistants,
  listAgents,
  listHubPrompts,
  listTools,
  listWorkspaces,
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
import { getAssistantId, isAssistantId, setAssistantId } from "@/lib/config";
import { WorkspaceSelect } from "./settings/WorkspaceSelect";
import { AssistantSelect } from "./settings/AssistantSelect";
import { NewAssistantDialog, type NewAssistantValues } from "./settings/NewAssistantDialog";
import { DemoBriefDialog, type DemoBrief } from "./settings/DemoBriefDialog";
import { OnboardingDialog } from "./settings/OnboardingDialog";
import { VisualSection } from "./settings/VisualSection";
import { BrandSection } from "./settings/BrandSection";
import { TypographySection } from "./settings/TypographySection";
import { AgentConfig } from "./settings/AgentConfig";
import { ToolsSection } from "./settings/ToolsSection";
import { DeleteAssistant } from "./settings/DeleteAssistant";
import { DemoTraffic } from "./settings/DemoTraffic";
import type { PanelConfig, PromptMode } from "./settings/types";
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
    const [assistants, setAssistants] = useState<Assistant[]>([]);
    const [workspaces, setWorkspaces] = useState<Workspace[]>([]);

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

    const [hubPrompts, setHubPrompts] = useState<string[]>([]);
    const [agents, setAgents] = useState<string[]>([]);
    const [toolSpecs, setToolSpecs] = useState<ToolSpec[]>([]);
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
    const [showNewForm, setShowNewForm] = useState(false);
    const [creating, setCreating] = useState(false);
    // Post-setup presenter brief popup (null = hidden).
    const [demoBrief, setDemoBrief] = useState<DemoBrief | null>(null);
    // First-run onboarding (shown once when no owner name is saved yet).
    const [showOnboarding, setShowOnboarding] = useState(false);
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

    const loadHubPrompts = useCallback(async (workspace: string) => {
      // Prompt sources are workspace-scoped; only fetch once a workspace is chosen.
      if (!workspace) {
        setHubPrompts([]);
        setAgents([]);
        return;
      }
      const [prompts, agentRepos] = await Promise.all([
        listHubPrompts(workspace),
        listAgents(workspace),
      ]);
      setHubPrompts(prompts);
      setAgents(agentRepos);
    }, []);

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

    const loadAll = useCallback(async () => {
      const [alist, wlist, tlist] = await Promise.all([
        listAssistants(),
        listWorkspaces(),
        listTools(),
      ]);
      setAssistants(alist);
      setWorkspaces(wlist);
      setToolSpecs(tlist);
      // First-run: no owner name saved yet ⇒ treat as a new DE and onboard once.
      if (!onboardingCheckedRef.current) {
        onboardingCheckedRef.current = true;
        if (!readLS(LAST_OWNER_LS_KEY)) setShowOnboarding(true);
      }
      // On the very first load, restore the saved assistant's config (no reset).
      const saved = selectedIdRef.current;
      if (isAssistantId(saved) && alist.some((a) => a.assistant_id === saved)) {
        setCfg((c) => {
          const a = alist.find((x) => x.assistant_id === saved)!;
          return configFromAssistant(a, c.lsWorkspace);
        });
      }
      await loadHubPrompts(cfgRef.current.lsWorkspace);
    }, [loadHubPrompts]);

    // Initial load, and a refresh each time the panel opens (matches the SPA).
    useEffect(() => {
      void loadAll();
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);
    useEffect(() => {
      if (open) void loadAll();
    }, [open, loadAll]);

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
    }, [selectedId, assistants, cfg.name, cfg.accent, cfg.accent2, cfg.brandNeutral, cfg.brandTint, cfg.logo, cfg.actions, cfg.theme, onActiveAssistantChange]);

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
        };
        try {
          const updated = await updateAssistant(id, { metadata: meta });
          setAssistants((list) =>
            list.map((a) => (a.assistant_id === id ? updated : a)),
          );
        } catch {
          /* non-fatal: branding still applied locally */
        }
      }, 600);
    }, []);

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
          const updated = await updateAssistant(id, {
            context: { ...(src?.context || {}), enabled_tools: ids },
          });
          setAssistants((list) => list.map((a) => (a.assistant_id === id ? updated : a)));
        } catch {
          /* non-fatal: the selection still applies to this session's runs */
        }
      }, 600);
    }, []);

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
    const handleWorkspace = useCallback(
      (id: string) => {
        setCfg((c) => ({ ...c, lsWorkspace: id }));
        writeLS(WORKSPACE_LS_KEY, id);
        void loadHubPrompts(id); // prompts are per-workspace
      },
      [loadHubPrompts],
    );

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
        const list = await listAssistants();
        setAssistants(list);
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
      [applySelection, onOpenChange],
    );

    const handleCreate = useCallback(
      async (v: NewAssistantValues) => {
        const workspace = cfgRef.current.lsWorkspace;
        if (!v.customer) {
          window.alert("Customer is required - it's used as the assistant name.");
          return;
        }
        if (!workspace) {
          window.alert("Pick a Workspace first (top of the panel) - setup needs it.");
          return;
        }
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
      [runCreate],
    );

    // First-run onboarding: capture the DE's name + workspace, create their first
    // demo, then dismiss. Reuses runCreate; website is left blank (LLM guesses)
    // and the failure mode defaults to the hallucination demo. Demo traffic is off
    // here as it is in the create form — this is someone's first minute in the app,
    // which is the worst moment to silently fill a project with priced runs.
    const handleOnboardingCreate = useCallback(
      async (name: string, workspace: string, customer: string, useCase: string) => {
        handleWorkspace(workspace); // persist + load that workspace's prompts
        setCreating(true);
        try {
          await runCreate(
            {
              owner: name,
              customer,
              website: "",
              useCase,
              failureMode: "hallucination",
              promptSource: "context_hub",
              demoTraffic: false,
            },
            workspace,
          );
          setShowOnboarding(false);
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
        const list = await listAssistants();
        setAssistants(list);
        applySelection("", list, false);
      } catch (e) {
        window.alert("Delete failed: " + errMsg(e));
      }
    }, [applySelection]);

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
      <OnboardingDialog
        open={showOnboarding}
        workspaces={workspaces}
        creating={creating}
        onCreate={handleOnboardingCreate}
      />
      <DemoBriefDialog brief={demoBrief} onClose={() => setDemoBrief(null)} />
      {/* Outside the Sheet on purpose: a modal here covers the settings panel and the
          page, so the only editable thing on screen is the customer being created. */}
      {showNewForm && (
        <NewAssistantDialog
          initialOwner={readLS(LAST_OWNER_LS_KEY)}
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
