/**
 * The right-hand pane: the widget dashboard, plus one tab per artifact.
 *
 *   ┌ Dashboard │ ⟨req-204⟩ brief.md │ spec.md │ report.html ─────┐
 *   │                                                             │
 *   │  DashboardCanvas / MarkdownArtifact / HtmlArtifact          │
 *   └─────────────────────────────────────────────────────────────┘
 *
 * "Dashboard" is always first and always present: widgets are the primary surface and
 * an artifact is the escape hatch for a layout the six widget types cannot express.
 *
 * Artifacts written into a FOLDER are banded together under that folder's name, the way
 * a browser groups tabs. The folder is how one request, ticket or feature packages its
 * documents, so a reviewer opening the third document of a request can see the other
 * two belong with it rather than reading three similar filenames and guessing.
 *
 * A newly seen artifact steals focus once, on first sight, so the user watches it being
 * written. After that the tab is theirs: later chunks of the same file update in place
 * without yanking them back, so reading artifact A while the agent appends to B works.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { IconFileTypePdf } from "@tabler/icons-react";
import { Button } from "@/components/ui/button";
import { DashboardCanvas } from "@/components/DashboardCanvas";
import { HtmlArtifact, type HtmlArtifactHandle } from "@/components/HtmlArtifact";
import { MarkdownArtifact } from "@/components/MarkdownArtifact";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { artifactFormat, artifactName, groupArtifacts } from "@/lib/artifacts";
import type { DocsTarget, Widget } from "@/lib/api";
import type { Theme } from "@/lib/theme";

/** One artifact's live state. `content` is partial while `streaming`. */
export interface ArtifactState {
  content: string;
  streaming: boolean;
}

export interface DashboardPaneProps {
  widgets: Widget[];
  theme: Theme;
  /** Keyed by absolute path. Insertion order is tab order, within folder groups. */
  artifacts: Record<string, ArtifactState>;
  /** Which assistant's document store the Markdown tabs read and save through. */
  docsTarget?: DocsTarget;
  /** Who a document save is recorded as in the revision history. */
  author?: string;
  /** Hand a passage the reader selected to the chat composer. */
  onAskAbout?: (excerpt: string, path: string) => void;
}

/** Tab value for the widget canvas. Not a path, so it cannot collide with one. */
const CANVAS_TAB = "dashboard";

/**
 * Band colors for folder groups, assigned by name rather than by position.
 *
 * By name so a group keeps its color when another request's tabs appear before it:
 * the color is a handle the reader learns ("the green one is req-204"), and one that
 * reshuffled itself as work arrived would be worse than no color.
 */
const BAND_COLORS = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
];

/** Stable index into BAND_COLORS for a folder name. */
function bandColor(folder: string): string {
  let hash = 0;
  for (let i = 0; i < folder.length; i++) hash = (hash * 31 + folder.charCodeAt(i)) % 100003;
  return BAND_COLORS[hash % BAND_COLORS.length];
}

/** Widget canvas plus a tab per artifact the agent wrote. */
export function DashboardPane({
  widgets,
  theme,
  artifacts,
  docsTarget,
  author,
  onAskAbout,
}: DashboardPaneProps) {
  const paths = Object.keys(artifacts);
  // Newline-joined so the effect below compares on a plain string: a fresh array every
  // render would re-run it forever.
  const pathsKey = paths.join("\n");
  // No widgets means no dashboard to show. The pane mounts for a graph or an artifact as
  // well, so the tab would otherwise sit there reading "LIVE DASHBOARD" over blank space.
  const hasWidgets = widgets.length > 0;
  const [active, setActive] = useState<string>(CANVAS_TAB);
  const seen = useRef<Set<string>>(new Set());
  // Only the visible artifact is mounted (Tabs unmounts the rest), so one ref is enough
  // to reach whichever one the download button is currently pointing at.
  const artifact = useRef<HtmlArtifactHandle>(null);
  const groups = useMemo(() => groupArtifacts(paths), [pathsKey]); // eslint-disable-line react-hooks/exhaustive-deps

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
    // A tab can disappear (a reset, or the agent deleting an artifact); fall back
    // rather than render an empty pane.
    setActive((cur) => (cur !== CANVAS_TAB && !list.includes(cur) ? CANVAS_TAB : cur));
  }, [pathsKey]);

  // Dashboard is the default tab but does not always exist, so land on the first tab
  // that does rather than on a trigger that is not rendered.
  useEffect(() => {
    if (active !== CANVAS_TAB || hasWidgets) return;
    const first = pathsKey ? pathsKey.split("\n")[0] : "";
    setActive(first || CANVAS_TAB);
  }, [active, hasWidgets, pathsKey]);

  // Nothing but widgets: render the canvas alone, exactly as before any of this.
  if (paths.length === 0) {
    return <DashboardCanvas widgets={widgets} theme={theme} />;
  }

  const tab = (path: string) => (
    <TabsTrigger key={path} value={path} className="max-w-52">
      {/* The full path lives here rather than in a header line: the tab names
          the file, and hovering gives you where it is. */}
      <span className="truncate" title={path}>
        {artifactName(path)}
      </span>
    </TabsTrigger>
  );

  return (
    <Tabs value={active} onValueChange={setActive} className="flex min-h-0 flex-1 flex-col gap-0">
      {/* Tabs and the download share ONE row. The button had a row to itself under the
          tabs, which doubled the header's height for a single control. */}
      <div className="mx-4 mt-3 flex items-center gap-3 print:hidden">
        <TabsList className="w-fit min-w-0 flex-shrink overflow-x-auto">
          {hasWidgets && <TabsTrigger value={CANVAS_TAB}>Dashboard</TabsTrigger>}
          {groups.map((group, index) =>
            group.folder ? (
              <span
                key={`${group.folder}-${index}`}
                className="flex items-center gap-1 rounded-md pb-0.5"
                style={{ boxShadow: `inset 0 -2px 0 0 ${bandColor(group.folder)}` }}
              >
                <span
                  className="ml-1 max-w-28 truncate rounded px-1.5 py-0.5 text-[11px] font-medium text-white"
                  style={{ backgroundColor: bandColor(group.folder) }}
                  title={group.folder}
                >
                  {group.folder}
                </span>
                {group.paths.map(tab)}
              </span>
            ) : (
              group.paths.map(tab)
            ),
          )}
        </TabsList>
        {/* Print-to-PDF is an HTML-document affordance: a Markdown tab is edited and
            versioned instead, and printing its source is not what anyone wants. */}
        {active !== CANVAS_TAB && artifactFormat(active) === "html" && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => artifact.current?.savePdf()}
            // Mid-write the document is incomplete, so a PDF of it would be too.
            disabled={artifacts[active]?.streaming}
            className="ml-auto flex-shrink-0"
            title="Open the print dialog, where you can choose Save as PDF"
          >
            <IconFileTypePdf className="size-4" />
            Save as PDF
          </Button>
        )}
      </div>
      {hasWidgets && (
        <TabsContent value={CANVAS_TAB} className="flex min-h-0 flex-1 flex-col">
          <DashboardCanvas widgets={widgets} theme={theme} />
        </TabsContent>
      )}
      {paths.map((path) => (
        // forceMount would re-run every artifact's scripts on every render; artifacts
        // are cheap to remount and only the visible one needs to exist.
        <TabsContent key={path} value={path} className="flex min-h-0 flex-1 flex-col">
          {artifactFormat(path) === "markdown" ? (
            <MarkdownArtifact
              path={path}
              content={artifacts[path].content}
              streaming={artifacts[path].streaming}
              target={docsTarget ?? {}}
              author={author}
              onAskAbout={onAskAbout}
            />
          ) : (
            <HtmlArtifact
              ref={artifact}
              path={path}
              content={artifacts[path].content}
              streaming={artifacts[path].streaming}
            />
          )}
        </TabsContent>
      ))}
    </Tabs>
  );
}
