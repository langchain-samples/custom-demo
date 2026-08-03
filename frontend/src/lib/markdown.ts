/**
 * The app's markdown typography, in one place.
 *
 * Streamdown emits bare `<p>`/`<h2>`/`<table>`/… (its own classes are compiled
 * via the `@source` line in index.css), so every surface that renders markdown
 * has to supply element styling itself. Both surfaces that do — the chat
 * answer bubble and the sandbox file viewer — must agree, or a README reads one
 * way in the transcript and another way in the file browser.
 *
 * Consumers add their own chrome (the bubble's `rounded-xl bg-panel-2 …`); this
 * constant is only the type.
 */
export const PROSE_CLS =
  "text-sm leading-relaxed text-foreground" +
  " [&_p]:my-1.5 [&_ul]:my-1.5 [&_ul]:list-disc [&_ul]:pl-5 [&_ol]:my-1.5 [&_ol]:list-decimal [&_ol]:pl-5 [&_li]:my-0.5" +
  " [&_h1]:mb-1 [&_h1]:mt-2 [&_h1]:text-base [&_h1]:font-bold [&_h2]:mb-1 [&_h2]:mt-2 [&_h2]:text-[15px] [&_h2]:font-bold [&_h3]:mb-1 [&_h3]:mt-2 [&_h3]:font-semibold" +
  " [&_strong]:font-semibold [&_a]:text-[color:var(--brand-label)] [&_a]:underline" +
  " [&_code]:rounded [&_code]:bg-background [&_code]:px-1 [&_code]:py-px [&_code]:font-mono [&_code]:text-[12px]" +
  " [&_table]:my-2 [&_table]:w-full [&_table]:border-collapse [&_table]:text-xs" +
  " [&_th]:border-b [&_th]:border-border [&_th]:px-2 [&_th]:py-1 [&_th]:text-left [&_th]:font-semibold" +
  " [&_td]:border-b [&_td]:border-border/50 [&_td]:px-2 [&_td]:py-1";
