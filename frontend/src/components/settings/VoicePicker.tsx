/**
 * Pick the assistant's spoken voice, with a sample you can actually hear.
 *
 * A dropdown of adjectives is not a choice anyone can make: "Even" and "Warm" mean nothing
 * until you have heard them say the same sentence. The samples in `public/voice-samples`
 * are pre-rendered by the Live API (one line each, ~14KB), so previewing costs no tokens
 * and works offline.
 */

import { useEffect, useRef, useState } from "react";
import { IconPlayerPlayFilled, IconPlayerStopFilled } from "@tabler/icons-react";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { DEFAULT_VOICE } from "@/lib/voice";

/**
 * The voices with a shipped sample, and Google's own one-word descriptor for each. A
 * longer list exists (30-odd); these are the ones an assistant might plausibly use, and
 * every one here has a file next to it.
 */
export const SAMPLED_VOICES: { name: string; character: string }[] = [
  { name: "Schedar", character: "even" },
  { name: "Sulafat", character: "warm" },
  { name: "Achird", character: "friendly" },
  { name: "Charon", character: "informative" },
  { name: "Kore", character: "firm" },
  { name: "Aoede", character: "breezy" },
  { name: "Leda", character: "youthful" },
  { name: "Puck", character: "upbeat" },
];

export interface VoicePickerProps {
  value: string;
  onChange: (voice: string) => void;
}

export function VoicePicker({ value, onChange }: VoicePickerProps) {
  const chosen = value || DEFAULT_VOICE;
  const [playing, setPlaying] = useState(false);
  const audio = useRef<HTMLAudioElement | null>(null);

  // A sample must not outlive the panel, or it keeps talking over the demo.
  useEffect(() => {
    return () => audio.current?.pause();
  }, []);

  const toggle = () => {
    const el = audio.current;
    if (!el) return;
    if (playing) {
      el.pause();
      el.currentTime = 0;
      setPlaying(false);
      return;
    }
    el.currentTime = 0;
    void el.play().then(() => setPlaying(true)).catch(() => setPlaying(false));
  };

  return (
    <div className="flex flex-col gap-1.5">
      <Label className="text-[11px] font-medium text-muted-foreground">
        Voice <span className="text-[10px] font-normal">(spoken replies)</span>
      </Label>
      <div className="flex items-center gap-2">
        <Select value={chosen} onValueChange={onChange}>
          <SelectTrigger className="h-8 flex-1 text-xs">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {SAMPLED_VOICES.map((v) => (
              <SelectItem key={v.name} value={v.name} className="text-xs">
                {v.name} <span className="text-muted-foreground">- {v.character}</span>
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <button
          type="button"
          onClick={toggle}
          title={`Hear ${chosen}`}
          aria-label={`Hear ${chosen}`}
          className="grid size-8 shrink-0 place-items-center rounded-full border border-border text-muted-foreground transition-colors hover:text-foreground"
        >
          {playing ? (
            <IconPlayerStopFilled className="size-3.5" />
          ) : (
            <IconPlayerPlayFilled className="size-3.5" />
          )}
        </button>
      </div>
      {/* `key` so switching voices reloads the element rather than replaying the old file. */}
      <audio
        key={chosen}
        ref={audio}
        src={`/voice-samples/${chosen}.mp3`}
        onEnded={() => setPlaying(false)}
        preload="none"
      />
      <p className="m-0 text-[10px] leading-snug text-muted-foreground">
        Applies to the next voice session. Only used when voice mode is on for this
        assistant.
      </p>
    </div>
  );
}
