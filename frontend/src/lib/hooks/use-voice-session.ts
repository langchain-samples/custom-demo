/**
 * Owns one voice conversation's lifecycle and the state a UI needs to draw it.
 *
 * Extracted from the button because two surfaces now render the same session: the
 * immersive stage (VoiceStage) and the compact header control. Both read this; neither
 * owns it, so exiting the stage does not end the conversation.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type { ChatPanelHandle } from "@/components/ChatPanel";
import { voiceToken, voiceTrace } from "@/lib/api";
import { INVOKE_TOOL, VoiceSession, type VoiceState } from "@/lib/voice";

export interface VoiceSessionOptions {
  chat: React.RefObject<ChatPanelHandle | null>;
  workspace?: string;
  project?: string;
  customer?: string;
  /** Prebuilt voice name from `metadata.voice.voice_name`; falls back to the house voice. */
  voiceName?: string;
}

export interface VoiceSessionView {
  state: VoiceState;
  /** One line on what the agent is doing right now ("searching the data now"). */
  activity: string;
  /** True while the model is talking, for the orb's animation. */
  speaking: boolean;
  /**
   * Live loudness, 0..1. A REF and not state on purpose: it updates dozens of times a
   * second, so the orb reads it from an animation frame instead of re-rendering.
   */
  level: React.MutableRefObject<number>;
  /** The last thing either side said, for a caption under the orb. */
  lastSaid: { role: "user" | "model"; text: string } | null;
  error: string;
  running: boolean;
  start: () => void;
  stop: () => void;
}

export function useVoiceSession(opts: VoiceSessionOptions): VoiceSessionView {
  const { chat, workspace, project, customer, voiceName } = opts;
  const [state, setState] = useState<VoiceState>("idle");
  const [activity, setActivity] = useState("");
  const [speaking, setSpeaking] = useState(false);
  const [lastSaid, setLastSaid] = useState<VoiceSessionView["lastSaid"]>(null);
  const [error, setError] = useState("");
  const session = useRef<VoiceSession | null>(null);
  const level = useRef(0);
  const traceId = useRef("");

  // A live microphone and an open socket must not outlive the page.
  useEffect(
    () => () => {
      session.current?.stop();
      if (traceId.current) void voiceTrace({ action: "end", session_id: traceId.current });
    },
    [],
  );

  const stop = useCallback(() => {
    session.current?.stop();
    session.current = null;
    if (traceId.current) {
      void voiceTrace({ action: "end", session_id: traceId.current });
      traceId.current = "";
    }
    setActivity("");
    setSpeaking(false);
    level.current = 0;
    setState("idle");
  }, []);

  const start = useCallback(() => {
    void (async () => {
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
            setLastSaid({ role, text });
            if (traceId.current) {
              void voiceTrace({ action: "utterance", session_id: traceId.current, role, text });
            }
          },
          onState: setState,
          onActivity: setActivity,
          onSpeaking: setSpeaking,
          onLevel: (v) => {
            level.current = v;
          },
          onError: (detail) => {
            // Logged as well as shown: the server's reason is usually the whole answer.
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
        }, voiceName);
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
    })();
  }, [chat, workspace, project, customer, voiceName]);

  return {
    state,
    activity,
    speaking,
    level,
    lastSaid,
    error,
    running: state !== "idle" && state !== "error",
    start,
    stop,
  };
}
