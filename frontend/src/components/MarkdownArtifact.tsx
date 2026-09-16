/**
 * One Markdown document, read and edited in place, with its revision history.
 *
 *   ┌ brief.md ──────────────── ⟲ History │ Edit ┐
 *   │  rendered markdown, selectable              │
 *   │    ┌ Ask about this ┐  ← on a selection     │
 *   └─────────────────────────────────────────────┘
 *
 * Three modes, one document: reading it, editing its source, and looking back at a
 * revision. Editing is deliberately the source rather than a rich-text surface over it,
 * because the document IS Markdown all the way through to the agent that reads it next,
 * and a round trip through a rich-text model is where headings and Gherkin blocks get
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
  IconBold,
  IconCode,
  IconDeviceFloppy,
  IconEye,
  IconHistory,
  IconItalic,
  IconLink,
  IconList,
  IconMessagePlus,
  IconPencil,
  IconX,
} from "@tabler/icons-react";
import { Streamdown } from "streamdown";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
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
import { cn } from "@/lib/utils";

export interface MarkdownArtifactProps {
  path: string;
  /** What streamed from the agent's write. The canonical text is re-read from the store. */
  content: string;
  streaming: boolean;
  target: DocsTarget;
  /**
   * Who a save is recorded as, when the caller knows. Nothing in this deployment does:
   * authentication is one shared token and there is no signed-in user, so the save bar
   * asks for a role instead of inventing an author. Naming that gap where the save
   * happens is the point - a history full of unattributed revisions would be useless
   * for a review chain, and a made-up name would be worse than useless.
   */
  author?: string;
  /** Hand a selected passage to the chat composer as context for the next question. */
  onAskAbout?: (excerpt: string, path: string) => void;
}

/** A source edit applied to the selection, as the toolbar buttons perform it. */
interface Wrap {
  before: string;
  after?: string;
  /** Applied to the start of every selected line instead of around the selection. */
  linePrefix?: boolean;
  placeholder: string;
}

const WRAPS: { icon: typeof IconBold; title: string; wrap: Wrap }[] = [
  { icon: IconBold, title: "Bold", wrap: { before: "**", after: "**", placeholder: "bold text" } },
  { icon: IconItalic, title: "Italic", wrap: { before: "_", after: "_", placeholder: "italic" } },
  {
    icon: IconList,
    title: "Bulleted list",
    wrap: { before: "- ", linePrefix: true, placeholder: "list item" },
  },
  {
    icon: IconCode,
    title: "Code",
    wrap: { before: "`", after: "`", placeholder: "code" },
  },
  {
    icon: IconLink,
    title: "Link",
    wrap: { before: "[", after: "](https://)", placeholder: "link text" },
  },
];

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
  const [buffer, setBuffer] = useState("");
  const [message, setMessage] = useState("");
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<DocsError | Error | null>(null);
  const [preview, setPreview] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [versions, setVersions] = useState<DocVersion[]>([]);
  const [viewing, setViewing] = useState<DocVersion | null>(null);
  const [viewingText, setViewingText] = useState("");
  const [selection, setSelection] = useState<{ text: string; top: number; left: number } | null>(
    null,
  );
  const [role, setRole] = useState<string>(storedRole);
  const readRef = useRef<HTMLDivElement>(null);
  const areaRef = useRef<HTMLTextAreaElement>(null);

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

  const loadVersions = useCallback(() => {
    listDocVersions(target, path).then(setVersions);
  }, [target, path]);

  const openHistory = () => {
    setHistoryOpen((open) => !open);
    if (!historyOpen) loadVersions();
  };

  const startEditing = () => {
    setBuffer(text);
    setMessage("");
    setSaveError(null);
    setEditing(true);
    setViewing(null);
  };

  /** Apply a toolbar wrap to the textarea's current selection, keeping it selected. */
  const applyWrap = (wrap: Wrap) => {
    const area = areaRef.current;
    if (!area) return;
    const start = area.selectionStart;
    const end = area.selectionEnd;
    const chosen = buffer.slice(start, end);
    let replacement: string;
    if (wrap.linePrefix) {
      const lines = (chosen || wrap.placeholder).split("\n");
      replacement = lines.map((line) => wrap.before + line).join("\n");
    } else {
      replacement = wrap.before + (chosen || wrap.placeholder) + (wrap.after ?? "");
    }
    const next = buffer.slice(0, start) + replacement + buffer.slice(end);
    setBuffer(next);
    // Restore the selection around what was just wrapped, so a second click on another
    // button applies to the same passage instead of to a collapsed caret.
    requestAnimationFrame(() => {
      area.focus();
      area.setSelectionRange(start, start + replacement.length);
    });
  };

  const save = async () => {
    setSaving(true);
    setSaveError(null);
    try {
      const saved = await saveAgentDoc(target, {
        path,
        content: buffer,
        message,
        author: author || role,
        // Omitted while the store has never been read: there is no revision to be
        // stale against, and sending "" would read as "I looked at nothing".
        baseVersion: stored?.version,
      });
      setStored({
        path,
        content: buffer,
        version: saved.version,
        message: saved.message,
        author: author || role,
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
  const restore = () => {
    if (!viewing) return;
    setBuffer(viewingText);
    setMessage(`Restored the revision from ${shortVersion(viewing.version)}`);
    setViewing(null);
    setEditing(true);
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
    setSelection({
      text: chosen,
      // Above the selection where there is room, below it at the very top of the pane.
      top: rect.top - box.top + host.scrollTop - 38 < 0 ? rect.bottom - box.top + host.scrollTop + 8 : rect.top - box.top + host.scrollTop - 38,
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
    if (!stored) return "";
    const who = stored.author ? `${stored.author}: ` : "";
    return stored.message ? `${who}${stored.message}` : "";
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
              <Button variant="ghost" size="sm" onClick={restore}>
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
          <span>Showing what was written in this turn. The stored copy could not be read: {loadError}</span>
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
              <p className="px-3 pb-3 text-[12px] text-muted-foreground">
                No saved revisions yet.
              </p>
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
                      {version.author || "unattributed"} · {shortVersion(version.version)}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </aside>
        )}

        {editing ? (
          <div className="flex min-h-0 flex-1 flex-col">
            <div className="flex flex-wrap items-center gap-1 border-b border-border px-3 py-1.5">
              {WRAPS.map(({ icon: Icon, title, wrap }) => (
                <Button
                  key={title}
                  variant="ghost"
                  size="sm"
                  title={title}
                  onClick={() => applyWrap(wrap)}
                >
                  <Icon className="size-4" />
                </Button>
              ))}
              <Button
                variant="ghost"
                size="sm"
                title="Heading"
                onClick={() => applyWrap({ before: "## ", linePrefix: true, placeholder: "Heading" })}
              >
                H2
              </Button>
              <Button
                variant={preview ? "secondary" : "ghost"}
                size="sm"
                title="Show the rendered document beside the source"
                onClick={() => setPreview((on) => !on)}
                className="ml-auto"
              >
                <IconEye className="size-4" />
                Preview
              </Button>
            </div>
            <div className="flex min-h-0 flex-1">
              <Textarea
                ref={areaRef}
                value={buffer}
                onChange={(e) => setBuffer(e.target.value)}
                spellCheck
                className="min-h-0 flex-1 resize-none rounded-none border-0 font-mono text-[13px] leading-relaxed focus-visible:ring-0"
              />
              {preview && (
                <div className="min-h-0 flex-1 overflow-y-auto border-l border-border p-4">
                  <Streamdown parseIncompleteMarkdown>{buffer}</Streamdown>
                </div>
              )}
            </div>
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
              {!author && (
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
              )}
              <Button variant="ghost" size="sm" onClick={() => setEditing(false)} disabled={saving}>
                Cancel
              </Button>
              <Button size="sm" onClick={save} disabled={saving || buffer === text}>
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
              <div
                className="absolute z-10"
                style={{ top: selection.top, left: selection.left }}
              >
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
