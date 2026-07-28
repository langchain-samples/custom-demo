/**
 * A collapsed-by-default disclosure with a rotating caret, replacing the SPA's
 * `<details class="sp-collapse">`. Used for "Quick actions" and the advanced
 * "Synthetic data prompt".
 */
import { useState, type ReactNode } from "react";
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible";
import { ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";
import { LABEL_CLS } from "./types";

interface Props {
  title: string;
  children: ReactNode;
  defaultOpen?: boolean;
}

export function CollapseSection({ title, children, defaultOpen = false }: Props) {
  const [open, setOpen] = useState(defaultOpen);
  return (
    <Collapsible
      open={open}
      onOpenChange={setOpen}
      className="border-t border-border pt-2.5"
    >
      <CollapsibleTrigger
        className={cn(LABEL_CLS, "flex w-full items-center gap-1 cursor-pointer")}
      >
        <ChevronRight
          className={cn("size-3 transition-transform", open && "rotate-90")}
        />
        {title}
      </CollapsibleTrigger>
      <CollapsibleContent className="mt-2.5 flex flex-col gap-2.5">
        {children}
      </CollapsibleContent>
    </Collapsible>
  );
}
