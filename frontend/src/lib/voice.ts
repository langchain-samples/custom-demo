/**
 * Voice mode: the Gemini Live shell that sits in FRONT of the deep agent.
 *
 * The division of labour, which is the whole design:
 *
 *   Gemini Live  owns the conversation - mic, voice activity detection, barge-in,
 *                speech out - and has exactly TWO capabilities, both implemented here
 *                in the browser: `invoke_deep_agent` and `resume_deep_agent`.
 *   The SPA      implements those calls by starting a NORMAL streaming run (the same
 *                `runStream` a typed question uses), so the dashboard, the chat
 *                transcript, the subagent cards and the LangSmith trace all come from
 *                the code path that already exists. The agent is not voice-aware.
 *
 * WHY THE BROWSER AND NOT THE SERVER. The dashboard is built client-side from
 * `push_widget` args as they stream (see ChatPanel). Run the tool server-side and the
 * agent works perfectly while the canvas stays blank, which is the demo's whole payoff.
 * Connecting straight to Google also keeps audio off our deployment (no WebSocket to
 * proxy) and is the lower-latency shape Google documents. The API key never reaches the
 * browser: it gets a short-lived token from POST /voice/token (see voice.py).
 *
 * Reference: langchain-ai/google-adk-realtime-deepagents-example does the same
 * split server-side with Google ADK. We diverge on WHERE the tool runs, for the
 * widget reason above; the audio worklets in public/ are adapted from it.
 */

// Relative AND with the extension: the Node tests import this module directly via type
// stripping, where neither the `@/` alias nor extensionless resolution works. Vite is
// happy with the explicit form, so it is the one that works in both.
import { ConversationRecorder } from "./voiceRecorder.ts";

const LIVE_HOST = "generativelanguage.googleapis.com";
// The CONSTRAINED method, and it has to be. Verified against the live API: an ephemeral
// token on the plain `BidiGenerateContent` is refused with `1008 Method doesn't allow
// unregistered callers`, which arrives as a socket that opens and closes again - no error
// frame, nothing in the network tab that names the cause.
const LIVE_PATH =
  "/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContentConstrained";

/**
 * The house voice, used unless an assistant names another.
 *
 * "Even" in Google's own one-word descriptors, which is what an insurance or analytics
 * assistant wants: the upbeat ones (Puck, Laomedeia) read as breezy when someone is asking
 * about a theft claim. Per-assistant override lives in `metadata.voice.voice_name`.
 */
export const DEFAULT_VOICE = "Schedar";

/** Live API audio rates. Both fixed by the API: 16k in, 24k out. */
export const MIC_RATE = 16000;
export const PLAYBACK_RATE = 24000;

/** Never narrate progress more often than this: thinking aloud, not a monologue. */
const PROGRESS_MIN_GAP_MS = 6000;

/** How long after the last audio chunk we still consider the model to be speaking. */
const SPEAKING_IDLE_MS = 700;

/** What the shell is allowed to ask the SPA to do. */
export const INVOKE_TOOL = "invoke_deep_agent";
export const RESUME_TOOL = "resume_deep_agent";

/** Who the shell is, so it can answer "who are you?" without guessing. */
export interface VoicePersona {
  /** The assistant's name, e.g. "Progressive GPT". */
  displayName?: string;
  /** The customer it belongs to, e.g. "Progressive". */
  customer?: string;
  /** Their industry, e.g. "Insurance". */
  industry?: string;
  /**
   * What people ask it about - the quick-action labels ("Claimant: Claim status"), which
   * carry both the personas it serves and their topics in a few words each.
   */
  topics?: string[];
}

/**
 * Spoken-delivery instructions for the shell. NOT the agent's prompt, which never learns
 * that voice exists.
 *
 * The IDENTITY half matters more than it looks. The deep agent's prompt is customer-
 * specific, but this model is the one doing the talking, so without it the assistant
 * introduces itself as a generic "analytics assistant" and cannot say who it works for or
 * what it covers - which is exactly the wrong first impression in a customer demo.
 *
 * Kept short deliberately: this is a realtime speech model, and a long system instruction
 * costs latency and adherence.
 */
export function voiceInstructions(persona: VoicePersona = {}): string {
  const name = persona.displayName || "a live analytics assistant";
  const who = persona.customer
    ? `You are ${name}, the voice of ${persona.customer}'s${
        persona.industry ? ` ${persona.industry.toLowerCase()}` : ""
      } assistant. You work for ${persona.customer} and you talk to their people about their own data.`
    : `You are ${name}.`;
  const covers = persona.topics?.length
    ? `\n\nWhat people ask you about: ${persona.topics.slice(0, 6).join("; ")}.`
    : "";

  return `${who}${covers}

You are being HEARD, not read: keep replies to a sentence or two, never speak markdown, and
never read out a list of numbers.

You have one real capability: the \`${INVOKE_TOOL}\` tool, which asks the analytics agent a
question. Use it for anything about the customer's data, metrics, accounts, orders or
reports. Pass the user's question through in full. Answer directly only for small talk and
for who or what you are.

\`${INVOKE_TOOL}\` is slow (up to a minute) and answers in two parts. So:
- Say a short acknowledgement when you call it ("Let me pull that up"), then stop and let the
  user talk. Do NOT go silent waiting for it.
- The findings arrive later as a follow-up result. Weave them in conversationally and
  reconnect them to what was asked.
- The figures are ON SCREEN in the dashboard. Summarise what they mean, do not recite them:
  say "it's on the dashboard now" instead.
- If a result says approval is needed, read out the options and, once the user picks one,
  call \`${RESUME_TOOL}\` with their choice.`;
}

/** One function call the model asked us to run. */
export interface LiveToolCall {
  id: string;
  name: string;
  args: Record<string, unknown>;
}

/** What we hand back. `scheduling` says when the model should surface it. */
export type ResponseScheduling = "INTERRUPT" | "WHEN_IDLE" | "SILENT";

/* -------------------------- pure helpers (unit-tested) -------------------------- */

/**
 * Float32 mic samples (-1..1) to little-endian 16-bit PCM, the only format the Live
 * API accepts. Clamped: Web Audio can hand back values slightly outside the range, and
 * letting those wrap produces a loud click rather than a quiet one.
 */
export function floatToPcm16(samples: Float32Array): ArrayBuffer {
  const out = new DataView(new ArrayBuffer(samples.length * 2));
  for (let i = 0; i < samples.length; i++) {
    const clamped = Math.max(-1, Math.min(1, samples[i]));
    out.setInt16(i * 2, Math.round(clamped * 0x7fff), true);
  }
  return out.buffer;
}

/** 16-bit PCM back to Float32, for the playback worklet. */
export function pcm16ToFloat(buffer: ArrayBuffer): Float32Array {
  const view = new DataView(buffer);
  const out = new Float32Array(Math.floor(buffer.byteLength / 2));
  for (let i = 0; i < out.length; i++) out[i] = view.getInt16(i * 2, true) / 0x8000;
  return out;
}

/**
 * Loudness of one audio frame, 0..1 (RMS, lightly boosted).
 *
 * Speech RMS sits low - a normal voice lands around 0.05-0.15 - so the raw value barely
 * moves a visual. The boost maps that useful band onto most of the range without letting a
 * loud moment clip past 1.
 */
export function frameLevel(samples: Float32Array): number {
  if (!samples.length) return 0;
  let sum = 0;
  for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i];
  return Math.min(1, Math.sqrt(sum / samples.length) * 4);
}

/** Baselines while the model talks or nobody does. */
const BASE = 1;
/** Baselines while the USER talks: the orb visibly makes room for them. */
const USER_HALO_BASE = 0.8;
const USER_SPHERE_BASE = 0.9;
/** How far each layer travels at full volume. */
const HALO_OUT = 0.3;
const HALO_IN = 0.18;
const SPHERE_OUT = 0.12;
const SPHERE_IN = 0.1;

/**
 * Where the orb sits, given how loud each side is.
 *
 * BOTH layers move, and in opposite directions per side. While the model speaks the sphere
 * and halo swell OUTWARD, which reads as projecting. While the user speaks both draw
 * INWARD from a smaller baseline, which reads as listening - leaning in rather than talking
 * over. Whoever is louder wins, so a moment of crosstalk resolves rather than fighting.
 *
 * Pure, because "what the orb does" is the design and it should not be something you can
 * only check by talking to it.
 */
export function orbTransform(
  userLevel: number,
  modelLevel: number,
): {
  halo: { scale: number; opacity: number };
  sphere: { scale: number };
  speaker: "user" | "model" | "idle";
} {
  const user = Math.max(0, Math.min(1, userLevel));
  const model = Math.max(0, Math.min(1, modelLevel));
  const speaker = user > model ? "user" : model > 0.01 ? "model" : "idle";
  if (speaker === "user") {
    return {
      // Never all the way to nothing: an orb that vanishes reads as broken, not attentive.
      halo: { scale: Math.max(0.55, USER_HALO_BASE - HALO_IN * user), opacity: 0.3 + 0.5 * user },
      sphere: { scale: Math.max(0.7, USER_SPHERE_BASE - SPHERE_IN * user) },
      speaker,
    };
  }
  return {
    halo: { scale: BASE + HALO_OUT * model, opacity: 0.22 + 0.7 * model },
    sphere: { scale: BASE + SPHERE_OUT * model },
    speaker,
  };
}

/** Base64 to bytes (the Live API sends audio as base64 inline data). *//** Base64 to bytes (the Live API sends audio as base64 inline data). */
export function decodeBase64(b64: string): ArrayBuffer {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
  return bytes.buffer;
}

/** Bytes to base64, chunked so a long utterance cannot blow the argument limit. */
export function encodeBase64(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let bin = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  }
  return btoa(bin);
}

/**
 * Does this model document `behavior: NON_BLOCKING` on a function declaration?
 *
 * Only the 2.5 Live models do; 3.1's function calling is "synchronous only" per Google's
 * tool-use docs. It is sent where it is supported and omitted elsewhere, because a field
 * a model does not know is a risk for no gain: what ACTUALLY keeps the conversation
 * moving during a long run is the two-phase response in `runTool` (acknowledge
 * immediately, deliver the real answer later), which needs no model support at all. That
 * is why the ADK example runs happily on 3.1.
 */
export function supportsNonBlocking(model: string): boolean {
  return model.includes("2.5");
}

/**
 * Resample a mic frame to the 16k the Live API wants, averaging rather than picking.
 *
 * Taking every Nth sample (the one-liner) aliases everything above 8kHz down into the
 * speech band, which is audible as harshness and makes recognition worse for no reason.
 * A box average over each output bin is three lines and much closer to right.
 */
export function downsampleTo16k(frame: Float32Array, inRate: number): Float32Array {
  if (inRate <= MIC_RATE) return frame;
  const ratio = inRate / MIC_RATE;
  const out = new Float32Array(Math.floor(frame.length / ratio));
  for (let i = 0; i < out.length; i++) {
    const start = Math.floor(i * ratio);
    const end = Math.min(frame.length, Math.floor((i + 1) * ratio));
    let sum = 0;
    for (let j = start; j < end; j++) sum += frame[j];
    out[i] = end > start ? sum / (end - start) : 0;
  }
  return out;
}

/**
 * One frame of mic audio, as a `realtimeInput`.
 *
 * `audio`, NOT `mediaChunks`: the latter is what most examples still show and the server
 * rejects it outright - `1007 realtime_input.media_chunks is deprecated. Use audio, video,
 * or text instead` - which kills the session on the first frame the mic produces, i.e.
 * only ever in front of a real microphone.
 */
export function realtimeAudioMessage(pcm: ArrayBuffer): object {
  return {
    realtimeInput: {
      audio: { mimeType: `audio/pcm;rate=${MIC_RATE}`, data: encodeBase64(pcm) },
    },
  };
}

/**
 * The `setup` message that opens a session.
 *
 * Only the model and the tool declarations are ours to choose here: everything else was
 * pinned into the ephemeral token server-side, and a client that contradicts the pinned
 * config is refused.
 */
export function setupMessage(
  model: string,
  resumeHandle?: string,
  voice = "",
  persona: VoicePersona = {},
): object {
  const behavior = supportsNonBlocking(model) ? { behavior: "NON_BLOCKING" } : {};
  // `||`, not a default parameter: an assistant with no voice saved yet carries "" rather
  // than undefined, and "" is not a voice the API accepts.
  const voiceName = voice || DEFAULT_VOICE;
  return {
    setup: {
      model: `models/${model}`,
      // Audio out, and it must be stated: a Live session opened without an explicit
      // modality closes with `1011 Internal error encountered` - no error frame, no hint.
      // `speechConfig` belongs in HERE and not on `setup`, where it is rejected with
      // `1007 Unknown name "speechConfig" at 'setup'`.
      generationConfig: {
        responseModalities: ["AUDIO"],
        speechConfig: { voiceConfig: { prebuiltVoiceConfig: { voiceName } } },
      },
      // The ONLY text this session produces. A native-audio model answers in audio, so
      // these transcripts are what the chat panel renders and what the trace records.
      inputAudioTranscription: {},
      outputAudioTranscription: {},
      systemInstruction: { parts: [{ text: voiceInstructions(persona) }] },
      tools: [
        {
          functionDeclarations: [
            {
              name: INVOKE_TOOL,
              ...behavior,
              description:
                "Ask the analytics agent a question about the customer's data. Slow " +
                "(up to a minute) and answers on the dashboard as well as in speech.",
              parameters: {
                type: "OBJECT",
                properties: {
                  question: {
                    type: "STRING",
                    description: "The user's question, passed through in full.",
                  },
                },
                required: ["question"],
              },
            },
            {
              name: RESUME_TOOL,
              ...behavior,
              description:
                "Answer the approval question a previous invoke_deep_agent result " +
                "reported, using one of the options it listed.",
              parameters: {
                type: "OBJECT",
                properties: {
                  choice: {
                    type: "STRING",
                    description: "Exactly one of the options the previous result listed.",
                  },
                },
                required: ["choice"],
              },
            },
          ],
        },
      ],
      // Resumption is not a nicety: a Live connection lasts ~10 minutes and a demo
      // does not, so every session opens ready to be resumed.
      sessionResumption: resumeHandle ? { handle: resumeHandle } : {},
    },
  };
}

/**
 * The `toolResponse` frame that ACKNOWLEDGES a call, so the model stops waiting.
 *
 * `willContinue` is what makes this an acknowledgement rather than an answer: it tells the
 * model the real result is still coming, which is how a later `clientContent` response for
 * the same call id is accepted instead of ignored. `scheduling` sits BESIDE `response`, not
 * inside it (it is a field of `FunctionResponse` - cf. the ADK example's
 * `types.FunctionResponse(..., response={...}, scheduling=...)`).
 */
export function toolResponseMessage(
  call: LiveToolCall,
  response: Record<string, unknown>,
  scheduling: ResponseScheduling,
  willContinue = false,
): object {
  return {
    toolResponse: {
      functionResponses: [
        {
          id: call.id,
          name: call.name,
          response,
          scheduling,
          ...(willContinue ? { willContinue: true } : {}),
        },
      ],
    },
  };
}

/**
 * How the late half of a tool call gets DELIVERED: as a plain user turn.
 *
 * Not as a second `toolResponse` (the call is already answered, so it is dropped), and not
 * as a `clientContent` turn carrying a `functionResponse` part either - that is what the
 * ADK example sends, and verified against a live 3.1 session it is silently ignored: the
 * model acknowledges, the result arrives, and it never speaks again. A user turn IS
 * narrated, every time.
 *
 * The `[system]` prefix is doing real work: without it the model answers the text as if the
 * user had said it ("thanks for telling me"), rather than reporting it back.
 */
export function systemTurnMessage(text: string): object {
  return {
    clientContent: {
      turns: [{ role: "user", parts: [{ text: `[system] ${text}` }] }],
      turnComplete: true,
    },
  };
}

/** The finished answer, phrased so the model reports rather than re-answers. */
export function resultMessage(answer: string, onScreen: string[]): object {
  const screen = onScreen.length ? ` On the dashboard: ${onScreen.join("; ")}.` : "";
  return systemTurnMessage(
    `result for the question you are working on: ${answer}${screen} ` +
      "Report this back conversationally in a sentence or two.",
  );
}

/** Progress, so a 60-second run is narrated rather than silent. */
export function progressMessage(what: string): object {
  return systemTurnMessage(
    `progress on the question you are working on: ${what}. ` +
      "Say one short line about it, do not repeat yourself, and keep waiting.",
  );
}

/**
 * What the agent is doing right now, in words a listener understands.
 *
 * Driven by the agent's OWN tool calls rather than a canned timer, so "still searching the
 * data" is true when it is said. Unknown tools fall back to a vague line, which is better
 * than naming an internal tool out loud.
 */
const PROGRESS_LABELS: Record<string, string> = {
  datasearch: "searching the data now",
  web_search: "checking external sources",
  execute: "crunching the numbers in the sandbox",
  push_widget: "building the dashboard",
  write_todos: "planning out the steps",
  read_file: "reading through the files",
  ls: "looking through the files",
  glob: "looking through the files",
  grep: "searching the files",
  task: "handing part of this to a specialist",
};

export function progressLabel(toolName: string): string {
  return PROGRESS_LABELS[toolName] || "still working through it";
}

/** Function calls in a server frame, if any. */
export function toolCallsFrom(frame: unknown): LiveToolCall[] {
  const calls = (frame as { toolCall?: { functionCalls?: unknown[] } })?.toolCall
    ?.functionCalls;
  if (!Array.isArray(calls)) return [];
  return calls
    .map((c) => c as { id?: string; name?: string; args?: Record<string, unknown> })
    .filter((c) => !!c.name)
    .map((c) => ({ id: String(c.id || ""), name: String(c.name), args: c.args || {} }));
}

/** Base64 PCM chunks of model speech in a server frame. */
export function audioChunksFrom(frame: unknown): string[] {
  const parts = (
    frame as {
      serverContent?: { modelTurn?: { parts?: { inlineData?: { data?: string } }[] } };
    }
  )?.serverContent?.modelTurn?.parts;
  if (!Array.isArray(parts)) return [];
  return parts.map((p) => p?.inlineData?.data).filter((d): d is string => !!d);
}

/**
 * The transcripts in a server frame, which are the ONLY text a native-audio session
 * produces: the model answers in audio, so the chat panel's turns come from here.
 */
export function transcriptsFrom(frame: unknown): { input: string; output: string } {
  const sc = (
    frame as {
      serverContent?: {
        inputTranscription?: { text?: string };
        outputTranscription?: { text?: string };
      };
    }
  )?.serverContent;
  return {
    input: String(sc?.inputTranscription?.text || ""),
    output: String(sc?.outputTranscription?.text || ""),
  };
}

/** True when the user talked over the model, i.e. stop playback and flush the queue. */
export function wasInterrupted(frame: unknown): boolean {
  return !!(frame as { serverContent?: { interrupted?: boolean } })?.serverContent
    ?.interrupted;
}

/** A resumption handle, when the server offers a new one. */
export function resumptionHandle(frame: unknown): string {
  const update = (
    frame as { sessionResumptionUpdate?: { resumable?: boolean; newHandle?: string } }
  )?.sessionResumptionUpdate;
  return update?.resumable && update?.newHandle ? String(update.newHandle) : "";
}

/**
 * Turn an agent turn into what the shell should SAY. Prose only, never widget JSON -
 * but the widget titles and values ride along as `on_screen` so an eyes-free listener
 * hears the headline figures the prose deliberately leaves to the dashboard.
 */
export function spokenResult(
  answer: string,
  widgets: { title?: string; value?: string }[],
): Record<string, unknown> {
  const onScreen = widgets
    .filter((w) => w.title && w.value)
    .map((w) => `${w.title}: ${w.value}`);
  return { answer, on_screen: onScreen };
}

/**
 * The websocket URL for an ephemeral token.
 *
 * `access_token`, not `key`: the token is used in place of an API key but is not one, and
 * `?key=` is refused with `1007 Missing or malformed auth token in request. Obtain one
 * from CreateAuthToken and pass it in an access_token query parameter`.
 */
/** Widget headlines as spoken lines ("Units: -12%"), for the on-screen digest. */
export function digest(widgets: { title?: string; value?: string }[]): string[] {
  return widgets.filter((w) => w.title && w.value).map((w) => `${w.title}: ${w.value}`);
}

export function liveUrl(token: string): string {
  return `wss://${LIVE_HOST}${LIVE_PATH}?access_token=${encodeURIComponent(token)}`;
}

/* ------------------------------ the session ------------------------------ */

/** What the session needs from the app to do its job. */
export interface VoiceSessionHooks {
  /**
   * Run one question through the deep agent. Returns the spoken prose plus the widgets
   * it built (for the on-screen digest), or an approval request when the run paused.
   * `headers` carries the trace parent, so the run nests under the conversation.
   */
  ask(
    question: string,
    headers: Record<string, string>,
    /** Called with each tool the agent starts, so the shell can narrate progress. */
    onProgress?: (toolName: string) => void,
  ): Promise<{
    answer: string;
    widgets: { title?: string; value?: string }[];
    approval?: string;
    /** The agent run's LangSmith id, recorded on the tool span to link the traces. */
    runId?: string;
  }>;
  /** Answer an approval the agent paused on, with the option the user spoke. */
  resume(
    choice: string,
  ): Promise<{ answer: string; widgets: { title?: string; value?: string }[] }>;
  /** A finished transcript line, for the chat panel and the trace. */
  onTranscript(role: "user" | "model", text: string): void;
  /** Connection state, for the button. */
  onState(state: VoiceState): void;
  /**
   * What the agent is doing, in words, for the one-line indicator. Fires on EVERY tool
   * the agent starts - unlike the spoken narration, which is throttled, because a status
   * line can update as often as it likes without becoming a monologue.
   */
  onActivity(label: string): void;
  /** True while model audio is arriving, so the UI can look alive while it talks. */
  onSpeaking(active: boolean): void;
  /**
   * Loudness right now, 0..1, and WHICH SIDE made it. Called per audio frame (dozens of
   * times a second), so a consumer must NOT hold it in React state - write it to a ref and
   * read it from an animation frame.
   *
   * The side matters because the orb reacts in opposite directions: it draws inward while
   * the user speaks and swells outward while the model does (see `haloTransform`).
   */
  onLevel(level: number, from: "user" | "model"): void;
  /**
   * Why a session failed, in the server's own words. Without this a bad URL, a rejected
   * token and a normal hang-up are indistinguishable: the socket just closes and the
   * button goes quiet.
   */
  onError(detail: string): void;
  /**
   * Mint a Live token. Called by the session rather than passed in, so it happens after
   * the mic is ready and so a retry can get a fresh one (see `start`).
   */
  getToken(): Promise<{ token: string; model: string }>;
  /** Open a trace span for a tool call; returns its id and the run's trace headers. */
  openToolSpan(question: string): Promise<{ tool_id?: string; headers?: Record<string, string> }>;
  /** Close that span once the run finished. */
  closeToolSpan(toolId: string, outputs: Record<string, unknown>): void;
}

export type VoiceState = "idle" | "connecting" | "listening" | "thinking" | "error";

/**
 * One voice conversation: a WebSocket to Gemini Live, the mic and speaker worklets, and
 * the tool loop that turns a function call into a deep agent run.
 *
 * Raw WebSocket rather than Google's SDK, deliberately: the protocol we need is a
 * handful of JSON frames, and a spike that adds no dependency is easier to read,
 * review and delete.
 */
export class VoiceSession {
  private ws: WebSocket | null = null;
  private ctx: AudioContext | null = null;
  private mic: MediaStream | null = null;
  private player: AudioWorkletNode | null = null;
  private recorder: AudioWorkletNode | null = null;
  private resumeHandle = "";
  private pendingApproval = "";
  private closed = false;
  /** Partial transcripts accumulate here; Live streams them a few words at a time. */
  private partial = { user: "", model: "" };
  private speakingTimer: ReturnType<typeof setTimeout> | null = null;
  /** Both sides of the conversation, for the trace's playable timeline. */
  private readonly tape = new ConversationRecorder(MIC_RATE, PLAYBACK_RATE);

  private readonly hooks: VoiceSessionHooks;
  private readonly voice: string;
  private readonly persona: VoicePersona;
  private model = "";
  private token = "";
  /** One retry with a fresh token, for the stale-token case below. */
  private retried = false;

  constructor(hooks: VoiceSessionHooks, voice = DEFAULT_VOICE, persona: VoicePersona = {}) {
    this.hooks = hooks;
    this.voice = voice || DEFAULT_VOICE;
    this.persona = persona;
  }

  /**
   * Start the mic FIRST, then mint the token, then connect.
   *
   * The order matters and is not obvious: a token is only good for ~60 seconds to OPEN a
   * session (Google's `newSessionExpireTime` default), and `getUserMedia` can sit on a
   * browser permission dialog for as long as the user takes to notice it. Minting first
   * means a slow "Allow" spends the whole window and the socket closes with a 1007 that
   * blames the token.
   */
  async start(): Promise<void> {
    this.hooks.onState("connecting");
    await this.startAudio();
    await this.connect();
  }

  /** Tear everything down. Safe to call twice. */
  stop(): void {
    this.closed = true;
    if (this.speakingTimer) clearTimeout(this.speakingTimer);
    this.hooks.onLevel(0, "model");
    this.hooks.onLevel(0, "user");
    this.ws?.close();
    this.ws = null;
    this.recorder?.disconnect();
    this.player?.disconnect();
    this.mic?.getTracks().forEach((t) => t.stop());
    void this.ctx?.close();
    this.ctx = null;
    this.hooks.onState("idle");
  }

  private async startAudio(): Promise<void> {
    // One context at the PLAYBACK rate (24k, fixed by the API). Mic frames arrive at
    // whatever the device gives and are downsampled per frame, which is cheaper and
    // more portable than forcing a second context at 16k.
    const ctx = new AudioContext({ sampleRate: PLAYBACK_RATE });
    this.ctx = ctx;
    await ctx.audioWorklet.addModule("/pcm-recorder-processor.js");
    await ctx.audioWorklet.addModule("/pcm-player-processor.js");

    this.player = new AudioWorkletNode(ctx, "pcm-player-processor");
    this.player.connect(ctx.destination);

    this.mic = await navigator.mediaDevices.getUserMedia({
      audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
    });
    this.recorder = new AudioWorkletNode(ctx, "pcm-recorder-processor");
    ctx.createMediaStreamSource(this.mic).connect(this.recorder);
    this.recorder.port.onmessage = (event) => this.sendMic(event.data as Float32Array);
  }

  private async connect(): Promise<void> {
    if (!this.token) {
      const minted = await this.hooks.getToken();
      this.token = minted.token;
      this.model = minted.model;
    }
    const ws = new WebSocket(liveUrl(this.token));
    this.ws = ws;
    ws.onopen = () => {
      // The first frame MUST be setup, and nothing else may be sent until the server
      // answers setupComplete.
      ws.send(
        JSON.stringify(
          setupMessage(this.model, this.resumeHandle || undefined, this.voice, this.persona),
        ),
      );
    };
    ws.onmessage = (event) => void this.onFrame(event.data);
    ws.onerror = () => this.hooks.onError("websocket error (see the console for detail)");
    ws.onclose = (event) => {
      if (this.closed) return;
      // A Live connection lasts about ten minutes and a demo does not, so a close with
      // a resumption handle in hand is a reconnect, not the end of the conversation.
      if (this.resumeHandle) {
        void this.connect();
        return;
      }
      // 1000 is a clean hang-up; anything else is the server telling us why, and it is
      // the only place it ever says so.
      if (!event.code || event.code === 1000) {
        this.hooks.onState("idle");
        return;
      }
      // A token is single-use and only opens a session for ~60 seconds, so "this token is
      // spent or stale" is an ordinary outcome, not a fault: mint a fresh one and try
      // once more before bothering the user. Once, so a genuine rejection still surfaces.
      if (!this.retried && /token/i.test(event.reason || "")) {
        this.retried = true;
        this.token = "";
        void this.connect();
        return;
      }
      this.hooks.onError(`live session closed (${event.code}): ${event.reason || "no reason given"}`);
      this.hooks.onState("error");
    };
  }

  /** Downsample one mic frame to 16k and ship it. */
  private sendMic(frame: Float32Array): void {
    if (this.ws?.readyState !== WebSocket.OPEN || !this.ctx) return;
    const out = downsampleTo16k(frame, this.ctx.sampleRate);
    // The halo should react to the USER too, not just the model: half a conversation is
    // them talking, and a flat orb while you speak feels deaf.
    this.hooks.onLevel(frameLevel(out), "user");
    // Recorded at the rate we SEND, so the two sides line up on one timeline.
    this.tape.add("user", out);
    this.ws.send(JSON.stringify(realtimeAudioMessage(floatToPcm16(out))));
  }

  private async onFrame(data: unknown): Promise<void> {
    // Frames arrive as text or as a Blob, depending on the browser.
    const text = typeof data === "string" ? data : await (data as Blob).text();
    let frame: unknown;
    try {
      frame = JSON.parse(text);
    } catch {
      return;
    }

    if ((frame as { setupComplete?: unknown }).setupComplete) this.hooks.onState("listening");

    // An in-band error, e.g. a malformed frame from us. Reported rather than swallowed:
    // the session often survives it, so nothing else would ever mention it.
    const inBand = (frame as { error?: { message?: string } })?.error?.message;
    if (inBand) this.hooks.onError(`live error: ${inBand}`);

    const handle = resumptionHandle(frame);
    if (handle) this.resumeHandle = handle;

    if (wasInterrupted(frame)) {
      // Barge-in: drop everything queued so we stop mid-sentence. The server drops its
      // pending function calls too, so there is nothing left to respond to.
      this.player?.port.postMessage("clear");
    }

    const chunks = audioChunksFrom(frame);
    for (const chunk of chunks) {
      const samples = pcm16ToFloat(decodeBase64(chunk));
      this.player?.port.postMessage(samples);
      this.tape.add("model", samples);
      // Measured here rather than in the worklet: the worklet drains on the audio thread
      // and would need a message back, and this is the same audio a beat earlier.
      this.hooks.onLevel(frameLevel(samples), "model");
    }
    // Audio arrives in a burst per turn, so "still speaking" is a timer rather than a
    // flag: it stays true until the chunks stop for a moment.
    if (chunks.length) {
      this.hooks.onSpeaking(true);
      if (this.speakingTimer) clearTimeout(this.speakingTimer);
      this.speakingTimer = setTimeout(() => this.hooks.onSpeaking(false), SPEAKING_IDLE_MS);
    }

    const { input, output } = transcriptsFrom(frame);
    if (input) this.partial.user += input;
    if (output) this.partial.model += output;
    // A completed generation is the turn boundary: flush whatever accumulated.
    const done = (frame as { serverContent?: { generationComplete?: boolean } })?.serverContent
      ?.generationComplete;
    if (done) this.flushTranscripts();

    for (const call of toolCallsFrom(frame)) void this.runTool(call);
  }

  private flushTranscripts(): void {
    for (const role of ["user", "model"] as const) {
      const text = this.partial[role].trim();
      if (text) this.hooks.onTranscript(role, text);
      this.partial[role] = "";
    }
  }

  /**
   * Run one function call. TWO responses, sent as DIFFERENT message types, which is the
   * whole trick to not going silent:
   *
   *   1. `toolResponse` with an acknowledgement, immediately. This completes the call, so
   *      the model stops waiting and keeps the floor.
   *   2. `clientContent` carrying a `functionResponse` part, once the agent finishes (up
   *      to a minute later). A second `toolResponse` would be dropped - see
   *      `lateToolResponseMessage`.
   */
  private async runTool(call: LiveToolCall): Promise<void> {
    const ws = this.ws;
    if (!ws) return;
    const ack = { status: "started", note: "Working on it; results follow." };
    // `willContinue`: acknowledged, not answered. This is what stops the model waiting.
    ws.send(JSON.stringify(toolResponseMessage(call, ack, "SILENT", true)));
    this.hooks.onState("thinking");

    // Progress, throttled. The agent calls a dozen tools in a turn and narrating each one
    // would be worse than silence; one line every few seconds reads as thinking aloud.
    let lastProgress = 0;
    const narrate = (toolName: string) => {
      const label = progressLabel(toolName);
      // The status line updates every time; the SPOKEN line does not.
      this.hooks.onActivity(label);
      const now = Date.now();
      if (now - lastProgress < PROGRESS_MIN_GAP_MS) return;
      lastProgress = now;
      ws.send(JSON.stringify(progressMessage(label)));
    };

    try {
      if (call.name === RESUME_TOOL) {
        const out = await this.hooks.resume(String(call.args.choice || ""));
        this.pendingApproval = "";
        ws.send(JSON.stringify(resultMessage(out.answer, digest(out.widgets))));
        return;
      }

      const question = String(call.args.question || "");
      const span = await this.hooks.openToolSpan(question);
      const out = await this.hooks.ask(question, span.headers || {}, narrate);
      const payload = out.approval
        ? { status: "needs_approval", approval: out.approval }
        : spokenResult(out.answer, out.widgets);
      this.pendingApproval = out.approval || "";
      // `run_id` is the link between this conversation's trace and the agent's own: the two
      // cannot be nested (graph.py explains why), so the span records where to look.
      if (span.tool_id) {
        this.hooks.closeToolSpan(span.tool_id, { ...payload, run_id: out.runId || "" });
      }
      ws.send(
        JSON.stringify(
          out.approval
            ? systemTurnMessage(
                `the agent needs a decision before it can finish: ${out.approval} ` +
                  "Read out the options and wait for the answer.",
              )
            : resultMessage(out.answer, digest(out.widgets)),
        ),
      );
    } catch (e) {
      ws.send(
        JSON.stringify(
          systemTurnMessage(
            `that question failed: ${(e as Error).message}. Say so briefly.`,
          ),
        ),
      );
    } finally {
      this.hooks.onActivity("");
      this.hooks.onState("listening");
    }
  }

  /**
   * The conversation as a stereo WAV (user left, assistant right), or null if nothing was
   * captured. Attached to the trace when the session ends, which is what gives the run a
   * scrubbable audio timeline.
   */
  recording(): Uint8Array | null {
    return this.tape.render();
  }

  /** Whether the agent is waiting on a spoken approval (drives the button's hint). */
  get awaitingApproval(): string {
    return this.pendingApproval;
  }
}
