/**
 * Owns one voice conversation's lifecycle and the state a UI needs to draw it.
 *
 * Extracted from the button because two surfaces now render the same session: the
 * immersive stage (VoiceStage) and the compact header control. Both read this; neither
 * owns it, so exiting the stage does not end the conversation.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type { ChatPanelHandle } from "@/components/ChatPanel";
import { ensureThread, getThreadState, voiceToken, voiceTrace } from "@/lib/api";
import {
  conversationDigest,
  INVOKE_TOOL,
  VoiceSession,
  type VoicePersona,
  type VoiceState,
} from "@/lib/voice";

export interface VoiceSessionOptions {
  chat: React.RefObject<ChatPanelHandle | null>;
  workspace?: string;
  project?: string;
  customer?: string;
  /** Prebuilt voice name from `metadata.voice.voice_name`; falls back to the house voice. */
  voiceName?: string;
  /**
   * Who the assistant is, for the shell's system instruction. Without it the voice
   * introduces itself as a generic analytics assistant and cannot say who it works for.
   */
  persona?: VoicePersona;
}

export interface VoiceSessionView {
  state: VoiceState;
  /** One line on what the agent is doing right now ("searching the data now"). */
  activity: string;
  /** True while the model is talking, for the orb's animation. */
  speaking: boolean;
  /**
   * How many turns the USER has finished. A counter rather than a boolean because it is
   * consumed as an event ("a turn just ended") - the teleprompter collapses on it, since
   * once the question has been read the card is in the way.
   */
  userTurns: number;
  /**
   * Live loudness per side, 0..1. A REF and not state on purpose: it updates dozens of
   * times a second, so the orb reads it from an animation frame instead of re-rendering.
   * Split by side because the halo moves in opposite directions for each (`haloTransform`).
   */
  level: React.MutableRefObject<{ user: number; model: number }>;
  error: string;
  running: boolean;
  start: () => void;
  stop: () => void;
}

/** Bytes to base64, chunked so a long recording cannot blow the argument limit. */
function toBase64(bytes: Uint8Array): string {
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(binary);
}

export function useVoiceSession(opts: VoiceSessionOptions): VoiceSessionView {
  const { chat, workspace, project, customer, voiceName, persona } = opts;
  const [state, setState] = useState<VoiceState>("idle");
  const [activity, setActivity] = useState("");
  const [speaking, setSpeaking] = useState(false);
  const [userTurns, setUserTurns] = useState(0);
  const [error, setError] = useState("");
  const session = useRef<VoiceSession | null>(null);
  const level = useRef({ user: 0, model: 0 });
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
    // Rendered BEFORE stop() tears the session down, and sent with the closing call so the
    // trace ends up with a scrubbable timeline of the conversation.
    const wav = session.current?.recording() || null;
    session.current?.stop();
    session.current = null;
    if (traceId.current) {
      void voiceTrace({
        action: "end",
        session_id: traceId.current,
        audio_wav: wav ? toBase64(wav) : "",
      });
      traceId.current = "";
    }
    setActivity("");
    setSpeaking(false);
    level.current = { user: 0, model: 0 };
    setState("idle");
  }, []);

  const start = useCallback(() => {
    void (async () => {
      setError("");
      setState("connecting");
      try {
        // The conversation's root span. Best-effort: a blank id means "not traced", and
        // the conversation proceeds either way.
        //
        // `thread_id` is what puts the run in a THREAD, which is where LangSmith renders a
        // conversation as turns with an audio player rather than as a lone run with a file
        // attached. It is the SAME thread the agent runs on, so the spoken conversation and
        // the agent's turns are one thing in the UI. `ensureThread` mints it now if the
        // first question has not been asked yet.
        const threadId = await ensureThread().catch(() => "");

        // What has already been said on this thread, so joining mid-conversation is not a
        // cold start for the shell. Best-effort and non-blocking to the point of being
        // skippable: a session that starts without it is merely forgetful, and failing to
        // start because the history could not be read would be worse.
        let history: string[] = [];
        try {
          const state = await getThreadState(threadId);
          history = conversationDigest(state?.values?.messages ?? []);
        } catch {
          /* a fresh thread has no state yet, which is the common case */
        }
        const started = await voiceTrace({
          action: "session",
          workspace,
          project,
          metadata: { customer: customer || "", thread_id: threadId },
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
            // The text itself is not rendered - the stage shows status, not a caption - but
            // the BOUNDARY matters: a finished user turn is what collapses the teleprompter.
            if (role === "user") setUserTurns((n) => n + 1);
            if (traceId.current) {
              void voiceTrace({ action: "utterance", session_id: traceId.current, role, text });
            }
          },
          onState: setState,
          onActivity: setActivity,
          onSpeaking: setSpeaking,
          onLevel: (v, from) => {
            level.current[from] = v;
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
        }, voiceName, { ...persona, history });
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
  }, [chat, workspace, project, customer, voiceName, persona]);

  return {
    state,
    activity,
    speaking,
    userTurns,
    level,
    error,
    running: state !== "idle" && state !== "error",
    start,
    stop,
  };
}
