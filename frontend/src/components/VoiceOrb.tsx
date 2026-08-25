/**
 * The floating orb: a soft gradient sphere with a halo that breathes with the audio.
 *
 * The halo is driven by MEASURED loudness (`voice.level`, RMS per audio frame from both
 * the microphone and the model), not by a canned animation - so it moves when someone is
 * actually talking and sits still when nobody is. That is the whole difference between
 * this and a pulsing div.
 *
 * Level arrives dozens of times a second, so it is read from an animation frame and
 * written straight to CSS custom properties. Putting it in React state would re-render the
 * tree at audio rate.
 */

import { useEffect, useRef } from "react";
import type { VoiceSessionView } from "@/lib/hooks/use-voice-session";

export interface VoiceOrbProps {
  voice: VoiceSessionView;
  onClick: () => void;
  /** Diameter in px. The halo extends well past this. */
  size?: number;
}

/** How fast the halo follows the audio: quick to swell, slower to settle. */
const ATTACK = 0.35;
const RELEASE = 0.08;

export function VoiceOrb({ voice, onClick, size = 260 }: VoiceOrbProps) {
  const root = useRef<HTMLButtonElement>(null);
  const shown = useRef(0);
  const { running, state } = voice;

  useEffect(() => {
    let raf = 0;
    const tick = () => {
      const target = running ? voice.level.current : 0;
      // Asymmetric easing: a syllable should register immediately, then decay smoothly,
      // which is what makes it read as breathing rather than flickering.
      const k = target > shown.current ? ATTACK : RELEASE;
      shown.current += (target - shown.current) * k;
      const el = root.current;
      if (el) {
        el.style.setProperty("--level", shown.current.toFixed(3));
        // An idle-but-connected orb still drifts a little, so it never looks frozen.
        el.style.setProperty("--idle", running ? "1" : "0.35");
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
