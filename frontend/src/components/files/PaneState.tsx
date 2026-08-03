/**
 * Centered icon + copy (+ optional action) — the shared "this pane has no
 * content right now" block used by every empty/loading/error state in the file
 * browser, so no pane is ever a blank rectangle.
 */
export function PaneState({
  icon,
  title,
  detail,
  action,
}: {
  icon: React.ReactNode;
  title: string;
  detail?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-2.5 p-8 text-center">
      <span className="opacity-40">{icon}</span>
      <p className="m-0 text-sm text-foreground">{title}</p>
      {detail ? (
        <p className="m-0 max-w-md text-xs text-muted-foreground">{detail}</p>
      ) : null}
      {action}
    </div>
  );
}
