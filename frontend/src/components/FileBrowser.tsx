/**
 * "Browse agent files" — a near-full-screen dialog over the active assistant's
 * LangSmith sandbox VM: lazy file tree on the left, file viewer on the right.
 *
 *   ┌──────────────────────────── Agent files ──────────────── ⟳ ✕ ┐
 *   │  tree (280px)      │  viewer                                  │
 *   │  lazy per-folder   │  markdown / monospace / placeholder      │
 *   ├──────────────────────────────────────────────────────────────┤
 *   │  root · VM id · truncation + directory-error notices          │
 *   └──────────────────────────────────────────────────────────────┘
 *
 * This file is only the chrome; SandboxBrowser owns the data. Refresh works by
 * bumping SandboxBrowser's `key`, so a reload is a remount rather than manual
 * cache invalidation.
 *
 * The sandbox is legitimately optional (DA_SANDBOX=0, no entitlement, no key,
 * or simply never warmed) and opening this must never create one — so every
 * outcome has visible copy: no assistant, loading, unavailable (with Retry), or
 * a browsable tree.
 */
import { useMemo, useState } from "react";
import { IconRefresh, IconUserOff } from "@tabler/icons-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { PaneState } from "@/components/files/PaneState";
import { SandboxBrowser } from "@/components/files/SandboxBrowser";
import type { Assistant, SandboxTarget } from "@/lib/api";

interface FileBrowserProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /**
   * The active assistant. Its metadata carries the same sandbox key the agent
   * itself uses (`ls_artifacts.agent_repo` + `customer`) — this is the object
   * App already holds, not a second fetch of the assistant.
   */
  assistant: Assistant | null;
}

export function FileBrowser({ open, onOpenChange, assistant }: FileBrowserProps) {
  /** Bumped by Refresh to remount SandboxBrowser with empty caches. */
  const [reloadNonce, setReloadNonce] = useState(0);

  const assistantId = assistant?.assistant_id ?? null;
  const agentRepo = assistant?.metadata?.ls_artifacts?.agent_repo;
  const customer = assistant?.metadata?.customer;
  // Blank strings are omitted, not sent: assistant_setup writes
  // `ls_artifacts.agent_repo = ""` when there's no Context Hub repo, and the
  // runtime falls back to the customer in exactly the same way.
  const target = useMemo<SandboxTarget>(
    () => ({ agent_repo: agentRepo || undefined, customer: customer || undefined }),
    [agentRepo, customer],
  );

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {/* max-w-none AND sm:max-w-none: DialogContent's base caps at sm:max-w-sm.
          `flex` overrides its default `grid`; gap-0/p-0 hand layout to the panes. */}
      <DialogContent className="flex h-[88vh] w-[min(1200px,94vw)] max-w-none flex-col gap-0 p-0 sm:max-w-none print:hidden">
        {/* pr-12 keeps the title clear of DialogContent's own close button. */}
        <DialogHeader className="flex-row items-center gap-2 border-b border-border p-3 pr-12">
          <DialogTitle>Agent files</DialogTitle>
          <DialogDescription className="sr-only">
            Browse the files on this assistant's sandbox virtual machine.
          </DialogDescription>
          {assistantId ? (
            <Button
              variant="ghost"
              size="icon-sm"
              className="ml-auto"
              title="Reload the file tree"
              aria-label="Reload the file tree"
              onClick={() => setReloadNonce((n) => n + 1)}
            >
              <IconRefresh size={15} />
            </Button>
          ) : null}
        </DialogHeader>

        {assistantId ? (
          <SandboxBrowser
            key={`${assistantId}:${reloadNonce}`}
            target={target}
            onReload={() => setReloadNonce((n) => n + 1)}
          />
        ) : (
          <PaneState
            icon={<IconUserOff size={30} />}
            title="No assistant selected"
            detail="Pick or create an assistant in Settings — the file browser reads that assistant's sandbox."
          />
        )}
      </DialogContent>
    </Dialog>
  );
}
