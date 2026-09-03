/**
 * App shell — wires the ported panes into the working two-pane layout:
 *
 *   header (large logo + display name from the active assistant's branding, gear)
 *   ┌─────────────── before any dashboard: single centered chat column ──────────┐
 *   │  ChatPanel                                                                  │
 *   └─────────────────────────────────────────────────────────────────────────── ┘
 *   ┌── once widgets exist: two columns (420px rail + 1fr) ──────────────────────┐
 *   │  ChatPanel  │  DashboardPane (widget canvas + one tab per HTML artifact)     │
 *   └─────────────────────────────────────────────────────────────────────────── ┘
 *
 * The gear opens the SettingsPanel Sheet. App owns the streamed-widget list, the
 * active-assistant branding, the send-guards (delegated to the settings handle),
 * and the conversation-reset key (bumped on assistant switch/create).
 */
import { useEffect, useMemo, useRef, useState } from "react";
import {
  IconSparkles,
  IconSun,
  IconMoon,
  IconSettings,
  IconRobot,
  IconFolders,
  IconFlask,
} from "@tabler/icons-react";
import { Button } from "@/components/motion/button";
import { Tooltip } from "@/components/motion/tooltip";
import ChatPanel, { type ChatPanelHandle } from "@/components/ChatPanel";
import { VoiceButton } from "@/components/VoiceButton";
import { VoiceStage } from "@/components/VoiceStage";
import { useVoiceSession } from "@/lib/hooks/use-voice-session";
import {
  DashboardPane,
  type ActivityState,
  type ArtifactState,
} from "@/components/DashboardPane";
import { EvalPanel } from "@/components/EvalPanel";
import { FileBrowser } from "@/components/FileBrowser";
import { SettingsPanel, type SettingsHandle } from "@/components/SettingsPanel";
import { getAssistantId } from "@/lib/config";
import { applyTheme, getStoredTheme, setStoredTheme, type Theme } from "@/lib/theme";
import { invalidateColorCache } from "@/lib/branding";
import type { Assistant, RunContext, Widget } from "@/lib/api";
import { readSandboxTextFile } from "@/lib/api";

const DEFAULT_NAME = "Corebot";
const DEFAULT_LOGO = "";
const URL_RE = /^(https?:|data:)/i;

/** Header brand logo: an <img> for a URL/data-URI, an emoji span for an emoji, else a Tabler robot. */
function BrandLogo({ logo }: { logo: string }) {
  const v = (logo || "").trim();
  if (URL_RE.test(v)) {
    return (
      <img
        src={v}
        alt="logo"
        className="block h-10 w-10 rounded-md object-contain"
      />
    );
  }
  if (v) return <span className="text-[34px] leading-none">{v}</span>;
  return <IconRobot size={34} stroke={1.5} />;
}

export default function App() {
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [filesOpen, setFilesOpen] = useState(false);
  const [evalsOpen, setEvalsOpen] = useState(false);
  const [activeAssistant, setActiveAssistant] = useState<Assistant | null>(null);
  const [widgets, setWidgets] = useState<Widget[]>([]);
  // HTML files the agent wrote to /workspace/artifacts, keyed by path; each becomes a
  // tab beside the widget canvas. Cleared with the dashboard on reset.
  const [artifacts, setArtifacts] = useState<Record<string, ArtifactState>>({});
  // The current question's tool activity, mirrored out of ChatPanel to draw the Graph
  // tab. Read-only: nothing here feeds back into a run.
  const [activity, setActivity] = useState<ActivityState>({
    chips: [],
    subagents: [],
    running: false,
  });
  // Sticky: once a dashboard has appeared, keep the two-column layout until a
  // conversation reset (assistant switch/create) — matches the SPA's
  // has-dashboard class, which clearDashboard() empties but does not remove.
  const [hasDashboard, setHasDashboard] = useState(false);
  const [resetCounter, setResetCounter] = useState(0);
  // Manual theme preference used when no assistant is active; an active
  // assistant's `metadata.theme` (its brand default) overrides this.
  const [globalTheme, setGlobalTheme] = useState<Theme>(() => getStoredTheme());

  // Width (px) of the chat rail once a dashboard exists — drag-resizable.
  const [chatWidth, setChatWidth] = useState<number>(() => {
    try {
      const v = Number(localStorage.getItem("chatRailWidth"));
      return v >= 320 && v <= 900 ? v : 420;
    } catch {
      return 420;
    }
  });
  const [resizing, setResizing] = useState(false);
  /** Whether the right pane exists at all: widgets, or tool activity to graph. */
  const showPane = hasDashboard || activity.chips.length > 0;

  const startResize = (e: React.PointerEvent) => {
    e.preventDefault();
    setResizing(true);
    const onMove = (ev: PointerEvent) => {
      // Chat rail starts at viewport x=0; clamp so the dashboard keeps ≥380px.
      const w = Math.min(Math.max(ev.clientX, 320), Math.min(760, window.innerWidth - 380));
      setChatWidth(w);
    };
    const onUp = () => {
      setResizing(false);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  };

  // Persist the rail width once a drag settles.
  useEffect(() => {
    if (resizing) return;
    try {
      localStorage.setItem("chatRailWidth", String(chatWidth));
    } catch {
      /* ignore */
    }
  }, [resizing, chatWidth]);

  const settingsRef = useRef<SettingsHandle>(null);
  /** Imperative handle voice mode drives (see VoiceButton). */
  const chatRef = useRef<ChatPanelHandle | null>(null);

  // Branding for the header + chat presets (falls back to the app defaults).
  const meta = activeAssistant?.metadata;
  const displayName = meta?.display_name || DEFAULT_NAME;
  const logo = meta?.logo || DEFAULT_LOGO;
  const presets = meta?.actions ?? [];
  /**
   * The immersive orb view, which REPLACES the chat rail while a conversation is live.
   *
   * Default OFF, and every assistant can talk. Voice used to be a per-assistant switch
   * that also chose the landing screen, which made one setting do two unrelated jobs and
   * left most assistants unable to talk at all for no reason anyone could name. Now
   * every assistant opens the same typing-first screen with a mic in the composer, and
   * the mic is what opens this.
   */
  const [voiceStage, setVoiceStage] = useState(false);
  /**
   * The identity the voice shell speaks with. Memoised because it is a dependency of the
   * session's `start`, and a fresh object every render would rebuild that callback.
   */
  const voicePersona = useMemo(
    () => ({
      displayName,
      customer: meta?.customer,
      industry: meta?.industry,
      // The quick-action labels double as "who asks you what": each one is a persona and
      // a topic in a few words ("Claimant: Claim status").
      topics: (meta?.actions ?? []).map((a) => a.label).filter(Boolean),
    }),
    // `meta.actions` rather than `presets`: the latter is `?? []`, so it is a NEW array on
    // every render, which made this memo re-run every time and rebuild the session's
    // `start` callback with it.
    [displayName, meta?.customer, meta?.industry, meta?.actions],
  );
  const voice = useVoiceSession({
    chat: chatRef,
    workspace: meta?.ls_artifacts?.workspace,
    project: meta?.customer,
    customer: meta?.customer,
    voiceName: (meta?.voice as { voice_name?: string } | undefined)?.voice_name,
    persona: voicePersona,
  });
  const showStage = voiceStage;
  /**
   * `stop` through a ref so the reset below can end a session without depending on the
   * session view - listing `voice` there would re-run the effect on every state change and
   * hang up mid-conversation.
   */
  const voiceStopRef = useRef(voice.stop);
  voiceStopRef.current = voice.stop;
  // Back to the typing-first screen on every assistant switch, so picking an assistant
  // never lands you in a voice view you did not ask for.
  useEffect(() => {
    setVoiceStage(false);
    /**
     * And END any live session, because a session belongs to the assistant it was started
     * for. Its identity and voice are baked into the Live API setup frame, which is sent
     * once and cannot be amended, and its trace is scoped to that customer's project. Left
     * running across a switch, the model keeps introducing itself as the previous company
     * and its turns land in the wrong project.
     *
     * Deliberately not restarted: picking a new assistant should not seize the microphone.
     * The orb comes back in its resting state and is the button to start talking.
     */
    voiceStopRef.current();
  }, [activeAssistant?.assistant_id]);

  // Effective theme: an active assistant's brand theme wins, else the manual
  // preference. Applied to <html> + remembered so a reload restores it.
  const assistantTheme = meta?.theme;
  const effectiveTheme: Theme =
    assistantTheme === "light" || assistantTheme === "dark" ? assistantTheme : globalTheme;
  useEffect(() => {
    applyTheme(effectiveTheme);
    setStoredTheme(effectiveTheme);
    // Token-derived colors (chart series, grid, PDF background) differ per theme,
    // so the resolved-color memo must be dropped when the theme flips.
    invalidateColorCache();
  }, [effectiveTheme]);

  // Header toggle: persist into the active assistant's metadata (so it becomes
  // that brand's default) when one is selected, else flip the manual preference.
  const toggleTheme = () => {
    const next: Theme = effectiveTheme === "dark" ? "light" : "dark";
    if (activeAssistant) settingsRef.current?.setTheme(next);
    else setGlobalTheme(next);
  };

  // Reflect the display name in the browser tab (mirrors applyConfig()).
  useEffect(() => {
    document.title = displayName;
  }, [displayName]);

  // Reset the conversation + dashboard on assistant switch/create.
  const handleResetConversation = () => {
    setWidgets([]);
    setArtifacts({});
    setHasDashboard(false);
    setResetCounter((n) => n + 1);
    // A live voice session restarts with it. Leaving it up would mean the model still
    // remembers a conversation the AGENT no longer has - the thread is gone - so it would
    // reference figures and claims that are no longer anywhere, and keep appending to a
    // trace whose conversation ended. Restarting gives a new thread, a new Live session and
    // a new `voice_session` trace, which is what "new chat" should mean on all three.
    // Stopping also flushes the finished conversation's audio onto its own trace.
    if (voice.running) {
      voice.stop();
      voice.start();
    }
  };

  // Per-run context, resolved fresh at send time from the settings handle.
  const getRunContext = (): RunContext => settingsRef.current?.getRunContext() ?? {};

  // Send guard: block + open settings when a requirement is unmet. Mirrors the
  // SPA's inline guards (assistant → workspace → system prompt), in that order.
  const guard = (): string | null => {
    const guards = settingsRef.current?.getGuards();
    if (!guards) return null;
    if (!guards.hasAssistant) {
      setSettingsOpen(true);
      return "Pick or create an assistant in Settings before sending.";
    }
    if (!guards.hasWorkspace) {
      setSettingsOpen(true);
      return "Pick a Workspace in Settings before sending - trace routing needs an explicit workspace.";
    }
    if (!guards.hasPrompt) {
      setSettingsOpen(true);
      return "A system prompt is required - pick one from the Hub or switch to Prompt and write one (Settings → System prompt).";
    }
    return null;
  };

  const assistantId = activeAssistant?.assistant_id ?? getAssistantId();
  const resetKey = useMemo(
    () => `${activeAssistant?.assistant_id ?? ""}:${resetCounter}`,
    [activeAssistant?.assistant_id, resetCounter],
  );

  return (
    <div className="flex h-screen w-screen flex-col overflow-hidden bg-background text-foreground print:h-auto print:overflow-visible">
      <header className="flex flex-shrink-0 items-center gap-3.5 border-b border-border bg-panel px-5 py-3.5">
        <button
          type="button"
          onClick={handleResetConversation}
          title="Reset the conversation + dashboard"
          className="flex items-center gap-3.5 rounded-lg transition-opacity hover:opacity-70"
        >
          <BrandLogo logo={logo} />
          <h1 className="m-0 font-heading text-2xl font-bold tracking-tight">{displayName}</h1>
        </button>
        {/* One right-anchored action bar. `ml-auto` belongs to the BAR, not to whichever
            button happens to come first, so the row does not slide when a child is
            conditional. The voice control used to live here; it is in the composer now,
            next to send, because talking to the assistant is the same act as sending. */}
        <div className="ml-auto flex items-center gap-3.5 print:hidden">
        {/* No tooltip: this is the one action in the bar with a visible label, so a hover
            card explaining it just covers the row below. The icon-only buttons keep theirs. */}
        <Button
          variant="secondary"
          className="gap-1.5 rounded-full px-4 print:hidden"
          aria-label="New Chat"
          title="Resets the conversation and the dashboard"
          onClick={handleResetConversation}
        >
          <IconSparkles size={16} /> New Chat
        </Button>
        <Tooltip content="Browse the agent's files" side="bottom">
          <Button
            variant="secondary"
            size="icon"
            className="print:hidden"
            aria-label="Browse agent files"
            onClick={() => setFilesOpen(true)}
          >
            <IconFolders size={18} />
          </Button>
        </Tooltip>
        <Tooltip content="Evals - run this assistant's demo dataset in LangSmith" side="bottom">
          <Button
            variant="secondary"
            size="icon"
            className="print:hidden"
            aria-label="Evals"
            onClick={() => setEvalsOpen(true)}
          >
            <IconFlask size={18} />
          </Button>
        </Tooltip>
        <Tooltip
          content={effectiveTheme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
          side="bottom"
        >
          <Button
            variant="secondary"
            size="icon"
            className="print:hidden"
            aria-label="Toggle theme"
            onClick={toggleTheme}
          >
            {effectiveTheme === "dark" ? <IconSun size={18} /> : <IconMoon size={18} />}
          </Button>
        </Tooltip>
        <Tooltip content="Customize appearance" side="bottom">
          <Button
            variant="secondary"
            size="icon"
            className="print:hidden"
            aria-label="Settings"
            onClick={() => setSettingsOpen(true)}
          >
            <IconSettings size={18} />
          </Button>
        </Tooltip>
        </div>
      </header>

      <div
        className={
          "grid min-h-0 flex-1 print:block print:h-auto print:overflow-visible " +
          (resizing ? "cursor-col-resize select-none " : "transition-[grid-template-columns] duration-500 ease-in-out ")
        }
        style={{ gridTemplateColumns: showPane ? `${chatWidth}px 1fr` : "1fr" }}
      >
        {/* Chat pane — centered on load, becomes the left rail once a dashboard exists.
            Hidden when printing a dashboard (Ctrl+P exports the dashboard only). */}
        <section
          className={
            "relative flex min-h-0 flex-col " +
            (showPane
              ? "border-r border-border print:hidden"
              : "mx-auto w-full max-w-[760px]")
          }
        >
          {/* The orb, over the chat rail. ChatPanel stays MOUNTED underneath rather than
              being swapped out: it owns the run machinery and the widget flushing, so
              unmounting it would take the dashboard with it (and drop the session's
              `ask` handle mid-turn). */}
          {showStage && (
            <div className="absolute inset-0 z-20 bg-background">
              <VoiceStage
                voice={voice}
                // Leaving the orb HANGS UP. Orb and listening are one thing: the alternative
                // made "running, but not on the orb" a reachable state, which is why the
                // composer needed its own stop button next to the mic - two controls for one
                // conversation, and a live microphone with nothing on screen saying so.
                onExit={() => {
                  voice.stop();
                  setVoiceStage(false);
                }}
                displayName={displayName}
                logo={logo}
                presets={presets}
              />
            </div>
          )}
          <ChatPanel
            handleRef={chatRef}
            voiceControl={
              showStage ? undefined : (
                <VoiceButton voice={voice} onOpen={() => setVoiceStage(true)} />
              )
            }
            assistantId={assistantId}
            /* Same sandbox key the Files dialog and the agent itself use, so a file
               dropped on the chat lands in the VM this assistant reads from. */
            sandboxTarget={{
              agent_repo: activeAssistant?.metadata?.ls_artifacts?.agent_repo || undefined,
              customer: activeAssistant?.metadata?.customer || undefined,
            }}
            presets={presets}
            getRunContext={getRunContext}
            onActivity={setActivity}
            onWidget={(w) => {
              setWidgets((prev) => [...prev, w]);
              setHasDashboard(true);
            }}
            onArtifact={({ path, content, streaming }) => {
              setHasDashboard(true);
              setArtifacts((prev) => ({
                ...prev,
                // A finishing write sends no content: keep whatever streamed, so the
                // tab holds its last good render until the canonical read lands.
                [path]: {
                  content: content || prev[path]?.content || "",
                  streaming,
                },
              }));
              if (streaming) return;
              // The write completed. Re-read the file, because what streamed is the
              // TOOL ARGUMENT, and for edit_file that is a diff rather than the
              // document. Best effort: with no sandbox (DA_SANDBOX=0, no entitlement)
              // the file lives in graph state and this 503s, leaving the streamed
              // content in place, which is the right fallback for write_file.
              readSandboxTextFile(
                {
                  agent_repo: activeAssistant?.metadata?.ls_artifacts?.agent_repo || undefined,
                  customer: activeAssistant?.metadata?.customer || undefined,
                },
                path,
              )
                .then((text) => {
                  if (text === null) return;
                  setArtifacts((prev) => ({ ...prev, [path]: { content: text, streaming: false } }));
                })
                .catch(() => {
                  /* keep the streamed content */
                });
            }}
            guard={guard}
            resetKey={resetKey}
            logo={logo}
            industry={meta?.industry}
            hasAssistant={!!activeAssistant}
            onOpenSettings={() => setSettingsOpen(true)}
          />
          {/* Drag handle to resize the rail (only when the split is shown). */}
          {showPane && (
            <div
              onPointerDown={startResize}
              title="Drag to resize"
              className="absolute top-0 -right-[3px] z-20 h-full w-1.5 cursor-col-resize transition-colors hover:bg-[var(--brand-primary)]/40 print:hidden"
            />
          )}
        </section>

        {/* Right pane — mounted once there is a dashboard, an artifact, or tool work
            to graph. */}
        {showPane && (
          <section className="flex min-h-0 flex-col overflow-hidden print:overflow-visible">
            <DashboardPane
              widgets={widgets}
              theme={effectiveTheme}
              artifacts={artifacts}
              activity={activity}
              assistantName={displayName}
            />
          </section>
        )}
      </div>

      <FileBrowser
        open={filesOpen}
        onOpenChange={setFilesOpen}
        assistant={activeAssistant}
      />

      <EvalPanel
        open={evalsOpen}
        onOpenChange={setEvalsOpen}
        assistant={activeAssistant}
      />

      <SettingsPanel
        ref={settingsRef}
        open={settingsOpen}
        onOpenChange={setSettingsOpen}
        onActiveAssistantChange={setActiveAssistant}
        onResetConversation={handleResetConversation}
      />
    </div>
  );
}
