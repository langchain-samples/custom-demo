/**
 * App shell — wires the ported panes into the working two-pane layout:
 *
 *   header (large logo + display name from the active assistant's branding, gear)
 *   ┌─────────────── before any dashboard: single centered chat column ──────────┐
 *   │  ChatPanel                                                                  │
 *   └─────────────────────────────────────────────────────────────────────────── ┘
 *   ┌── once widgets exist: two columns (420px rail + 1fr) ──────────────────────┐
 *   │  ChatPanel  │  DashboardCanvas                                              │
 *   └─────────────────────────────────────────────────────────────────────────── ┘
 *
 * The gear opens the SettingsPanel Sheet. App owns the streamed-widget list, the
 * active-assistant branding, the send-guards (delegated to the settings handle),
 * and the conversation-reset key (bumped on assistant switch/create).
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { IconSparkles, IconSun, IconMoon, IconSettings, IconRobot } from "@tabler/icons-react";
import { Button } from "@/components/ui/button";
import ChatPanel from "@/components/ChatPanel";
import { DashboardCanvas } from "@/components/DashboardCanvas";
import { SettingsPanel, type SettingsHandle } from "@/components/SettingsPanel";
import { getAssistantId } from "@/lib/config";
import { applyTheme, getStoredTheme, setStoredTheme, type Theme } from "@/lib/theme";
import { invalidateColorCache } from "@/lib/branding";
import type { Assistant, RunContext, Widget } from "@/lib/api";

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
  const [activeAssistant, setActiveAssistant] = useState<Assistant | null>(null);
  const [widgets, setWidgets] = useState<Widget[]>([]);
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

  // Branding for the header + chat presets (falls back to the app defaults).
  const meta = activeAssistant?.metadata;
  const displayName = meta?.display_name || DEFAULT_NAME;
  const logo = meta?.logo || DEFAULT_LOGO;
  const presets = meta?.actions ?? [];

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
    setHasDashboard(false);
    setResetCounter((n) => n + 1);
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
      return "Pick a Workspace in Settings before sending — trace routing needs an explicit workspace.";
    }
    if (!guards.hasPrompt) {
      setSettingsOpen(true);
      return "A system prompt is required — pick one from the Hub or switch to Prompt and write one (Settings → System prompt).";
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
        <Button
          variant="secondary"
          className="ml-auto gap-1.5 rounded-full px-4 print:hidden"
          title="Start a new chat (reset the conversation + dashboard)"
          onClick={handleResetConversation}
        >
          <IconSparkles size={16} /> New Chat
        </Button>
        <Button
          variant="secondary"
          size="icon"
          className="print:hidden"
          title={effectiveTheme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
          aria-label="Toggle theme"
          onClick={toggleTheme}
        >
          {effectiveTheme === "dark" ? <IconSun size={18} /> : <IconMoon size={18} />}
        </Button>
        <Button
          variant="secondary"
          size="icon"
          className="print:hidden"
          title="Customize appearance"
          aria-label="Settings"
          onClick={() => setSettingsOpen(true)}
        >
          <IconSettings size={18} />
        </Button>
      </header>

      <div
        className={
          "grid min-h-0 flex-1 print:block print:h-auto print:overflow-visible " +
          (resizing ? "cursor-col-resize select-none " : "transition-[grid-template-columns] duration-500 ease-in-out ")
        }
        style={{ gridTemplateColumns: hasDashboard ? `${chatWidth}px 1fr` : "1fr" }}
      >
        {/* Chat pane — centered on load, becomes the left rail once a dashboard exists.
            Hidden when printing a dashboard (Ctrl+P exports the dashboard only). */}
        <section
          className={
            "relative flex min-h-0 flex-col " +
            (hasDashboard
              ? "border-r border-border print:hidden"
              : "mx-auto w-full max-w-[760px]")
          }
        >
          <ChatPanel
            assistantId={assistantId}
            presets={presets}
            getRunContext={getRunContext}
            onWidget={(w) => {
              setWidgets((prev) => [...prev, w]);
              setHasDashboard(true);
            }}
            guard={guard}
            resetKey={resetKey}
            logo={logo}
            industry={meta?.industry}
            hasAssistant={!!activeAssistant}
            onOpenSettings={() => setSettingsOpen(true)}
          />
          {/* Drag handle to resize the rail (only when the dashboard split is shown). */}
          {hasDashboard && (
            <div
              onPointerDown={startResize}
              title="Drag to resize"
              className="absolute top-0 -right-[3px] z-20 h-full w-1.5 cursor-col-resize transition-colors hover:bg-[var(--brand-primary)]/40 print:hidden"
            />
          )}
        </section>

        {/* Live dashboard pane — mounted once a dashboard has appeared. */}
        {hasDashboard && (
          <section className="flex min-h-0 flex-col overflow-hidden print:overflow-visible">
            <DashboardCanvas widgets={widgets} theme={effectiveTheme} />
          </section>
        )}
      </div>

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
