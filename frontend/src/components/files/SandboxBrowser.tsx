/**
 * The two-pane body of the file browser: lazy tree on the left, viewer on the
 * right, provenance footer underneath.
 *
 * Owns all sandbox state (root listing, directory cache, selected file). It is
 * deliberately a separate component from the dialog chrome so that "Refresh" is
 * a plain remount — the parent bumps this component's `key`, every cache and
 * request dies with the old instance, and there is no cache-invalidation code
 * to get wrong. Radix unmounts dialog children on close, so each open likewise
 * starts from a fresh listing.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { IconAlertTriangle, IconFolderOff, IconLoader2, IconRefresh } from "@tabler/icons-react";
import { Button } from "@/components/ui/button";
import { PaneState } from "@/components/files/PaneState";
import { SandboxFileView } from "@/components/files/SandboxFileView";
import { SandboxTree } from "@/components/files/SandboxTree";
import {
  createDirLoader,
  type FileState,
  type SandboxNode,
} from "@/components/files/fileTreeData";
import {
  listSandboxFiles,
  readSandboxFile,
  type SandboxListing,
  type SandboxTarget,
} from "@/lib/api";

/** Bootstrap: one root listing, which also reveals the root path and VM id. */
type Boot =
  | { status: "loading" }
  | { status: "ready"; listing: SandboxListing }
  | { status: "error"; message: string };

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

interface SandboxBrowserProps {
  target: SandboxTarget;
  /** Ask the parent to remount this component (drops caches, refetches). */
  onReload: () => void;
}

export function SandboxBrowser({ target, onReload }: SandboxBrowserProps) {
  const [boot, setBoot] = useState<Boot>({ status: "loading" });
  const [selected, setSelected] = useState<SandboxNode | null>(null);
  const [fileState, setFileState] = useState<FileState>({ status: "idle" });
  const [dirError, setDirError] = useState<string | null>(null);
  const [truncated, setTruncated] = useState(false);
  // Folders whose last load failed. @headless-tree caches whatever `loadDir`
  // returns — including `[]` — and never refetches on collapse/re-expand, so a
  // failed folder would otherwise sit there looking convincingly empty. The tree
  // renders an inline retry for each of these.
  const [failedDirs, setFailedDirs] = useState<ReadonlySet<string>>(() => new Set());
  // A "Show more" page is in flight (the viewer's button is disabled meanwhile).
  const [appending, setAppending] = useState(false);

  // One directory cache for this instance's lifetime.
  const loader = useMemo(() => createDirLoader(target), [target]);

  useEffect(() => {
    let cancelled = false;
    setBoot({ status: "loading" });
    // listSandboxFiles never throws — an absent sandbox arrives as `error`.
    listSandboxFiles(target).then((listing) => {
      if (cancelled) return;
      if (listing.error) {
        setBoot({ status: "error", message: listing.error });
        return;
      }
      loader.prime(listing);
      if (listing.truncated) setTruncated(true);
      setBoot({ status: "ready", listing });
    });
    return () => {
      cancelled = true;
    };
  }, [target, loader]);

  const loadDir = useCallback(
    async (path: string): Promise<SandboxNode[]> => {
      const result = await loader.load(path);
      // Resolving (rather than throwing) is deliberate: a rejection leaves
      // @headless-tree's `loadingItemChildrens` set forever — a permanent spinner
      // and an unhandled rejection. The retry lives on the node instead.
      setFailedDirs((prev) => {
        if (result.error ? prev.has(path) : !prev.has(path)) return prev;
        const next = new Set(prev);
        if (result.error) next.add(path);
        else next.delete(path);
        return next;
      });
      // Footer mirrors the latest outcome, so a successful retry clears the notice.
      setDirError(result.error ?? null);
      if (result.truncated) setTruncated(true);
      return result.nodes;
    },
    [loader],
  );

  // Guards against an out-of-order response when files are clicked quickly.
  const readSeq = useRef(0);
  const openFile = useCallback(
    async (node: SandboxNode) => {
      const seq = ++readSeq.current;
      setSelected(node);
      setAppending(false);
      setFileState({ status: "loading" });
      try {
        const file = await readSandboxFile(target, node.path);
        if (readSeq.current === seq) setFileState({ status: "ready", file });
      } catch (e) {
        if (readSeq.current === seq) setFileState({ status: "error", message: errMsg(e) });
      }
    },
    [target],
  );

  /**
   * Fetch the page after the one on screen and append it, so a long file reads
   * as one document. Without this the viewer would stop at the server's line
   * limit with no way forward — the state the truncation notice warns about.
   */
  const loadMore = useCallback(async () => {
    if (appending || fileState.status !== "ready") return;
    const { path, next_offset: offset } = fileState.file;
    if (offset == null) return;
    const seq = readSeq.current;
    setAppending(true);
    try {
      const page = await readSandboxFile(target, path, { offset });
      if (readSeq.current !== seq) return; // another file was opened meanwhile
      setFileState((prev) =>
        prev.status === "ready" && prev.file.path === path
          ? {
              status: "ready",
              // Keep the ORIGINAL offset: `content` now spans page 1..n.
              file: {
                ...page,
                offset: prev.file.offset,
                content: `${prev.file.content ?? ""}\n${page.content ?? ""}`,
              },
            }
          : prev,
      );
    } catch (e) {
      if (readSeq.current === seq) setFileState({ status: "error", message: errMsg(e) });
    } finally {
      setAppending(false);
    }
  }, [appending, fileState, target]);

  if (boot.status === "loading") {
    return (
      <PaneState
        icon={<IconLoader2 size={30} className="animate-spin" />}
        title="Opening the sandbox…"
      />
    );
  }

  if (boot.status === "error") {
    return (
      <PaneState
        icon={<IconFolderOff size={30} />}
        title="No sandbox available for this assistant"
        detail={`${boot.message} — a sandbox appears once the agent has run a turn that uses one, and browsing never starts one.`}
        action={
          <Button variant="secondary" size="sm" className="mt-1" onClick={onReload}>
            <IconRefresh size={14} /> Try again
          </Button>
        }
      />
    );
  }

  const { root, entries, sandbox_id: sandboxId } = boot.listing;

  return (
    <>
      <div className="grid min-h-0 flex-1 grid-cols-[280px_1fr]">
        <div className="min-h-0 overflow-auto border-r border-border bg-panel-2 p-1.5">
          {entries.length === 0 ? (
            <p className="m-0 px-2 py-3 text-xs text-muted-foreground">
              This sandbox has no files yet.
            </p>
          ) : (
            <SandboxTree
              rootPath={root}
              loadDir={loadDir}
              onOpenFile={openFile}
              selectedPath={selected?.path ?? null}
              failedDirs={failedDirs}
            />
          )}
        </div>
        <div className="flex min-h-0 flex-col">
          <SandboxFileView
            node={selected}
            state={fileState}
            onLoadMore={loadMore}
            appending={appending}
          />
        </div>
      </div>

      <div className="flex flex-shrink-0 flex-wrap items-center gap-x-3 gap-y-1 border-t border-border px-3 py-1.5 text-[11px] text-muted-foreground">
        <span className="font-mono">{root}</span>
        {sandboxId ? <span>VM {sandboxId}</span> : null}
        {truncated ? (
          <span className="inline-flex items-center gap-1 text-foreground">
            <IconAlertTriangle size={12} />
            Some folders have more entries than shown.
          </span>
        ) : null}
        {dirError ? (
          <span className="inline-flex items-center gap-1 text-destructive">
            <IconAlertTriangle size={12} />
            {dirError}
          </span>
        ) : null}
      </div>
    </>
  );
}
