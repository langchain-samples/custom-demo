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
import { IconFileTypePdf, IconX } from "@tabler/icons-react";
import { Button } from "@/components/ui/button";
import { DashboardCanvas } from "@/components/DashboardCanvas";
import { HtmlArtifact, type HtmlArtifactHandle } from "@/components/HtmlArtifact";
import { MarkdownArtifact } from "@/components/MarkdownArtifact";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { artifactFolder, artifactFormat, artifactName, groupArtifacts } from "@/lib/artifacts";
import { cn } from "@/lib/utils";
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
  /**
   * Close one artifact's tab.
   *
   * CLOSES the tab; it does not delete the document. A Markdown artifact lives in the
   * assistant's documents repo with its revisions, and a tab is just a view of it, so
   * closing has to be as cheap and as reversible as closing a browser tab. The agent
   * writing to that file again reopens it.
   */
  onCloseArtifact?: (path: string) => void;
}

/** Tab value for the widget canvas. Not a path, so it cannot collide with one. */
const CANVAS_TAB = "dashboard";

/**
 * Tighter than the shared tab default, which is sized for a settings pane.
 *
 * The selected-tab indicator is `inset-0` on the trigger, so its padding IS the size of
 * the coloured pill, and at the default `px-3.5 py-1.5` the brand block around a filename
 * dominates the strip. Applied here rather than in `ui/tabs`, which other views use at
 * their own scale. Ordered last in `cn` so tailwind-merge drops the defaults.
 */
const TAB_CLS = "max-w-52 gap-1 px-2 py-0.5 text-[13px]";

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
  onCloseArtifact,
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
  /**
   * Folders whose tabs are folded away behind their label.
   *
   * A request that has run a few stages carries five or six documents, and two requests
   * at once fills the strip. Collapsing is how a browser solves exactly this, and the
   * label stays put so the group is still visibly there.
   */
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  /** Paths whose tab is currently rendered, in strip order. */
  const visible = useMemo(
    () => groups.flatMap((g) => (g.folder && collapsed.has(g.folder) ? [] : g.paths)),
    [groups, collapsed],
  );

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
      const folder = artifactFolder(firstSight);
      if (folder) {
        setCollapsed((prev) => {
          if (!prev.has(folder)) return prev;
          const next = new Set(prev);
          next.delete(folder);
          return next;
        });
      }
      return;
    }
    // A tab can disappear (a reset, or the agent deleting an artifact); fall back
    // rather than render an empty pane.
    setActive((cur) => (cur !== CANVAS_TAB && !list.includes(cur) ? CANVAS_TAB : cur));
  }, [pathsKey]);

  /**
   * Keep the selection on a tab that is actually on screen.
   *
   * ONE effect, deliberately. This was two - "the dashboard does not always exist, so
   * land on the first artifact" and "the selected artifact is gone, so fall back to the
   * dashboard" - and they fight: collapse every group and neither has a valid answer, so
   * each one's correction re-triggers the other and the pane renders forever.
   */
  useEffect(() => {
    if (active !== CANVAS_TAB && visible.includes(active)) return;
    if (active === CANVAS_TAB && (hasWidgets || !visible.length)) return;
    setActive(visible[0] ?? CANVAS_TAB);
  }, [active, hasWidgets, visible]);

  /**
   * Close `path`, having first moved off it.
   *
   * Selection moves to the neighbour rather than back to the dashboard: closing the
   * third of four documents should leave you reading the fourth, the way a browser
   * does, not send you to the start.
   */
  const close = (path: string) => {
    if (path === active) {
      const at = visible.indexOf(path);
      const next = visible[at + 1] ?? visible[at - 1] ?? CANVAS_TAB;
      setActive(next);
    }
    // Forget it, so a later write to the same file counts as first sight and takes
    // focus again. A closed tab that silently reappeared unfocused would look broken.
    seen.current.delete(path);
    onCloseArtifact?.(path);
  };

  const toggleGroup = (folder: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(folder)) next.delete(folder);
      else next.add(folder);
      return next;
    });
  };

  // Nothing but widgets: render the canvas alone, exactly as before any of this.
  if (paths.length === 0) {
    return <DashboardCanvas widgets={widgets} theme={theme} />;
  }

  const tab = (path: string) => (
    <TabsTrigger key={path} value={path} className={cn("group", TAB_CLS)}>
      {/* The full path lives here rather than in a header line: the tab names
          the file, and hovering gives you where it is. */}
      <span className="truncate" title={path}>
        {artifactName(path)}
      </span>
      {/* A span, not a button: TabsTrigger already renders a button and nesting one
          inside it is invalid HTML. `onPointerDown` has to stop there too, because
          Radix activates a tab on pointer down, so a click on the close control would
          otherwise select the tab on the way to closing it. */}
      <span
        role="button"
        tabIndex={-1}
        aria-label={`Close ${artifactName(path)}`}
        title="Close this tab. The document is kept."
        onPointerDown={(e) => {
          e.stopPropagation();
          e.preventDefault();
        }}
        onClick={(e) => {
          e.stopPropagation();
          close(path);
        }}
        className={cn(
          "flex-shrink-0 rounded p-0.5 text-muted-foreground transition-opacity hover:bg-panel-2 hover:text-foreground",
          // Shown on the selected tab and on hover, the way a browser does it, so the
          // strip is not a row of close buttons competing with the filenames.
          active === path ? "opacity-100" : "opacity-0 group-hover:opacity-100",
        )}
      >
        <IconX className="size-3" />
      </span>
    </TabsTrigger>
  );

  return (
    <Tabs value={active} onValueChange={setActive} className="flex min-h-0 flex-1 flex-col gap-0">
      {/* Tabs and the download share ONE row. The button had a row to itself under the
          tabs, which doubled the header's height for a single control. */}
      <div className="mx-4 mt-3 flex items-center gap-3 print:hidden">
        <TabsList className="w-fit min-w-0 flex-shrink overflow-x-auto">
          {hasWidgets && (
            <TabsTrigger value={CANVAS_TAB} className={TAB_CLS}>
              Dashboard
            </TabsTrigger>
          )}
          {groups.map((group, index) =>
            group.folder ? (
              <span
                key={`${group.folder}-${index}`}
                className="flex items-center gap-1 rounded-md pb-0.5"
                style={{ boxShadow: `inset 0 -2px 0 0 ${bandColor(group.folder)}` }}
              >
                {/* The label is the group's control, as it is in a browser: clicking it
                    folds the group's tabs away and clicking it again brings them back.
                    Collapsed, it carries the count so the documents are visibly still
                    there rather than gone. */}
                <span
                  role="button"
                  tabIndex={0}
                  aria-expanded={!collapsed.has(group.folder)}
                  onClick={() => toggleGroup(group.folder)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      toggleGroup(group.folder);
                    }
                  }}
                  className="ml-1 max-w-28 flex-shrink-0 cursor-pointer truncate rounded px-1.5 py-0.5 text-[11px] font-medium text-white"
                  style={{ backgroundColor: bandColor(group.folder) }}
                  title={
                    collapsed.has(group.folder)
                      ? `Show the ${group.paths.length} documents in ${group.folder}`
                      : `Hide the documents in ${group.folder}`
                  }
                >
                  {collapsed.has(group.folder)
                    ? `${group.folder} (${group.paths.length})`
                    : group.folder}
                </span>
                {!collapsed.has(group.folder) && group.paths.map(tab)}
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
      {visible.map((path) => (
        // Only tabs that are on screen get a panel: a document whose group is folded
        // away must not be the thing the pane is showing. forceMount would also re-run
        // every artifact's scripts on every render, and artifacts are cheap to remount.
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
