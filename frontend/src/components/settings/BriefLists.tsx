/**
 * The two list shapes a presenter brief is rendered with.
 *
 * Extracted so the post-setup popup (DemoBriefDialog) and the reopenable About panel
 * render the SAME brief identically. They both show `demo_brief` / `demo_flow`, and two
 * private copies of the markup would have drifted the first time either was restyled.
 */

/** Brief points: what this assistant is and what to say about it. */
export function BulletList({ items }: { items: string[] }) {
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

/** Demo flow: ordered, because the order is the recommendation. */
export function NumberedList({ items }: { items: string[] }) {
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
