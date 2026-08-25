/**
 * The floating orb: a soft gradient sphere with a halo that breathes with the audio.
 *
 * The halo is driven by MEASURED loudness (`voice.level`, RMS per audio frame from both the
 * microphone and the model), not by a canned animation - so it moves when someone is
 * actually talking and sits still when nobody is. That is the whole difference between this
 * and a pulsing div.
 *
 * Both layers move, in opposite directions for the two sides: sphere and halo swell outward
 * on the model's speech, and draw inward from a smaller baseline on the user's. The geometry
 * lives in `orbTransform` so it is testable; this file only smooths and applies it.
 *
 * Level arrives dozens of times a second, so it is read from an animation frame and
 * written straight to CSS custom properties. Putting it in React state would re-render the
 * tree at audio rate.
 */

import { useEffect, useRef } from "react";
import type { VoiceSessionView } from "@/lib/hooks/use-voice-session";
import { orbTransform } from "@/lib/voice";

export interface VoiceOrbProps {
  voice: VoiceSessionView;
  onClick: () => void;
  /** Diameter in px. The halo extends well past this. */
  size?: number;
}

/** How fast the halo follows the audio: quick to swell, slower to settle. */
const ATTACK = 0.35;
const RELEASE = 0.08;

export function VoiceOrb({ voice, onClick, size = 156 }: VoiceOrbProps) {
  const root = useRef<HTMLButtonElement>(null);
  const { running, state } = voice;

  useEffect(() => {
    let raf = 0;
    // Smoothed per side, because the halo's direction depends on which one is louder and a
    // raw frame-to-frame comparison would flicker between them.
    const eased = { user: 0, model: 0 };
    const tick = () => {
      const live = running ? voice.level.current : { user: 0, model: 0 };
      for (const side of ["user", "model"] as const) {
        const target = live[side];
        // Asymmetric easing: a syllable should register immediately, then decay smoothly,
        // which is what makes it read as breathing rather than flickering.
        const k = target > eased[side] ? ATTACK : RELEASE;
        eased[side] += (target - eased[side]) * k;
      }
      const { halo, sphere, speaker } = orbTransform(eased.user, eased.model);
      const el = root.current;
      if (el) {
        el.style.setProperty("--halo-scale", halo.scale.toFixed(3));
        el.style.setProperty(
          "--halo-opacity",
          (running ? halo.opacity : halo.opacity * 0.35).toFixed(3),
        );
        el.style.setProperty("--sphere-scale", sphere.scale.toFixed(3));
        el.dataset.speaker = running ? speaker : "idle";
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [running, voice.level]);

  return (
    <button
      ref={root}
      type="button"
      onClick={onClick}
      aria-label={running ? "Stop the conversation" : "Start talking"}
      className="voice-orb"
      style={{ ["--orb-size" as string]: `${size}px` }}
      data-state={state}
    >
      {/* Three layers, back to front: the halo that reacts, the sphere, and a highlight
          that gives it a lit-from-above roundness. */}
      <span className="voice-orb__halo" aria-hidden="true" />
      <span className="voice-orb__sphere" aria-hidden="true" />
      <span className="voice-orb__sheen" aria-hidden="true" />
    </button>
  );
}
