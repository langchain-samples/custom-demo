/**
 * Keyboard sound for the dead air while the agent works.
 *
 * A looped recording (`/sfx/keyboard-typing.mp3`). This was synthesised at first - noise
 * bursts through a bandpass - which is cheap and asset-free but never stopped sounding like
 * what it was: real typing has hand movement, key variation and room in it that a filtered
 * noise burst does not imply.
 *
 * WHY IT RUNS FOR THE WHOLE TURN. It also started as one burst per tool call, which is
 * honest but does not do the job: a single tool call can run for half a minute, so the
 * sound landed at the start and then left the silence it was meant to fill. It now plays
 * from the moment the agent picks up the question until it answers. That is still tied to
 * real work rather than a timer: it is on exactly while a run is in flight, so a finished
 * turn goes quiet on its own and a crash goes quiet early.
 *
 * THE ASSET IS TRIMMED, and has to be. The source clip opens with 0.44s of silence and ends
 * with 0.68s more, so looping it raw left a dead hole every eight seconds - the one thing a
 * "someone is working" sound must not do. It is cut to the live region (6.96s), faded 8ms at
 * each edge so the seam does not click, and downmixed to mono at 96kbps (84KB, from 253KB):
 * this is background texture, and nothing about it needs stereo. The three interior pauses
 * are deliberately kept - they are the human rhythm of the take.
 *
 * It shares the session's AudioContext rather than opening its own: browsers cap concurrent
 * contexts, and the mic path already owns one at the playback rate.
 */

const SAMPLE_URL = "/sfx/keyboard-typing.mp3";
/** Playback gain. The asset peaks at -5.3dB, so there is headroom to 1.0 if wanted. */
const GAIN = 0.7;
/** Ramp on start/stop. Without it, cutting a loop dead is an audible click. */
const RAMP = 0.06;
/** Model speech above this (0..1) mutes the keys entirely, so they never talk over it. */
const DUCK_AT = 0.04;

export class TypingSound {
  private readonly ctx: AudioContext;
  /** Master gain, driven by `duck` so keys never compete with the assistant's voice. */
  private readonly bus: GainNode;
  private buffer: AudioBuffer | null = null;
  private source: AudioBufferSourceNode | null = null;
  private muted = false;
  /** True between `start` and `idle`, i.e. while an agent run is actually in flight. */
  private running = false;

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
    if (!this.muted) void this.load();
  }

  /**
   * Fetch and decode the loop. Fire-and-forget from the constructor, so the first run of a
   * session does not wait on it - `start` records the intent and this plays if it is still
   * wanted by the time the bytes land. Failure is silent on purpose: no keyboard sound is a
   * cosmetic loss, and a missing asset must not take a conversation down with it.
   */
  private async load(): Promise<void> {
    try {
      const res = await fetch(SAMPLE_URL);
      if (!res.ok) return;
      this.buffer = await this.ctx.decodeAudioData(await res.arrayBuffer());
      if (this.running) this.play();
    } catch {
      /* no sound, no complaint */
    }
  }

  /**
   * Type until told to stop. Idempotent, so the caller does not have to track whether a
   * run is already under way.
   */
  start(): void {
    if (this.muted || this.running || this.ctx.state === "closed") return;
    this.running = true;
    if (this.buffer) this.play();
  }

  /** Stop typing, ramped so the cut is not a click. */
  idle(): void {
    this.running = false;
    const src = this.source;
    if (!src) return;
    this.source = null;
    const end = this.ctx.currentTime + RAMP;
    try {
      const g = this.bus.gain;
      g.cancelScheduledValues(this.ctx.currentTime);
      g.setTargetAtTime(0, this.ctx.currentTime, RAMP / 3);
      src.stop(end + RAMP);
    } catch {
      /* context already closed */
    }
  }

  private play(): void {
    if (!this.buffer || this.source) return;
    const src = this.ctx.createBufferSource();
    src.buffer = this.buffer;
    src.loop = true;
    src.connect(this.bus);
    // Start somewhere random in the loop. The same seven seconds from the same point every
    // time is recognisable within a couple of turns, which is exactly when a demo notices.
    src.start(0, Math.random() * this.buffer.duration);
    this.source = src;
    const g = this.bus.gain;
    g.cancelScheduledValues(this.ctx.currentTime);
    g.setValueAtTime(0, this.ctx.currentTime);
    g.linearRampToValueAtTime(GAIN, this.ctx.currentTime + RAMP);
  }

  /**
   * Follow the model's loudness. Any speech at all mutes the keys rather than lowering
   * them: at this volume a partial duck is still audible under a voice, and the point of
   * the sound is the pauses.
   */
  duck(modelLevel: number): void {
    if (!this.source) return;
    const target = modelLevel > DUCK_AT ? 0 : GAIN;
    const g = this.bus.gain;
    if (Math.abs(g.value - target) < 0.01) return;
    g.cancelScheduledValues(this.ctx.currentTime);
    g.setTargetAtTime(target, this.ctx.currentTime, 0.08);
  }

  /** Turn the sound off for good (presenter on a laptop speaker in a real meeting). */
  mute(on: boolean): void {
    this.muted = on;
    if (on) this.idle();
  }

  /** Stop playback for good. The AudioContext belongs to the session, so it stays. */
  stop(): void {
    this.idle();
    try {
      this.bus.disconnect();
    } catch {
      /* already torn down with the context */
    }
  }
}
