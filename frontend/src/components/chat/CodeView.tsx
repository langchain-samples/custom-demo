/**
 * A syntax-highlighted, scrollable code block for tool chips (the `eval` script /
 * `execute` command). Highlights with Shiki off the main render (async), showing
 * the plain source until it resolves, and copies the raw source on click. Heredoc
 * bodies are highlighted in the interpreter's language (see lib/highlight).
 */
import { useEffect, useRef, useState } from "react";
import { IconCheck, IconCopy } from "@tabler/icons-react";
import { highlightSegments } from "@/lib/highlight";

export function CodeView({ code, lang }: { code: string; lang: string }) {
  const [html, setHtml] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const timer = useRef<number | null>(null);

  useEffect(() => {
    let cancelled = false;
    highlightSegments(code, lang)
      .then((h) => {
        if (!cancelled) setHtml(h);
      })
      .catch(() => {
        if (!cancelled) setHtml(null); // Shiki failed to load → keep the plain fallback
      });
    return () => {
      cancelled = true;
    };
  }, [code, lang]);

  const copy = () => {
    void navigator.clipboard?.writeText(code);
    setCopied(true);
    if (timer.current) window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setCopied(false), 1200);
  };

  return (
    <div
      className="relative max-h-72 min-w-0 overflow-auto rounded-md border border-border"
      style={{ backgroundColor: "#0d1117" }}
    >
      <button
        type="button"
        onClick={copy}
        title="Copy"
        className="absolute right-1.5 top-1.5 z-10 rounded p-1 text-muted-foreground/70 hover:bg-white/10 hover:text-foreground"
      >
        {copied ? <IconCheck size={13} /> : <IconCopy size={13} />}
      </button>
      <pre className="m-0 p-2.5 text-[11px] leading-normal">
        {html !== null ? (
          // Shiki output: escaped token spans with inline colors (safe to inject).
          // Lines are separated by real newlines inside the <pre>, so the `.line`
          // spans must stay inline — forcing them to `display:block` double-spaced
          // every line (the block break PLUS the newline).
          <code dangerouslySetInnerHTML={{ __html: html }} />
        ) : (
          <code className="whitespace-pre text-muted-foreground">{code}</code>
        )}
      </pre>
    </div>
  );
}
