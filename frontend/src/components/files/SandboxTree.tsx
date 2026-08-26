/**
 * Lazy file tree over the assistant's sandbox VM.
 *
 * Headless: @headless-tree hands back prop bags (roles, aria-level, roving
 * tabindex, arrow-key handlers) and ships no CSS, so every row is our own
 * Tailwind-token markup. Children are fetched one directory per expand via the
 * injected `loadDir`; nothing is loaded until a folder is opened.
 *
 * Keyboard: ↑/↓ move, →/← expand/collapse, Home/End jump — all from
 * hotkeysCoreFeature, bound to the tree element (not the document), so they
 * can't fight the Radix Dialog's focus trap. Enter is ours (see `hotkeys`
 * below): the library has no default binding for "open the focused item".
 */
import {
  asyncDataLoaderFeature,
  hotkeysCoreFeature,
  selectionFeature,
} from "@headless-tree/core";
import { useTree } from "@headless-tree/react";
import { Fragment } from "react";
import {
  IconAlertTriangle,
  IconFileCode,
  IconFileText,
  IconFileTypeCsv,
  IconFolder,
  IconFolderOpen,
  IconLoader2,
  IconMarkdown,
  IconFileTypePdf,
  IconPhoto,
} from "@tabler/icons-react";
import { baseName, type SandboxNode } from "@/components/files/fileTreeData";

/** Pixels of indent per tree level. */
const INDENT = 14;

const CODE_EXT = new Set([
  "py", "ts", "tsx", "js", "jsx", "json", "yaml", "yml", "toml",
  "sh", "bash", "html", "css", "sql", "rs", "go", "java", "rb",
]);

/** Icon for one node, chosen from the server's `kind` plus the extension. */
function iconFor(node: SandboxNode, expanded: boolean) {
  if (node.isDir) return expanded ? IconFolderOpen : IconFolder;
  if (node.kind === "binary") return IconPhoto;
  const ext = node.name.includes(".") ? node.name.split(".").pop()!.toLowerCase() : "";
  // "media" is a binary the browser CAN render, so it stays undimmed (see `dim` below)
  // and gets a real icon rather than the generic one.
  if (node.kind === "media") return ext === "pdf" ? IconFileTypePdf : IconPhoto;
  if (ext === "md" || ext === "mdx" || ext === "markdown") return IconMarkdown;
  if (ext === "csv" || ext === "tsv") return IconFileTypeCsv;
  if (CODE_EXT.has(ext)) return IconFileCode;
  return IconFileText;
}

interface SandboxTreeProps {
  /** Absolute sandbox root (from the listing's `root`) — the tree's root id. */
  rootPath: string;
  /** Fetch one directory's children. Must resolve, never reject. */
  loadDir: (path: string) => Promise<SandboxNode[]>;
  /** Fired when a non-directory row is activated (click or Enter). */
  onOpenFile: (node: SandboxNode) => void;
  /** Path currently shown in the viewer, highlighted in the tree. */
  selectedPath: string | null;
  /**
   * Folders whose last load failed. They are indistinguishable from empty ones
   * otherwise — @headless-tree caches the `[]` that `loadDir` returned and
   * `retrieveChildrenIds` short-circuits on it, so collapse/re-expand never
   * refetches. Each gets an inline retry that drops that cache entry.
   */
  failedDirs: ReadonlySet<string>;
}

export function SandboxTree({
  rootPath,
  loadDir,
  onOpenFile,
  selectedPath,
  failedDirs,
}: SandboxTreeProps) {
  const tree = useTree<SandboxNode>({
    rootItemId: rootPath,
    initialState: { expandedItems: [] },
    // getItemData() is null while an item's data is still in flight, even though
    // the type says SandboxNode — always guard.
    getItemName: (item) => item.getItemData()?.name ?? "…",
    isItemFolder: (item) => item.getItemData()?.isDir ?? false,
    onPrimaryAction: (item) => {
      const data = item.getItemData();
      if (data && !data.isDir) onOpenFile(data);
    },
    createLoadingItemData: () => ({
      name: "Loading…",
      path: "",
      isDir: false,
      kind: "text",
    }),
    dataLoader: {
      // Only ever called for the root: every other item's data arrives with its
      // parent's listing via getChildrenWithData.
      getItem: (id) => ({ name: baseName(id), path: id, isDir: true, kind: "dir" }),
      getChildrenWithData: async (id) =>
        (await loadDir(id)).map((node) => ({ id: node.path, data: node })),
    },
    indent: INDENT,
    // Enter opens the focused file. The tree feature ships arrow/Home/End
    // hotkeys but no "activate", so this custom entry is what makes the browser
    // usable without a mouse.
    hotkeys: {
      customOpenFocusedItem: {
        hotkey: "Enter",
        preventDefault: true,
        handler: (_e, t) => t.getFocusedItem()?.primaryAction(),
      },
    },
    features: [asyncDataLoaderFeature, selectionFeature, hotkeysCoreFeature],
  });

  return (
    <div {...tree.getContainerProps("Agent files")} className="select-none py-0.5">
      {tree.getItems().map((item) => {
        const data = item.getItemData();
        const isDir = item.isFolder();
        const expanded = isDir && item.isExpanded();
        const loading = item.isLoading();
        const node: SandboxNode = data ?? {
          name: item.getItemName(),
          path: item.getId(),
          isDir,
          kind: isDir ? "dir" : "text",
        };
        const Icon = iconFor(node, expanded);
        const active = !!selectedPath && item.getId() === selectedPath;
        // Binaries are listed but can't be previewed — dim them so that's clear
        // before the click (the viewer still explains why when they're opened).
        const dim = !isDir && node.kind === "binary";
        const failed = expanded && failedDirs.has(item.getId());
        const row = (
          <button
            {...item.getProps()}
            type="button"
            title={item.getId()}
            style={{ paddingLeft: item.getItemMeta().level * INDENT + 8 }}
            className={
              "flex w-full items-center gap-1.5 rounded-md py-1 pr-2 text-left text-[13px]" +
              " outline-none transition-colors hover:bg-[var(--brand-primary)]/15" +
              " focus-visible:ring-1 focus-visible:ring-ring" +
              (active
                ? " bg-brand-soft font-medium text-[color:var(--brand-label)]"
                : " text-foreground") +
              (dim && !active ? " opacity-55" : "")
            }
          >
            {loading ? (
              <IconLoader2 size={14} className="shrink-0 animate-spin opacity-70" />
            ) : (
              <Icon size={14} className="shrink-0 opacity-70" />
            )}
            <span className="truncate">{item.getItemName()}</span>
          </button>
        );
        if (!failed) return <Fragment key={item.getKey()}>{row}</Fragment>;
        return (
          <Fragment key={item.getKey()}>
            {row}
            {/* role="none": a tree's children must be treeitems, and this is chrome.
                invalidateChildrenIds() deletes the cached `[]` before refetching —
                the one path that actually re-issues the request. */}
            <div
              role="none"
              style={{ paddingLeft: (item.getItemMeta().level + 1) * INDENT + 8 }}
              className="flex items-center gap-1.5 py-1 pr-2 text-[12px] text-destructive"
            >
              <IconAlertTriangle size={13} className="shrink-0" />
              <span className="truncate">Couldn't load</span>
              <button
                type="button"
                className="rounded px-1 underline underline-offset-2 outline-none hover:bg-[var(--brand-primary)]/15 focus-visible:ring-1 focus-visible:ring-ring"
                onClick={() => void item.invalidateChildrenIds()}
              >
                Retry
              </button>
            </div>
          </Fragment>
        );
      })}
    </div>
  );
}
