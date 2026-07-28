/**
 * The thumbs up/down feedback row shown under a completed answer. The thumb is
 * submitted immediately (POST /feedback); an optional comment can be added and
 * re-sent, reusing the returned feedback_id. Ported from `renderFeedback()`.
 */
import { useRef, useState } from "react";
import { IconThumbUp, IconThumbDown } from "@tabler/icons-react";
import { postFeedback } from "@/lib/api";

type StatusKind = "" | "ok" | "err";

export function FeedbackRow({ runId }: { runId: string }) {
  const [score, setScore] = useState<number | null>(null);
  const [showComment, setShowComment] = useState(false);
  const [comment, setComment] = useState("");
  const [status, setStatus] = useState<{ text: string; kind: StatusKind }>({
    text: "",
    kind: "",
  });
  const feedbackId = useRef<string | undefined>(undefined);
  const scoreRef = useRef<number | null>(null);

  const post = async (withComment: string) => {
    if (scoreRef.current === null) return;
    setStatus({ text: "…", kind: "" });
    const body = {
      run_id: runId,
      score: scoreRef.current,
      comment: withComment || "",
      ...(feedbackId.current ? { feedback_id: feedbackId.current } : {}),
    };
    try {
      const d = await postFeedback(body);
      if (d.ok) {
        if (d.feedback_id) feedbackId.current = d.feedback_id;
        setStatus({
          text: withComment ? "✓ Sent with comment" : "✓ Sent — add a comment?",
          kind: "ok",
        });
      } else {
        setStatus({ text: (d.error || "failed"), kind: "err" });
      }
    } catch (e) {
      setStatus({ text: (e as Error).message, kind: "err" });
    }
  };

  const pick = (val: number) => {
    setScore(val);
    scoreRef.current = val;
    setShowComment(true);
    post(comment); // submit the thumb immediately
  };

  const btn = (sel: boolean) =>
    "flex cursor-pointer items-center rounded-lg border bg-panel-2 px-2 py-1 " +
    (sel ? "border-brand bg-brand/15 text-brand" : "border-border text-muted-foreground hover:border-brand hover:text-foreground");

  return (
    <div className="-mt-1 flex flex-wrap items-center gap-1.5 self-start text-[13px] text-muted-foreground">
      <span className="mr-0.5">Was this helpful?</span>
      <button type="button" aria-label="Helpful" className={btn(score === 1)} onClick={() => pick(1)}>
        <IconThumbUp size={15} />
      </button>
      <button type="button" aria-label="Not helpful" className={btn(score === 0)} onClick={() => pick(0)}>
        <IconThumbDown size={15} />
      </button>
      {status.text && (
        <span
          className={
            "text-xs " +
            (status.kind === "ok"
              ? "text-success"
              : status.kind === "err"
                ? "text-danger"
                : "")
          }
        >
          {status.text}
        </span>
      )}
      {showComment && (
        <div className="mt-1.5 flex w-full gap-1.5">
          <input
            className="flex-1 rounded-lg border border-border bg-panel-2 px-2.5 py-1.5 text-[13px] text-foreground"
            placeholder="Add a comment (optional), then Send…"
            value={comment}
            autoFocus
            onChange={(e) => setComment(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") post(comment);
            }}
          />
          <button
            type="button"
            className="cursor-pointer rounded-lg border-none bg-brand px-3 text-[13px] text-brand-foreground"
            onClick={() => post(comment)}
          >
            Send
          </button>
        </div>
      )}
    </div>
  );
}
