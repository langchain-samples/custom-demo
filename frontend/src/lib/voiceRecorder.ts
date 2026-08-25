/**
 * Records both sides of a voice conversation into one time-aligned stereo WAV.
 *
 * This is what makes a conversation PLAYABLE in its LangSmith trace: attached to the
 * `voice_session` run, the trace grows an audio timeline you can scrub, with the user on
 * the left channel and the assistant on the right. Ported from the ADK example's
 * `StereoRecorder`, which does the same thing server-side.
 *
 * The placement rule is the whole trick. Chunks are stamped with when they ARRIVED, but
 * model audio arrives in bursts faster than real time, so laying each chunk at its arrival
 * time would overlap them into a garbled sped-up mess. Instead each chunk starts at the
 * LATER of its arrival time and the end of the previous one: bursts stay contiguous, and a
 * genuine pause still leaves silence.
 */

export interface Stamped {
  /** Seconds since the recording started. */
  at: number;
  samples: Float32Array;
}

/** Lay stamped chunks onto one timeline at `rate`, keeping bursts contiguous. */
export function layout(chunks: Stamped[], rate: number): Float32Array {
  let cursor = 0;
  const placed: { start: number; samples: Float32Array }[] = [];
  for (const { at, samples } of chunks) {
    if (!samples.length) continue;
    const start = Math.max(Math.round(at * rate), cursor);
    placed.push({ start, samples });
    cursor = start + samples.length;
  }
  const track = new Float32Array(cursor);
  for (const { start, samples } of placed) track.set(samples, start);
  return track;
}

/** Nearest-neighbour resample of a whole track (once, so there are no chunk-edge clicks). */
export function resample(track: Float32Array, from: number, to: number): Float32Array {
  if (!track.length || from === to) return track;
  const out = new Float32Array(Math.round((track.length * to) / from));
  const ratio = from / to;
  for (let i = 0; i < out.length; i++) out[i] = track[Math.min(track.length - 1, Math.floor(i * ratio))];
  return out;
}

/** Two mono tracks to a 16-bit stereo WAV: left = user, right = assistant. */
export function stereoWav(left: Float32Array, right: Float32Array, rate: number): Uint8Array {
  const frames = Math.max(left.length, right.length);
  const bytes = new Uint8Array(44 + frames * 4);
  const view = new DataView(bytes.buffer);
  const ascii = (offset: number, text: string) => {
    for (let i = 0; i < text.length; i++) view.setUint8(offset + i, text.charCodeAt(i));
  };
  ascii(0, "RIFF");
  view.setUint32(4, 36 + frames * 4, true);
  ascii(8, "WAVE");
  ascii(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 2, true); // stereo
  view.setUint32(24, rate, true);
  view.setUint32(28, rate * 4, true); // byte rate: 2 channels * 2 bytes
  view.setUint16(32, 4, true); // block align
  view.setUint16(34, 16, true); // bits
  ascii(36, "data");
  view.setUint32(40, frames * 4, true);
  const clamp = (v: number) => Math.max(-1, Math.min(1, v || 0));
  for (let i = 0; i < frames; i++) {
    view.setInt16(44 + i * 4, Math.round(clamp(left[i]) * 0x7fff), true);
    view.setInt16(46 + i * 4, Math.round(clamp(right[i]) * 0x7fff), true);
  }
  return bytes;
}

/**
 * Collects both sides of a conversation, then renders the WAV.
 *
 * Capped, because an unbounded recording is a memory leak with a microphone attached: a
 * long session drops its oldest audio rather than growing forever.
 */
export class ConversationRecorder {
  private readonly started = performance.now();
  private user: Stamped[] = [];
  private model: Stamped[] = [];
  private samples = 0;

  private readonly userRate: number;
  private readonly modelRate: number;
  /** Roughly 20 minutes of both sides at 24k, which outlasts any session cap. */
  private readonly maxSamples: number;

  constructor(userRate: number, modelRate: number, maxSamples = 30_000_000) {
    this.userRate = userRate;
    this.modelRate = modelRate;
    this.maxSamples = maxSamples;
  }

  add(side: "user" | "model", samples: Float32Array): void {
    if (!samples.length || this.samples > this.maxSamples) return;
    this.samples += samples.length;
    // Copied: the caller's buffer is reused by the audio pipeline after this returns.
    const entry = { at: (performance.now() - this.started) / 1000, samples: samples.slice(0) };
    (side === "user" ? this.user : this.model).push(entry);
  }

  /** The conversation as a stereo WAV, or null when nothing was captured. */
  render(): Uint8Array | null {
    if (!this.user.length && !this.model.length) return null;
    const out = this.modelRate;
    const left = resample(layout(this.user, this.userRate), this.userRate, out);
    const right = layout(this.model, out);
    return stereoWav(left, right, out);
  }
}
