/** Settings is an editor for the app-owned assistant session, plus presentation dialogs. */
import { useCallback, useEffect, useRef, useState } from "react";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { useAgents, useTools } from "@/lib/queries";
import { LAST_OWNER_LS_KEY, readSessionPreference } from "@/lib/assistantSession";
import type { AssistantSession } from "@/lib/hooks/useAssistantSession";
import type { ToolSpec } from "@/lib/api";
import { WorkspaceSelect } from "./settings/WorkspaceSelect";
import { AssistantSelect } from "./settings/AssistantSelect";
import { NewAssistantDialog, type NewAssistantValues } from "./settings/NewAssistantDialog";
import { DemoBriefDialog, type DemoBrief } from "./settings/DemoBriefDialog";
import { VisualSection } from "./settings/VisualSection";
import { BrandSection } from "./settings/BrandSection";
import { TypographySection } from "./settings/TypographySection";
import { AgentConfig } from "./settings/AgentConfig";
import { ToolsSection } from "./settings/ToolsSection";
import { McpSection } from "./settings/McpSection";
import { DeleteAssistant } from "./settings/DeleteAssistant";
import { DemoTraffic } from "./settings/DemoTraffic";
import { VoicePicker } from "./settings/VoicePicker";

interface SettingsPanelProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  session: AssistantSession;
}

const EMPTY_NAMES: string[] = [];
const EMPTY_TOOLS: ToolSpec[] = [];

function errMsg(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function SettingsPanel({ open, onOpenChange, session }: SettingsPanelProps) {
  const {
    draft: cfg, selectedId, selectedAssistant, visibleAssistants, workspaces, organization,
    workspaceReset, fontStatus, editBranding, previewPrompt, editModel, editTools, editMcpServers,
  } = session;
  const agents = useAgents(cfg.lsWorkspace).data ?? EMPTY_NAMES;
  const toolSpecs = useTools().data ?? EMPTY_TOOLS;
  const [showNewForm, setShowNewForm] = useState(false);
  const [creating, setCreating] = useState(false);
  const [demoBrief, setDemoBrief] = useState<DemoBrief | null>(null);
  const onboardingCheckedRef = useRef(false);
  const [panelWidth, setPanelWidth] = useState(() => {
    try {
      const width = Number(localStorage.getItem("settingsPanelWidth"));
      return width >= 320 && width <= 760 ? width : 380;
    } catch {
      return 380;
    }
  });
  const [resizing, setResizing] = useState(false);

  const startResize = useCallback((event: React.PointerEvent) => {
    event.preventDefault();
    setResizing(true);
    const onMove = (ev: PointerEvent) => {
      setPanelWidth(Math.min(Math.max(window.innerWidth - ev.clientX, 320), Math.min(760, window.innerWidth - 120)));
    };
    const onUp = () => {
      setResizing(false);
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  }, []);

  useEffect(() => {
    if (resizing) return;
    try {
      localStorage.setItem("settingsPanelWidth", String(panelWidth));
    } catch {
      // Resizing remains available without browser persistence.
    }
  }, [resizing, panelWidth]);

  useEffect(() => {
    if (workspaceReset) setShowNewForm(true);
  }, [workspaceReset]);

  useEffect(() => {
    if (session.assistantsPending || onboardingCheckedRef.current) return;
    onboardingCheckedRef.current = true;
    if (!readSessionPreference(LAST_OWNER_LS_KEY)) setShowNewForm(true);
  }, [session.assistantsPending]);

  async function handleCreate(values: NewAssistantValues) {
    const workspace = values.workspace || cfg.lsWorkspace;
    if (!values.customer) {
      window.alert("Customer is required - it's used as the assistant name.");
      return;
    }
    if (!workspace) {
      window.alert("Pick a Workspace first (top of the panel) - setup needs it.");
      return;
    }
    if (values.workspace) session.selectWorkspace(values.workspace);
    setCreating(true);
    try {
      const { metadata } = await session.create({
        workspace, customer: values.customer, owner: values.owner, website: values.website,
        use_case: values.useCase, failure_mode: values.failureMode,
        push_prompts: true, demo_traffic: values.demoTraffic,
      });
      const brief = metadata.demo_brief || [];
      const flow = metadata.demo_flow || [];
      if (brief.length || flow.length) {
        onOpenChange(false);
        setDemoBrief({ customer: values.customer, brief, flow });
      }
      setShowNewForm(false);
    } catch (error) {
      window.alert("Setup failed: " + errMsg(error));
    } finally {
      setCreating(false);
    }
  }

  async function handleDelete() {
    try {
      await session.remove((failed) => window.alert(
        "Some LangSmith artifacts could not be deleted:\n" +
        failed.map((f) => `- ${f.artifact}: ${f.error}`).join("\n"),
      ));
    } catch (error) {
      window.alert("Delete failed: " + errMsg(error));
    }
  }

  const deleteLabel = selectedAssistant?.metadata?.display_name || selectedAssistant?.name || selectedId;
  return (
    <>
      <DemoBriefDialog brief={demoBrief} onClose={() => setDemoBrief(null)} />
      {showNewForm && (
        <NewAssistantDialog
          initialOwner={readSessionPreference(LAST_OWNER_LS_KEY)}
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
          <div
            onPointerDown={startResize}
            title="Drag to resize"
            className="absolute top-0 left-0 z-20 h-full w-1.5 cursor-col-resize transition-colors hover:bg-[var(--brand-primary)]/40"
          />
          <SheetHeader className="p-4 pb-2">
            <SheetTitle>Customize</SheetTitle>
          </SheetHeader>
          <div className="flex min-h-0 flex-1 flex-col gap-3.5 overflow-y-auto p-4 pt-0">
            <WorkspaceSelect
              value={cfg.lsWorkspace}
              workspaces={workspaces}
              organization={organization}
              onChange={session.selectWorkspace}
            />
            <div className="border-t border-border" />
            <AssistantSelect
              value={selectedId}
              assistants={visibleAssistants}
              onChange={(id) => { setShowNewForm(false); session.selectAssistant(id); }}
              onNewClick={() => setShowNewForm((shown) => !shown)}
            />
            {session.guards.hasAssistant && (
              <>
                <VisualSection
                  name={cfg.name}
                  logo={cfg.logo}
                  actions={cfg.actions}
                  theme={cfg.theme}
                  onName={(v) => editBranding({ name: v })}
                  onLogo={(v) => editBranding({ logo: v })}
                  onActions={(actions) => editBranding({ actions })}
                  onTheme={(theme) => editBranding({ theme })}
                />
                <BrandSection
                  accent={cfg.accent}
                  accent2={cfg.accent2}
                  neutral={cfg.brandNeutral}
                  tint={cfg.brandTint}
                  onAccent={(accent) => editBranding({ accent })}
                  onAccent2={(accent2) => editBranding({ accent2 })}
                  onNeutral={(brandNeutral) => editBranding({ brandNeutral })}
                  onTint={(brandTint) => editBranding({ brandTint })}
                >
                  <VoicePicker value={cfg.voiceName} onChange={(voiceName) => editBranding({ voiceName })} />
                  <TypographySection
                    headingFont={cfg.fontHeading}
                    headingFallback={cfg.fontHeadingFallback}
                    bodyFont={cfg.fontBody}
                    bodyFallback={cfg.fontBodyFallback}
                    useGoogle={cfg.fontSource === "google"}
                    status={fontStatus}
                    onHeadingFont={(fontHeading) => editBranding({ fontHeading })}
                    onHeadingFallback={(fontHeadingFallback) => editBranding({ fontHeadingFallback })}
                    onBodyFont={(fontBody) => editBranding({ fontBody })}
                    onBodyFallback={(fontBodyFallback) => editBranding({ fontBodyFallback })}
                    onUseGoogle={(v) => editBranding({ fontSource: v ? "google" : "curated" })}
                  />
                </BrandSection>
                <AgentConfig
                  agentRepo={cfg.agentRepo}
                  model={cfg.model}
                  agents={agents}
                  onAgentRepo={previewPrompt}
                  onModel={editModel}
                />
                <ToolsSection specs={toolSpecs} enabled={cfg.enabledTools} onChange={editTools} />
                <McpSection servers={cfg.mcpServers} onChange={editMcpServers} />
                <DemoTraffic
                  target={selectedAssistant ? {
                    project: (selectedAssistant.context?.ls_project as string) || selectedAssistant.metadata?.customer || selectedAssistant.name || "",
                    workspace: selectedAssistant.context?.ls_workspace as string,
                    context: selectedAssistant.context,
                    actions: selectedAssistant.metadata?.actions,
                    data_gap: selectedAssistant.context?.data_gap as string,
                    customer: selectedAssistant.metadata?.customer,
                  } : null}
                />
                <DeleteAssistant label={deleteLabel} onDelete={handleDelete} />
              </>
            )}
          </div>
        </SheetContent>
      </Sheet>
    </>
  );
}
