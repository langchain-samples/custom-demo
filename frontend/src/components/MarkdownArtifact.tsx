/**
 * One Markdown document, edited as a document, with its revision history.
 *
 *   ┌ brief.md ──────────────── ⟲ History │ Edit ┐
 *   │  rendered markdown, selectable              │
 *   │    ┌ Ask about this ┐  ← on a selection     │
 *   └─────────────────────────────────────────────┘
 *
 * Editing is a real WYSIWYG surface (Milkdown/Crepe), not a source pane. That matters
 * for who this is for: the people who raise and review these documents are product
 * owners and business analysts, and asking them to type `##` is asking them to learn a
 * markup language to do their job. Markdown stays the storage format all the way to the
 * agent that reads the document next, which is why the editor is markdown-native
 * (ProseMirror over remark) rather than rich text with a converter bolted on: a round
 * trip through a foreign document model is where headings and Gherkin fences get
 * quietly reformatted.
 *
 * Saving is only possible when the assistant stores documents in Context Hub. An
 * assistant whose artifacts live on its sandbox VM has no write route and no revision
 * history, and that is said on screen rather than shown as a disabled button with no
 * explanation: the document is still perfectly readable, only not editable here.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  IconAlertTriangle,
  IconArrowBackUp,
  IconDeviceFloppy,
  IconHistory,
  IconMessagePlus,
  IconPencil,
  IconX,
} from "@tabler/icons-react";
import { Streamdown } from "streamdown";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  DocsError,
  listDocVersions,
  readAgentDoc,
  saveAgentDoc,
  type AgentDoc,
  type DocsTarget,
  type DocVersion,
} from "@/lib/api";
import { artifactName } from "@/lib/artifacts";
import { LAST_OWNER_LS_KEY, readSessionPreference } from "@/lib/assistantSession";
import type { MarkdownEditor } from "@/lib/markdownEditor";
import { cn } from "@/lib/utils";

export interface MarkdownArtifactProps {
  path: string;
  /** What streamed from the agent's write. The canonical text is re-read from the store. */
  content: string;
  streaming: boolean;
  target: DocsTarget;
  /**
   * Who a save is recorded as, when the caller knows. Nothing in this deployment does:
   * authentication is one shared token and there is no signed-in user, so the name comes
   * from what this browser already knows and the save bar asks for the role. Naming that
   * gap where the save happens is the point - a history full of unattributed revisions
   * would be useless for a review chain, and a made-up name would be worse than useless.
   */
  author?: string;
  /** Hand a selected passage to the chat composer as context for the next question. */
  onAskAbout?: (excerpt: string, path: string) => void;
}

/**
 * Roles a reviewer saves as, and where the choice is remembered.
 *
 * A stand-in for the signed-in user this deployment does not have. Per browser, because
 * that is the only scope available: it is a label on a revision, never a permission.
 */
const ROLES = [
  "Product Owner",
  "Product Manager",
  "Business Analyst",
  "Architect",
  "UX Designer",
  "QA",
  "Reviewer",
];
const ROLE_KEY = "documentReviewerRole";

/** The role this browser last saved as, defaulting to the most general one. */
function storedRole(): string {
  try {
    return window.localStorage.getItem(ROLE_KEY) || "Reviewer";
  } catch {
    // Private windows and blocked site data both throw here, and a role is a nicety.
    return "Reviewer";
  }
}

/** When a revision carries no message, say when it happened rather than nothing. */
function versionLabel(version: DocVersion): string {
  if (version.message) return version.message;
  if (!version.created_at) return "Saved";
  const when = new Date(version.created_at);
  return Number.isNaN(when.getTime()) ? "Saved" : `Saved ${when.toLocaleString()}`;
}

/**
 * Who a revision is credited to.
 *
 * An agent write reaches the store through the filesystem mount rather than the save
 * route, so it records neither, and says so plainly instead of borrowing the last
 * person's name.
 */
function versionWho(version: DocVersion): string {
  const parts = [version.author, version.role].filter(Boolean);
  return parts.length ? parts.join(" · ") : "written by the assistant";
}

/** Short, stable revision id for the history list. */
function shortVersion(version: string): string {
  return version ? version.slice(0, 7) : "";
}

export function MarkdownArtifact({
  path,
  content,
  streaming,
  target,
  author,
  onAskAbout,
}: MarkdownArtifactProps) {
  const editable = !!target.docs_repo;
  const [stored, setStored] = useState<AgentDoc | null>(null);
  const [loadError, setLoadError] = useState("");
  const [editing, setEditing] = useState(false);
  const [message, setMessage] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<DocsError | Error | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [versions, setVersions] = useState<DocVersion[]>([]);
  const [viewing, setViewing] = useState<DocVersion | null>(null);
  const [viewingText, setViewingText] = useState("");
  const [selection, setSelection] = useState<{ text: string; top: number; left: number } | null>(
    null,
  );
  const [role, setRole] = useState<string>(storedRole);
  const readRef = useRef<HTMLDivElement>(null);
  const editorHost = useRef<HTMLDivElement>(null);
  const crepe = useRef<MarkdownEditor | null>(null);
  /** The text the editor opened with, so Save can tell whether anything changed. */
  const opened = useRef("");

  /** The person this browser knows about, when the caller did not supply one. */
  const person = useMemo(
    () => author || readSessionPreference(LAST_OWNER_LS_KEY),
    [author],
  );

  /**
   * Re-read the canonical document once a write settles.
   *
   * What streamed is the write tool's ARGUMENT, and for an edit that is a patch rather
   * than the document, so the store is the only place the finished text exists. Keyed
   * on `streaming` falling as well as on the path so the second of two consecutive
   * writes also refreshes.
   */
  useEffect(() => {
    if (streaming || !editable) return;
    let live = true;
    readAgentDoc(target, path)
      .then((doc) => {
        if (!live) return;
        setStored(doc);
        setLoadError("");
      })
      .catch((e: unknown) => {
        if (!live) return;
        // A document the agent just wrote can be absent for one beat; anything else is
        // worth saying, because the text on screen is then only what streamed.
        setLoadError(e instanceof Error ? e.message : String(e));
      });
    return () => {
      live = false;
    };
  }, [path, streaming, editable, target]);

  // Leaving the document (a tab switch remounts, but the path can also change under a
  // stable mount) must not carry one document's draft into another.
  useEffect(() => {
    setEditing(false);
    setHistoryOpen(false);
    setViewing(null);
    setSaveError(null);
    setSelection(null);
  }, [path]);

  const text = stored?.content ?? content;

  /**
   * Mount the editor for the current text, and tear it down on the way out.
   *
   * Keyed on `editing` and the document's revision, NOT on the text: Crepe owns its own
   * document once created, and re-creating it on every keystroke would move the caret to
   * the start and lose the selection. A revision arriving from elsewhere is a different
   * document and does warrant a fresh editor.
   */
  useEffect(() => {
    if (!editing || !editorHost.current) return;
    const host = editorHost.current;
    const openedWith = text;
    let live = true;
    let instance: MarkdownEditor | null = null;
    // Imported here rather than at the top of the file: the editor and its stylesheets
    // are a large chunk that only an editing session needs.
    void (async () => {
      try {
        const { Crepe } = await import("@/lib/markdownEditor");
        if (!live) return;
        instance = new Crepe({ root: host, defaultValue: openedWith });
        crepe.current = instance;
        opened.current = openedWith;
        await instance.create();
      } catch (e: unknown) {
        if (!live) return;
        setSaveError(new Error(`The editor could not open this document: ${String(e)}`));
      }
    })();
    return () => {
      live = false;
      crepe.current = null;
      // Destroy is async and the node is already going; nothing waits on it.
      void instance?.destroy();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editing, stored?.version]);

  const loadVersions = useCallback(() => {
    listDocVersions(target, path).then(setVersions);
  }, [target, path]);

  const openHistory = () => {
    setHistoryOpen((open) => !open);
    if (!historyOpen) loadVersions();
  };

  const startEditing = () => {
    setMessage("");
    setSaveError(null);
    setEditing(true);
    setViewing(null);
  };

  const save = async () => {
    const edited = crepe.current?.getMarkdown() ?? "";
    if (!edited) {
      setSaveError(new Error("The editor has no content to save."));
      return;
    }
    setSaving(true);
    setSaveError(null);
    try {
      const saved = await saveAgentDoc(target, {
        path,
        content: edited,
        message,
        author: person,
        role,
        // Omitted while the store has never been read: there is no revision to be
        // stale against, and sending "" would read as "I looked at nothing".
        baseVersion: stored?.version,
      });
      setStored({
        path,
        content: edited,
        version: saved.version,
        message: saved.message,
        author: person,
        role,
      });
      setEditing(false);
      setMessage("");
      if (historyOpen) loadVersions();
    } catch (e: unknown) {
      setSaveError(e instanceof Error ? e : new Error(String(e)));
    } finally {
      setSaving(false);
    }
  };

  /** Re-read after a refused save, so the reader sees the revision that beat theirs. */
  const reload = async () => {
    try {
      const doc = await readAgentDoc(target, path);
      setStored(doc);
      setSaveError(null);
      loadVersions();
    } catch (e: unknown) {
      setSaveError(e instanceof Error ? e : new Error(String(e)));
    }
  };

  const openVersion = async (version: DocVersion) => {
    setViewing(version);
    setEditing(false);
    try {
      const doc = await readAgentDoc(target, path, version.version);
      setViewingText(doc.content);
    } catch {
      setViewingText("");
    }
  };

  /**
   * Put an old revision back by SAVING it as a new one.
   *
   * Never by rewriting history: the point of the record is that the reviewer who wrote
   * the discarded version can still find it, and a restore that erased the revision it
   * replaced would make the history a worse account of the document than no history.
   */
  const restore = async () => {
    if (!viewing) return;
    const restoring = viewingText;
    const note = `Restored the revision from ${shortVersion(viewing.version)}`;
    setViewing(null);
    setSaving(true);
    setSaveError(null);
    try {
      const saved = await saveAgentDoc(target, {
        path,
        content: restoring,
        message: note,
        author: person,
        role,
        baseVersion: stored?.version,
      });
      setStored({
        path,
        content: restoring,
        version: saved.version,
        message: note,
        author: person,
        role,
      });
      loadVersions();
    } catch (e: unknown) {
      setSaveError(e instanceof Error ? e : new Error(String(e)));
    } finally {
      setSaving(false);
    }
  };

  /** Track a selection in the rendered document, for the "Ask about this" action. */
  const onSelectionChange = () => {
    if (!onAskAbout) return;
    const sel = window.getSelection();
    const host = readRef.current;
    if (!sel || sel.isCollapsed || !host) {
      setSelection(null);
      return;
    }
    const chosen = sel.toString().trim();
    if (!chosen || !host.contains(sel.anchorNode)) {
      setSelection(null);
      return;
    }
    const rect = sel.getRangeAt(0).getBoundingClientRect();
    const box = host.getBoundingClientRect();
    const above = rect.top - box.top + host.scrollTop - 38;
    setSelection({
      text: chosen,
      // Above the selection where there is room, below it at the very top of the pane.
      top: above < 0 ? rect.bottom - box.top + host.scrollTop + 8 : above,
      left: Math.max(0, rect.left - box.left),
    });
  };

  const askAbout = () => {
    if (!selection || !onAskAbout) return;
    onAskAbout(selection.text, path);
    window.getSelection()?.removeAllRanges();
    setSelection(null);
  };

  const conflict = saveError instanceof DocsError && saveError.reason === "conflict";
  const shown = viewing ? viewingText : text;
  const revisionNote = useMemo(() => {
    if (!stored?.message) return "";
    const who = [stored.author, stored.role].filter(Boolean).join(" · ");
    return who ? `${who}: ${stored.message}` : stored.message;
  }, [stored]);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* Header: what this document is, and the two things you can do to it. */}
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-2">
        <span className="truncate text-sm font-medium" title={path}>
          {artifactName(path)}
        </span>
        {viewing && (
          <span className="rounded bg-panel-2 px-1.5 py-0.5 text-[11px] text-muted-foreground">
            revision {shortVersion(viewing.version)}
          </span>
        )}
        {!viewing && revisionNote && (
          <span className="truncate text-[11px] text-muted-foreground" title={revisionNote}>
            {revisionNote}
          </span>
        )}
        <div className="ml-auto flex items-center gap-1">
          {editable && (
            <Button variant="ghost" size="sm" onClick={openHistory} title="Revision history">
              <IconHistory className="size-4" />
              History
            </Button>
          )}
          {editable && !editing && !viewing && (
            <Button variant="ghost" size="sm" onClick={startEditing} disabled={streaming}>
              <IconPencil className="size-4" />
              Edit
            </Button>
          )}
          {viewing && (
            <>
              <Button variant="ghost" size="sm" onClick={restore} disabled={saving}>
                <IconArrowBackUp className="size-4" />
                Restore this revision
              </Button>
              <Button variant="ghost" size="sm" onClick={() => setViewing(null)}>
                <IconX className="size-4" />
                Back to current
              </Button>
            </>
          )}
        </div>
      </div>

      {/* Why editing is unavailable, when it is. Said once, at the top, rather than as a
          silently disabled control. */}
      {!editable && (
        <p className="border-b border-border bg-panel-2 px-4 py-2 text-[12px] text-muted-foreground">
          This assistant keeps its artifacts on its sandbox VM, so this document has no
          revision history and edits cannot be saved from here.
        </p>
      )}
      {loadError && (
        <p className="flex items-start gap-2 border-b border-border bg-panel-2 px-4 py-2 text-[12px] text-muted-foreground">
          <IconAlertTriangle className="mt-0.5 size-3.5 flex-shrink-0" />
          <span>
            Showing what was written in this turn. The stored copy could not be read: {loadError}
          </span>
        </p>
      )}

      <div className="flex min-h-0 flex-1">
        {/* History rail. Opens beside the document rather than over it, so a revision
            can be compared against what is on screen. */}
        {historyOpen && editable && (
          <aside className="w-64 flex-shrink-0 overflow-y-auto border-r border-border">
            <p className="px-3 py-2 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
              Revisions
            </p>
            {versions.length === 0 && (
              <p className="px-3 pb-3 text-[12px] text-muted-foreground">No saved revisions yet.</p>
            )}
            <ul className="flex flex-col">
              {versions.map((version) => (
                <li key={version.version}>
                  <button
                    type="button"
                    onClick={() => openVersion(version)}
                    className={cn(
                      "flex w-full flex-col gap-0.5 border-l-2 px-3 py-2 text-left hover:bg-panel-2",
                      viewing?.version === version.version
                        ? "border-l-[var(--brand-primary)] bg-panel-2"
                        : "border-l-transparent",
                    )}
                  >
                    <span className="text-[12px] leading-snug">{versionLabel(version)}</span>
                    <span className="text-[11px] text-muted-foreground">
                      {versionWho(version)} · {shortVersion(version.version)}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </aside>
        )}

        {editing ? (
          <div className="flex min-h-0 flex-1 flex-col">
            {/* The editor owns its own toolbars: a selection raises an inline one, and a
                new line offers a block menu. So there is no toolbar of ours here, and
                nothing tells the reader they are editing markup. */}
            <div ref={editorHost} className="min-h-0 flex-1 overflow-y-auto" />
            {/* Save bar. The message is the whole reason the history is readable later,
                so it sits in the primary path rather than behind a dialog. */}
            <div className="flex flex-wrap items-center gap-2 border-t border-border px-3 py-2">
              <Input
                id="doc-save-message"
                value={message}
                onChange={(e) => setMessage(e.target.value)}
                placeholder="What changed? (shows up in the history)"
                className="h-8 min-w-48 flex-1 text-[13px]"
              />
              <span className="text-[12px] text-muted-foreground">
                {person ? `${person} as` : "Saving as"}
              </span>
              <select
                id="doc-save-role"
                value={role}
                onChange={(e) => {
                  setRole(e.target.value);
                  try {
                    window.localStorage.setItem(ROLE_KEY, e.target.value);
                  } catch {
                    /* a role that cannot be remembered still labels this save */
                  }
                }}
                title="The role this revision is recorded as"
                className="h-8 rounded-md border border-border bg-transparent px-2 text-[12px]"
              >
                {ROLES.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
              <Button variant="ghost" size="sm" onClick={() => setEditing(false)} disabled={saving}>
                Cancel
              </Button>
              <Button size="sm" onClick={save} disabled={saving}>
                <IconDeviceFloppy className="size-4" />
                {saving ? "Saving" : "Save revision"}
              </Button>
            </div>
            {saveError && (
              <div className="flex flex-wrap items-center gap-2 border-t border-border bg-panel-2 px-3 py-2 text-[12px]">
                <IconAlertTriangle className="size-3.5 flex-shrink-0" />
                <span className="flex-1">{saveError.message}</span>
                {conflict && (
                  <Button variant="secondary" size="sm" onClick={reload}>
                    Load the current revision
                  </Button>
                )}
              </div>
            )}
          </div>
        ) : (
          <div
            ref={readRef}
            onMouseUp={onSelectionChange}
            onKeyUp={onSelectionChange}
            className="relative min-h-0 flex-1 overflow-y-auto p-6"
          >
            <Streamdown parseIncompleteMarkdown>{shown}</Streamdown>
            {selection && (
              <div className="absolute z-10" style={{ top: selection.top, left: selection.left }}>
                <Button size="sm" onClick={askAbout} className="shadow-md">
                  <IconMessagePlus className="size-4" />
                  Ask about this
                </Button>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
