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

const LIVE_HOST = "generativelanguage.googleapis.com";
// The CONSTRAINED method, and it has to be. Verified against the live API: an ephemeral
// token on the plain `BidiGenerateContent` is refused with `1008 Method doesn't allow
// unregistered callers`, which arrives as a socket that opens and closes again - no error
// frame, nothing in the network tab that names the cause.
const LIVE_PATH =
  "/ws/google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContentConstrained";

/** Live API audio rates. Both fixed by the API: 16k in, 24k out. */
export const MIC_RATE = 16000;
export const PLAYBACK_RATE = 24000;

/** What the shell is allowed to ask the SPA to do. */
export const INVOKE_TOOL = "invoke_deep_agent";
export const RESUME_TOOL = "resume_deep_agent";

/**
 * Spoken-delivery instructions for the shell (NOT the agent's prompt, which stays
 * untouched). The non-blocking contract is stated explicitly because the model has to
 * cooperate with it: a deep agent run takes tens of seconds, and the alternative to
 * "acknowledge, then keep the floor" is dead air.
 */
export const VOICE_INSTRUCTIONS = `You are the voice of a live analytics assistant. You
are being HEARD, not read: keep replies to a sentence or two, never speak markdown, and
never read out a list of numbers.

You have one real capability: the \`${INVOKE_TOOL}\` tool, which asks the analytics agent
a question. Use it for anything about the customer's data, metrics, accounts, orders or
reports. Pass the user's question through in full.

\`${INVOKE_TOOL}\` is NON-BLOCKING and slow (up to a minute). So:
- Say a short acknowledgement when you call it ("Let me pull that up"), then stop and let
  the user talk. Do NOT go silent waiting for it.
- The findings arrive later as a follow-up result. Weave them in conversationally and
  reconnect them to what was asked.
- The figures are ON SCREEN in the dashboard. Summarise what they mean; do not recite
  them. Point at the screen instead: "it's on the dashboard now".
- If the result says approval is needed, read out the options and, once the user picks
  one, call \`${RESUME_TOOL}\` with their choice.`;

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

/** Base64 to bytes (the Live API sends audio as base64 inline data). */
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
 * The `setup` message that opens a session.
 *
 * Only the model and the tool declarations are ours to choose here: everything else was
 * pinned into the ephemeral token server-side, and a client that contradicts the pinned
 * config is refused.
 */
export function setupMessage(model: string, resumeHandle?: string): object {
  const behavior = supportsNonBlocking(model) ? { behavior: "NON_BLOCKING" } : {};
  return {
    setup: {
      model: `models/${model}`,
      // Audio out, and it must be stated: a Live session opened without an explicit
      // modality closes with `1011 Internal error encountered` - no error frame, no hint.
      generationConfig: { responseModalities: ["AUDIO"] },
      // The ONLY text this session produces. A native-audio model answers in audio, so
      // these transcripts are what the chat panel renders and what the trace records.
      inputAudioTranscription: {},
      outputAudioTranscription: {},
      systemInstruction: { parts: [{ text: VOICE_INSTRUCTIONS }] },
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

/** The `toolResponse` frame for one finished call. */
export function toolResponseMessage(
  call: LiveToolCall,
  response: Record<string, unknown>,
  scheduling: ResponseScheduling,
): object {
  return {
    toolResponse: {
      functionResponses: [
        // `scheduling` rides INSIDE the response payload, not beside it.
        { id: call.id, name: call.name, response: { ...response, scheduling } },
      ],
    },
  };
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
  ): Promise<{ answer: string; widgets: { title?: string; value?: string }[]; approval?: string }>;
  /** Answer an approval the agent paused on, with the option the user spoke. */
  resume(
    choice: string,
  ): Promise<{ answer: string; widgets: { title?: string; value?: string }[] }>;
  /** A finished transcript line, for the chat panel and the trace. */
  onTranscript(role: "user" | "model", text: string): void;
  /** Connection state, for the button. */
  onState(state: VoiceState): void;
  /**
   * Why a session failed, in the server's own words. Without this a bad URL, a rejected
   * token and a normal hang-up are indistinguishable: the socket just closes and the
   * button goes quiet.
   */
  onError(detail: string): void;
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

  private readonly model: string;
  private readonly token: string;
  private readonly hooks: VoiceSessionHooks;

  constructor(model: string, token: string, hooks: VoiceSessionHooks) {
    this.model = model;
    this.token = token;
    this.hooks = hooks;
  }

  /** Connect, start the mic, and begin the conversation. */
  async start(): Promise<void> {
    this.hooks.onState("connecting");
    await this.startAudio();
    await this.connect();
  }

  /** Tear everything down. Safe to call twice. */
  stop(): void {
    this.closed = true;
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
    const ws = new WebSocket(liveUrl(this.token));
    this.ws = ws;
    ws.onopen = () => {
      // The first frame MUST be setup, and nothing else may be sent until the server
      // answers setupComplete.
      ws.send(JSON.stringify(setupMessage(this.model, this.resumeHandle || undefined)));
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
      if (event.code && event.code !== 1000) {
        this.hooks.onError(`live session closed (${event.code}): ${event.reason || "no reason given"}`);
        this.hooks.onState("error");
      } else {
        this.hooks.onState("idle");
      }
    };
  }

  /** Downsample one mic frame to 16k and ship it. */
  private sendMic(frame: Float32Array): void {
    if (this.ws?.readyState !== WebSocket.OPEN || !this.ctx) return;
    const ratio = this.ctx.sampleRate / MIC_RATE;
    const out = new Float32Array(Math.floor(frame.length / ratio));
    for (let i = 0; i < out.length; i++) out[i] = frame[Math.floor(i * ratio)];
    this.ws.send(
      JSON.stringify({
        realtimeInput: {
          mediaChunks: [
            { mimeType: `audio/pcm;rate=${MIC_RATE}`, data: encodeBase64(floatToPcm16(out)) },
          ],
        },
      }),
    );
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

    for (const chunk of audioChunksFrom(frame)) {
      this.player?.port.postMessage(pcm16ToFloat(decodeBase64(chunk)));
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
   * Run one function call. TWO responses, which is the whole trick to not going silent:
   * an immediate acknowledgement so the model keeps the floor, then the real answer with
   * `INTERRUPT` scheduling once the agent finishes (up to a minute later). The two-phase
   * shape is borrowed from the ADK example.
   */
  private async runTool(call: LiveToolCall): Promise<void> {
    const ws = this.ws;
    if (!ws) return;
    const ack = { status: "started", note: "Working on it; results follow." };
    ws.send(JSON.stringify(toolResponseMessage(call, ack, "SILENT")));
    this.hooks.onState("thinking");

    try {
      if (call.name === RESUME_TOOL) {
        const out = await this.hooks.resume(String(call.args.choice || ""));
        this.pendingApproval = "";
        ws.send(
          JSON.stringify(
            toolResponseMessage(call, spokenResult(out.answer, out.widgets), "INTERRUPT"),
          ),
        );
        return;
      }

      const question = String(call.args.question || "");
      const span = await this.hooks.openToolSpan(question);
      const out = await this.hooks.ask(question, span.headers || {});
      const payload = out.approval
        ? { status: "needs_approval", approval: out.approval }
        : spokenResult(out.answer, out.widgets);
      this.pendingApproval = out.approval || "";
      if (span.tool_id) this.hooks.closeToolSpan(span.tool_id, payload);
      ws.send(JSON.stringify(toolResponseMessage(call, payload, "INTERRUPT")));
    } catch (e) {
      ws.send(
        JSON.stringify(
          toolResponseMessage(call, { status: "failed", error: (e as Error).message }, "INTERRUPT"),
        ),
      );
    } finally {
      this.hooks.onState("listening");
    }
  }

  /** Whether the agent is waiting on a spoken approval (drives the button's hint). */
  get awaitingApproval(): string {
    return this.pendingApproval;
  }
}
