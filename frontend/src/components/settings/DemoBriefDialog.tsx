/**
 * Post-setup popup: a short presenter brief + recommended demo flow, generated
 * by the setup agent and carried on the new assistant's metadata (demo_brief /
 * demo_flow). Shown once, right after the assistant-builder finishes.
 */
import { IconClipboardText, IconRoute } from "@tabler/icons-react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";

export interface DemoBrief {
  customer: string;
  brief: string[];
  flow: string[];
}

function BulletList({ items }: { items: string[] }) {
  return (
    <ul className="m-0 flex list-none flex-col gap-1.5 pl-0">
      {items.map((t, i) => (
        <li key={i} className="flex gap-2 text-[13px] leading-snug">
          <span className="mt-[6px] h-1 w-1 shrink-0 rounded-full bg-[var(--brand-primary)]" />
          <span>{t}</span>
        </li>
      ))}
    </ul>
  );
}

function NumberedList({ items }: { items: string[] }) {
  return (
    <ol className="m-0 flex list-none flex-col gap-1.5 pl-0">
      {items.map((t, i) => (
        <li key={i} className="flex gap-2 text-[13px] leading-snug">
          <span className="mt-px flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-[var(--brand-primary)] text-[10px] font-semibold text-[color:var(--brand-primary-foreground,#fff)]">
            {i + 1}
          </span>
          <span>{t}</span>
        </li>
      ))}
    </ol>
  );
}

export function DemoBriefDialog({
  brief,
  onClose,
}: {
  brief: DemoBrief | null;
  onClose: () => void;
}) {
  const open = !!brief;
  return (
    <Dialog open={open} onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{brief?.customer} demo is ready</DialogTitle>
          <DialogDescription>
            A quick brief for presenting this assistant.
          </DialogDescription>
        </DialogHeader>

        {brief && brief.brief.length > 0 && (
          <section className="flex flex-col gap-2">
            <h3 className="m-0 flex items-center gap-1.5 text-[11px] font-semibold tracking-wide text-muted-foreground uppercase">
              <IconClipboardText className="h-3.5 w-3.5" stroke={2} />
              Demo brief
            </h3>
            <BulletList items={brief.brief} />
          </section>
        )}

        {brief && brief.flow.length > 0 && (
          <section className="flex flex-col gap-2">
            <h3 className="m-0 flex items-center gap-1.5 text-[11px] font-semibold tracking-wide text-muted-foreground uppercase">
              <IconRoute className="h-3.5 w-3.5" stroke={2} />
              Recommended flow
            </h3>
            <NumberedList items={brief.flow} />
          </section>
        )}

        <DialogFooter>
          <Button onClick={onClose}>Got it</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
