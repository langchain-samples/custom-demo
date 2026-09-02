/**
 * The right-hand pane: the widget dashboard, plus one tab per HTML artifact.
 *
 *   ┌ Dashboard │ report.html │ invoice.html ──────────┐
 *   │                                                   │
 *   │  DashboardCanvas  /  HtmlArtifact                 │
 *   └───────────────────────────────────────────────────┘
 *
 * "Dashboard" is always first and always present: widgets are the primary surface and
 * an artifact is the escape hatch for a layout the six widget types cannot express.
 *
 * A newly seen artifact steals focus once, on first sight, so the user watches it being
 * written. After that the tab is theirs: later chunks of the same file update in place
 * without yanking them back, so reading artifact A while the agent appends to B works.
 */
import { useEffect, useRef, useState } from "react";
import { DashboardCanvas } from "@/components/DashboardCanvas";
import { HtmlArtifact } from "@/components/HtmlArtifact";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { artifactName } from "@/lib/artifacts";
import type { Widget } from "@/lib/api";
import type { Theme } from "@/lib/theme";

/** One artifact's live state. `content` is partial while `streaming`. */
export interface ArtifactState {
  content: string;
  streaming: boolean;
}

export interface DashboardPaneProps {
  widgets: Widget[];
  theme: Theme;
  /** Keyed by absolute path. Insertion order is tab order. */
  artifacts: Record<string, ArtifactState>;
}

/** Tab value for the widget canvas. Not a path, so it cannot collide with one. */
const CANVAS_TAB = "dashboard";

/** Widget canvas plus an HTML artifact tab per file the agent wrote. */
export function DashboardPane({ widgets, theme, artifacts }: DashboardPaneProps) {
  const paths = Object.keys(artifacts);
  // Newline-joined so the effect below compares on a plain string: a fresh array every
  // render would re-run it forever.
  const pathsKey = paths.join("\n");
  const [active, setActive] = useState<string>(CANVAS_TAB);
  const seen = useRef<Set<string>>(new Set());

  useEffect(() => {
    const list = pathsKey ? pathsKey.split("\n") : [];
    let firstSight: string | null = null;
    for (const path of list) {
      if (seen.current.has(path)) continue;
      seen.current.add(path);
      firstSight = path;
    }
    if (firstSight !== null) {
      setActive(firstSight);
      return;
    }
    // A tab can disappear on reset; fall back rather than render an empty pane.
    setActive((cur) => (cur !== CANVAS_TAB && !list.includes(cur) ? CANVAS_TAB : cur));
  }, [pathsKey]);

  // No artifacts yet: render the canvas alone, exactly as before this feature.
  if (paths.length === 0) {
    return <DashboardCanvas widgets={widgets} theme={theme} />;
  }

  return (
    <Tabs value={active} onValueChange={setActive} className="flex min-h-0 flex-1 flex-col gap-0">
      <TabsList className="mx-4 mt-3 w-fit max-w-[calc(100%-2rem)] overflow-x-auto print:hidden">
        <TabsTrigger value={CANVAS_TAB}>Dashboard</TabsTrigger>
        {paths.map((path) => (
          <TabsTrigger key={path} value={path} className="max-w-52">
            {/* The full path lives here rather than in a header line: the tab names the
                file, and hovering gives you where it is. */}
            <span className="truncate" title={path}>
              {artifactName(path)}
            </span>
          </TabsTrigger>
        ))}
      </TabsList>
      <TabsContent value={CANVAS_TAB} className="flex min-h-0 flex-1 flex-col">
        <DashboardCanvas widgets={widgets} theme={theme} />
      </TabsContent>
      {paths.map((path) => (
        // forceMount would re-run every artifact's scripts on every render; artifacts
        // are cheap to remount and only the visible one needs to exist.
        <TabsContent key={path} value={path} className="flex min-h-0 flex-1 flex-col">
          <HtmlArtifact
            path={path}
            content={artifacts[path].content}
            streaming={artifacts[path].streaming}
          />
        </TabsContent>
      ))}
    </Tabs>
  );
}
