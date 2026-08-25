/**
 * Keyboard sound for the dead air while the agent works.
 *
 * WHY IT IS SYNTHESISED. A keystroke is a few milliseconds of filtered noise with a fast
 * decay, so building it costs less than shipping, licensing and cache-busting an mp3 - and
 * it is tunable: every strike gets a slightly different pitch and level, which is what stops
 * a repeated sample from sounding like a repeated sample.
 *
 * WHY IT IS EVENT-DRIVEN, NOT A LOOP. Each burst is one real tool call. A continuous bed
 * sounds identical whether the agent is running six tools or has silently died, so it stops
 * carrying information about thirty seconds in - exactly when you start needing it. Tied to
 * tool calls, the rhythm is the truth: a busy turn sounds busy, and a hang goes quiet.
 *
 * It shares the session's AudioContext rather than opening its own: browsers cap concurrent
 * contexts, and the mic path already owns one at the playback rate.
 */

/** Peak gain of a single strike, before ducking. Deliberately low - this is texture. */
const STRIKE_GAIN = 0.055;
/** A strike's decay. Longer than this and it reads as a tap on a desk, not a key. */
const STRIKE_DECAY = 0.028;
/** Gap between strikes, humanised: a typist is not a metronome. */
const GAP_MS = { min: 55, max: 135 };
/** Keystrokes per burst, i.e. per tool call. Enough to register, short of a paragraph. */
const BURST = { min: 4, max: 9 };
/** Model speech above this (0..1) mutes the keys entirely, so they never talk over it. */
const DUCK_AT = 0.04;

function between(a: number, b: number): number {
  return a + Math.random() * (b - a);
}

export class TypingSound {
  private readonly ctx: AudioContext;
  /** Master gain, driven by `duck` so keys never compete with the assistant's voice. */
  private readonly bus: GainNode;
  private noise: AudioBuffer | null = null;
  private timers: ReturnType<typeof setTimeout>[] = [];
  private muted = false;

  constructor(ctx: AudioContext) {
    this.ctx = ctx;
    this.bus = ctx.createGain();
    this.bus.gain.value = 1;
    this.bus.connect(ctx.destination);
    // Off switch with no UI attached yet: a presenter on a laptop speaker in a real
    // meeting needs to kill this without a deploy. `localStorage.voiceKeys = "off"`.
    try {
      this.muted = localStorage.getItem("voiceKeys") === "off";
    } catch {
      /* private mode: leave it on */
    }
  }

  /** One second of white noise, reused by every strike (allocated once, on first use). */
  private noiseBuffer(): AudioBuffer {
    if (!this.noise) {
      const buf = this.ctx.createBuffer(1, this.ctx.sampleRate, this.ctx.sampleRate);
      const data = buf.getChannelData(0);
      for (let i = 0; i < data.length; i++) data[i] = Math.random() * 2 - 1;
      this.noise = buf;
    }
    return this.noise;
  }

  /**
   * One keystroke: a noise burst through a bandpass, enveloped to near-nothing in under
   * 30ms. The bandpass centre wanders per strike so consecutive keys are not identical,
   * and a quieter low-passed copy underneath gives the "thock" of the key bottoming out.
   */
  private strike(at: number): void {
    const ctx = this.ctx;
    const src = ctx.createBufferSource();
    src.buffer = this.noiseBuffer();
    // Start somewhere random in the buffer, or every strike samples the same noise.
    const offset = Math.random() * 0.9;

    const band = ctx.createBiquadFilter();
    band.type = "bandpass";
    band.frequency.value = between(1400, 2600);
    band.Q.value = between(0.7, 1.4);

    const env = ctx.createGain();
    env.gain.setValueAtTime(0, at);
    env.gain.linearRampToValueAtTime(STRIKE_GAIN * between(0.7, 1.2), at + 0.002);
    env.gain.exponentialRampToValueAtTime(0.0001, at + STRIKE_DECAY);

    const thock = ctx.createBiquadFilter();
    thock.type = "lowpass";
    thock.frequency.value = between(180, 320);
    const thockEnv = ctx.createGain();
    thockEnv.gain.setValueAtTime(0, at);
    thockEnv.gain.linearRampToValueAtTime(STRIKE_GAIN * 0.6, at + 0.003);
    thockEnv.gain.exponentialRampToValueAtTime(0.0001, at + STRIKE_DECAY * 1.6);

    src.connect(band).connect(env).connect(this.bus);
    src.connect(thock).connect(thockEnv).connect(this.bus);
    src.start(at, offset, STRIKE_DECAY * 2);
    src.stop(at + STRIKE_DECAY * 2);
  }

  /** A short run of keystrokes. Called once per tool call the agent starts. */
  burst(): void {
    if (this.muted || this.ctx.state === "closed") return;
    const n = Math.round(between(BURST.min, BURST.max));
    let delay = 0;
    for (let i = 0; i < n; i++) {
      delay += between(GAP_MS.min, GAP_MS.max);
      // Scheduled on the audio clock (sample-accurate) but ARMED on a timer, so a burst
      // is cancellable: `stop` mid-burst must not leave keys queued in the graph.
      const t = setTimeout(() => this.strike(this.ctx.currentTime + 0.01), delay);
      this.timers.push(t);
    }
  }

  /**
   * Follow the model's loudness. Any speech at all mutes the keys rather than lowering
   * them: at this volume a partial duck is still audible under a voice, and the point of
   * the sound is the pauses.
   */
  duck(modelLevel: number): void {
    const target = modelLevel > DUCK_AT ? 0 : 1;
    const g = this.bus.gain;
    if (Math.abs(g.value - target) < 0.01) return;
    g.cancelScheduledValues(this.ctx.currentTime);
    g.setTargetAtTime(target, this.ctx.currentTime, 0.08);
  }

  /** Turn the sound off for good (presenter on a laptop speaker in a real meeting). */
  mute(on: boolean): void {
    this.muted = on;
    if (on) this.cancel();
  }

  private cancel(): void {
    for (const t of this.timers) clearTimeout(t);
    this.timers = [];
  }

  /** Drop every pending strike. The AudioContext belongs to the session, so it stays. */
  stop(): void {
    this.cancel();
    try {
      this.bus.disconnect();
    } catch {
      /* already torn down with the context */
    }
  }
}
