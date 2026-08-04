/**
 * Right-hand pane of the file browser: renders one file from the sandbox VM.
 *
 *   markdown  → Streamdown (the same renderer chat answers use, so a README
 *               reads identically here and in the transcript)
 *   other text→ monospace <pre>, wrapped, matching ToolChip's payload style
 *   binary /  → a friendly placeholder built from the server's `reason` +
 *   oversized   `message`; never raw bytes
 *
 * Every state has visible copy — idle, loading, error and empty all render
 * something, so the pane is never a blank white rectangle.
 */
import {
  IconAlertTriangle,
  IconFileOff,
  IconLoader2,
  IconPointerFilled,
} from "@tabler/icons-react";
import { Streamdown } from "streamdown";
import { Button } from "@/components/ui/button";
import { PaneState } from "@/components/files/PaneState";
import type { FileState, SandboxNode } from "@/components/files/fileTreeData";
import { PROSE_CLS } from "@/lib/markdown";

interface SandboxFileViewProps {
  /** The row the user picked, used for the sticky path header. */
  node: SandboxNode | null;
  state: FileState;
  /** Fetch and append the next page (only offered when `next_offset` is set). */
  onLoadMore?: () => void;
  /** A "Show more" page is in flight. */
  appending?: boolean;
}

export function SandboxFileView({ node, state, onLoadMore, appending }: SandboxFileViewProps) {
  if (state.status === "idle" || !node) {
    return (
      <PaneState
        icon={<IconPointerFilled size={28} />}
        title="Select a file to preview"
        detail="Pick a file in the tree on the left. Folders load their contents when you open them."
      />
    );
  }

  const file = state.status === "ready" ? state.file : null;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* Sticky path header — the tree only shows basenames. */}
      <div className="flex flex-shrink-0 items-center gap-2 border-b border-border px-4 py-2">
        <span
          className="truncate font-mono text-xs text-muted-foreground"
          title={node.path}
        >
          {node.path}
        </span>
        {file?.language ? (
          <span className="ml-auto flex-shrink-0 rounded bg-panel-2 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-muted-foreground">
            {file.language}
          </span>
        ) : null}
      </div>

      <div className="min-h-0 flex-1 overflow-auto p-4">
        {state.status === "loading" ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <IconLoader2 size={15} className="animate-spin [animation-duration:0.6s]" />
            Loading {node.name}…
          </div>
        ) : state.status === "error" ? (
          <p className="m-0 text-sm text-destructive">
            ⚠️ Could not read this file: {state.message}
          </p>
        ) : file && file.content === null ? (
          <PaneState
            icon={<IconFileOff size={28} />}
            title={file.message || "This file can't be previewed."}
            detail={
              file.reason === "too_large"
                ? "The file is larger than the preview cap."
                : "Only text files are previewable in the browser."
            }
          />
        ) : file && file.content === "" ? (
          <p className="m-0 text-sm text-muted-foreground">This file is empty.</p>
        ) : file ? (
          <>
            {file.language === "markdown" ? (
              <div className={PROSE_CLS}>
                <Streamdown>{file.content ?? ""}</Streamdown>
              </div>
            ) : (
              <pre className="m-0 whitespace-pre-wrap break-words rounded-md border border-border bg-background p-3 font-mono text-[12px] text-foreground">
                {file.content}
              </pre>
            )}
            {file.truncated ? (
              <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                <span className="inline-flex items-center gap-1.5">
                  <IconAlertTriangle size={13} />
                  Showing the first {(file.content ?? "").split("\n").length.toLocaleString()}{" "}
                  lines - this file is longer.
                </span>
                {file.next_offset != null && onLoadMore ? (
                  <Button variant="secondary" size="sm" onClick={onLoadMore} disabled={appending}>
                    {appending ? "Loading…" : "Show more"}
                  </Button>
                ) : null}
              </div>
            ) : null}
          </>
        ) : null}
      </div>
    </div>
  );
}
