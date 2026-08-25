/**
 * The voice-mode control: one button that opens a Gemini Live conversation in front of
 * the existing agent.
 *
 * It owns the session lifecycle and nothing else. Every question it hears is run through
 * the chat panel's `ask` handle, which is the same path the composer uses, so the
 * dashboard, chips, transcript and trace are produced by code that already existed. See
 * lib/voice.ts for the protocol half and dashboard_agent/voice.py for the token.
 */

import { useEffect, useRef, useState } from "react";
import { IconMicrophone, IconMicrophoneOff, IconLoader2 } from "@tabler/icons-react";
import type { ChatPanelHandle } from "@/components/ChatPanel";
import { Button } from "@/components/motion/button";
import { voiceToken, voiceTrace } from "@/lib/api";
import { INVOKE_TOOL, VoiceSession, type VoiceState } from "@/lib/voice";

export interface VoiceButtonProps {
  /** The chat panel to drive. */
  chat: React.RefObject<ChatPanelHandle | null>;
  /** Workspace and project the conversation's trace belongs in. */
  workspace?: string;
  project?: string;
  /** Customer name, for the trace's metadata. */
  customer?: string;
  /** A spoken line for the chat transcript, so what was said is visible on screen. */
  onTranscript?: (role: "user" | "model", text: string) => void;
}

const LABEL: Record<VoiceState, string> = {
  idle: "Talk to the assistant",
  connecting: "Connecting…",
  listening: "Listening, tap to stop",
  thinking: "Working on it",
  error: "Voice failed, tap to retry",
};

export function VoiceButton({
  chat,
  workspace,
  project,
  customer,
  onTranscript,
}: VoiceButtonProps) {
  const [state, setState] = useState<VoiceState>("idle");
  const [error, setError] = useState("");
  const session = useRef<VoiceSession | null>(null);
  const traceId = useRef("");

  // A live microphone and an open socket must not outlive the page.
  useEffect(() => {
    return () => {
      session.current?.stop();
      if (traceId.current) void voiceTrace({ action: "end", session_id: traceId.current });
    };
  }, []);

  const stop = () => {
    session.current?.stop();
    session.current = null;
    if (traceId.current) {
      void voiceTrace({ action: "end", session_id: traceId.current });
      traceId.current = "";
    }
    setState("idle");
  };

  const start = async () => {
    setError("");
    setState("connecting");
    try {
      // The conversation's root span. Best-effort: a blank id means "not traced", and
      // the conversation proceeds either way.
      const started = await voiceTrace({
        action: "session",
        workspace,
        project,
        metadata: { customer: customer || "" },
      });
      traceId.current = String(started.session_id || "");

      const live = new VoiceSession({
        // Minted by the session, once the mic is live: a token only opens a session for
        // about a minute, and the permission dialog can eat all of it.
        getToken: async () => {
          const { token, model } = await voiceToken();
          return { token, model };
        },
        ask: async (question, headers, onProgress) => {
          const out = await chat.current?.ask(question, headers, onProgress);
          return {
            answer: out?.answer || "",
            widgets: (out?.widgets || []) as { title?: string; value?: string }[],
            approval: out?.approval,
            runId: out?.runId,
          };
        },
        resume: async (choice) => {
          const out = await chat.current?.resumeWith(choice);
          return {
            answer: out?.answer || "",
            widgets: (out?.widgets || []) as { title?: string; value?: string }[],
          };
        },
        onTranscript: (role, text) => {
          onTranscript?.(role, text);
          if (traceId.current) {
            void voiceTrace({ action: "utterance", session_id: traceId.current, role, text });
          }
        },
        onState: setState,
        // Shown on the button and logged: the server's reason is usually the whole
        // answer (a wrong RPC or a stale token both read as "the socket just closed").
        onError: (detail) => {
          console.error("[voice]", detail);
          setError(detail);
        },
        openToolSpan: async (question) => {
          if (!traceId.current) return {};
          const span = await voiceTrace({
            action: "tool",
            session_id: traceId.current,
            name: INVOKE_TOOL,
            inputs: { question },
          });
          return span as { tool_id?: string; headers?: Record<string, string> };
        },
        closeToolSpan: (toolId, outputs) => {
          void voiceTrace({
            action: "tool_end",
            session_id: traceId.current,
            tool_id: toolId,
            outputs,
          });
        },
      });
      session.current = live;
      await live.start();
    } catch (e) {
      // The common cases are a denied microphone and an unset GEMINI_API_KEY, and both
      // deserve the real message rather than a shrug.
      setError((e as Error).message);
      setState("error");
      session.current?.stop();
      session.current = null;
    }
  };

  const running = state !== "idle" && state !== "error";
  const Icon = state === "connecting" || state === "thinking" ? IconLoader2 : IconMicrophone;

  return (
    <div className="flex items-center gap-2">
      {/* The reason a session died is often the entire answer, and a `title` tooltip
          truncates it. Rendered inline so it can be read and copied. */}
      <Button
        type="button"
        variant={running ? "primary" : "outline"}
        size="sm"
        title={error || LABEL[state]}
        aria-label={LABEL[state]}
        onClick={() => (running ? stop() : void start())}
        className="gap-1.5 rounded-full"
      >
        {running ? (
          <Icon className={state === "listening" ? "size-4" : "size-4 animate-spin"} />
        ) : (
          <IconMicrophoneOff className="size-4" />
        )}
        <span className="text-xs">{LABEL[state]}</span>
      </Button>
      {state === "error" && error && (
        <span className="max-w-[28rem] text-[11px] leading-snug text-red-500 select-text">
          {error}
        </span>
      )}
    </div>
  );
}
