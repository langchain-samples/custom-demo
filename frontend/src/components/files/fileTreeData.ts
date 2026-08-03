/**
 * Data plumbing for the sandbox file browser — the node shape the tree renders,
 * the viewer's request state, and a memoizing directory loader in front of
 * GET /sandbox-files.
 *
 * Deliberately free of JSX so the tree renderer (@headless-tree) can be swapped
 * without touching the fetch logic, and so these exports don't trip oxlint's
 * react(only-export-components) rule in a component file.
 */
import {
  listSandboxFiles,
  type SandboxEntry,
  type SandboxFile,
  type SandboxKind,
  type SandboxListing,
  type SandboxTarget,
} from "@/lib/api";

/** One tree node. `path` is both the absolute VM path and the tree item id. */
export interface SandboxNode {
  name: string;
  /** Absolute path on the sandbox VM — unique, so it doubles as the item id. */
  path: string;
  isDir: boolean;
  /** Server-side classification, used to pick an icon and grey out binaries. */
  kind: SandboxKind;
}

/** One loaded directory. Never rejects — failures arrive as `error`. */
export interface DirResult {
  nodes: SandboxNode[];
  /** The directory held more entries than the server's per-request cap. */
  truncated: boolean;
  error?: string;
}

/** Lifecycle of the right-hand viewer pane. */
export type FileState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "ready"; file: SandboxFile }
  | { status: "error"; message: string };

/** Last segment of an absolute POSIX path ("/workspace/a/b.txt" → "b.txt"). */
export function baseName(path: string): string {
  const parts = path.split("/").filter(Boolean);
  return parts.length ? parts[parts.length - 1] : path || "/";
}

/** Widen a listing entry into a tree node, defaulting `kind` defensively. */
export function entryToNode(e: SandboxEntry): SandboxNode {
  return {
    name: e.name || baseName(e.path),
    path: e.path,
    isDir: !!e.is_dir,
    kind: e.kind ?? (e.is_dir ? "dir" : "text"),
  };
}

/** Lazily loads one directory at a time, caching successes for the dialog's life. */
export interface DirLoader {
  /** Seed the cache with a listing already fetched (the bootstrap root call). */
  prime(listing: SandboxListing): void;
  /** Load one directory. Resolves (never rejects) with nodes and/or an `error`. */
  load(path: string): Promise<DirResult>;
}

/**
 * Build a directory loader bound to one sandbox target. Successful listings are
 * cached (the tree re-reads them on every collapse/expand), failures are not —
 * a retry should reach the server again. Concurrent loads of the same path share
 * one request.
 */
export function createDirLoader(target: SandboxTarget): DirLoader {
  const cache = new Map<string, DirResult>();
  const inflight = new Map<string, Promise<DirResult>>();

  const toResult = (l: SandboxListing): DirResult => ({
    nodes: (l.entries ?? []).map(entryToNode),
    truncated: !!l.truncated,
    error: l.error,
  });

  return {
    prime(listing) {
      if (!listing.error) cache.set(listing.path, toResult(listing));
    },
    load(path) {
      const hit = cache.get(path);
      if (hit) return Promise.resolve(hit);
      const pending = inflight.get(path);
      if (pending) return pending;
      const req = listSandboxFiles(target, path)
        .then((listing) => {
          const result = toResult(listing);
          if (!result.error) cache.set(path, result);
          return result;
        })
        .finally(() => inflight.delete(path));
      inflight.set(path, req);
      return req;
    },
  };
}
