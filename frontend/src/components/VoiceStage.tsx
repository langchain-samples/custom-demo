/**
 * The immersive voice view: one orb where the chat rail usually is.
 *
 * Presentational only - `useVoiceSession` owns the conversation, so leaving this view does
 * not end it. The dashboard keeps its own pane beside this, which is the point: you talk,
 * and the figures appear over there while the orb stays quiet about them.
 *
 * The one non-decorative element is the activity line. A deep agent turn runs for up to a
 * minute, and without a word about what it is doing, a silent orb is indistinguishable
 * from a hung one.
 */

import { useState } from "react";
import { IconX } from "@tabler/icons-react";
import type { QuickAction } from "@/lib/api";
import type { VoiceSessionView } from "@/lib/hooks/use-voice-session";
import { VoiceOrb } from "@/components/VoiceOrb";

export interface VoiceStageProps {
  voice: VoiceSessionView;
  /** Leave the immersive view (the conversation keeps going). */
  onExit: () => void;
  /** Assistant branding, so the stage reads as the customer's, not ours. */
  displayName: string;
  logo?: string;
  /**
   * The assistant's quick actions. In voice mode they are a TELEPROMPTER, not buttons:
   * tapping one opens the question to read aloud. Sending it as text would be the wrong
   * thing - the model has to hear the question or it has nothing to answer to, and a
   * silently-submitted turn would make the orb speak about something nobody asked.
   */
  presets?: QuickAction[];
}

/** What the orb is doing, in the fewest words that are still true. */
function statusLine(voice: VoiceSessionView): string {
  if (voice.state === "error") return voice.error || "Voice failed, tap to retry";
  if (voice.state === "connecting") return "Connecting…";
  if (voice.state === "idle") return "Tap to start talking";
  if (voice.activity) return voice.activity;
  if (voice.speaking) return "Speaking…";
  if (voice.state === "thinking") return "Working on it…";
  return "Listening, just start talking";
}

export function VoiceStage({
  voice,
  onExit,
  displayName,
  logo,
  presets = [],
}: VoiceStageProps) {
  const { running } = voice;
  const [reading, setReading] = useState<number | null>(null);

  return (
    <div className="relative flex h-full flex-col items-center justify-center gap-8 px-8">
      <button
        type="button"
        onClick={onExit}
        title="Back to the chat view (the conversation keeps going)"
        aria-label="Back to the chat view"
        className="absolute top-4 right-4 grid size-9 place-items-center rounded-full border border-border bg-panel text-muted-foreground transition-colors hover:text-foreground"
      >
        <IconX className="size-4" />
      </button>

      <div className="flex flex-col items-center gap-2">
        {logo && <img src={logo} alt="" className="size-8 rounded" />}
        <h2 className="m-0 font-heading text-xl font-semibold tracking-tight">{displayName}</h2>
      </div>

      <VoiceOrb voice={voice} onClick={() => (running ? voice.stop() : voice.start())} />

      <div className="flex min-h-16 max-w-md flex-col items-center gap-1.5 text-center">
        <p className="m-0 text-sm text-muted-foreground">{statusLine(voice)}</p>
        {/* The last thing said, so a listener can confirm they were heard correctly. */}
        {voice.lastSaid && (
          <p className="m-0 text-[13px] leading-snug text-foreground/70">
            <span className="text-muted-foreground">
              {voice.lastSaid.role === "user" ? "You: " : ""}
            </span>
            {voice.lastSaid.text}
          </p>
        )}
      </div>

      {presets.length > 0 && (
        <div className="flex w-full max-w-lg flex-col items-center gap-2">
          {reading === null ? (
            <>
              <p className="m-0 text-[11px] text-muted-foreground">
                Tap one to read it aloud
              </p>
              <div className="flex flex-wrap justify-center gap-1.5">
                {presets.map((preset, i) => (
                  <button
                    key={`${preset.label}-${i}`}
                    type="button"
                    onClick={() => setReading(i)}
                    className="rounded-full border border-border bg-panel px-3 py-1.5 text-[11.5px] text-muted-foreground transition-colors hover:text-foreground"
                  >
                    {preset.label}
                  </button>
                ))}
              </div>
            </>
          ) : (
            <button
              type="button"
              onClick={() => setReading(null)}
              title="Close"
              className="w-full rounded-xl border border-brand/40 bg-brand/5 px-4 py-3 text-left"
            >
              <span className="mb-1 block text-[10.5px] tracking-wide text-muted-foreground uppercase">
                {presets[reading].label} - read this out
              </span>
              {/* Deliberately large: this is meant to be read off the screen while
                  speaking, from further away than chat text. */}
              <span className="block text-[15px] leading-relaxed text-foreground">
                {presets[reading].question}
              </span>
            </button>
          )}
        </div>
      )}
    </div>
  );
}
