/**
 * The compact voice control in the header: state, a stop, and a way back to the stage.
 *
 * Shown once the immersive view (VoiceStage) has been exited, so a running conversation is
 * never invisible. `useVoiceSession` owns the session; this only renders it, which is why
 * leaving the stage does not hang up.
 */

import {
  IconMicrophone,
  IconMicrophoneOff,
  IconLoader2,
  IconArrowsMaximize,
} from "@tabler/icons-react";
import { Button } from "@/components/motion/button";
import type { VoiceSessionView } from "@/lib/hooks/use-voice-session";
import type { VoiceState } from "@/lib/voice";

export interface VoiceButtonProps {
  voice: VoiceSessionView;
  /** Return to the immersive view. */
  onExpand: () => void;
}

const LABEL: Record<VoiceState, string> = {
  idle: "Talk to the assistant",
  connecting: "Connecting…",
  listening: "Listening",
  thinking: "Working on it",
  error: "Voice failed, tap to retry",
};

export function VoiceButton({ voice, onExpand }: VoiceButtonProps) {
  const { state, running, activity, error } = voice;
  const Icon = state === "connecting" || state === "thinking" ? IconLoader2 : IconMicrophone;
  const spin = state === "connecting" || state === "thinking";

  return (
    <div className="flex items-center gap-2">
      <Button
        type="button"
        variant={running ? "primary" : "outline"}
        size="sm"
        title={error || LABEL[state]}
        aria-label={LABEL[state]}
        onClick={() => (running ? voice.stop() : voice.start())}
        className="gap-1.5 rounded-full"
      >
        {running ? (
          <Icon className={spin ? "size-4 animate-spin" : "size-4"} />
        ) : (
          <IconMicrophoneOff className="size-4" />
        )}
        {/* The activity line wins over the state label: while a run is in flight it is the
            only thing saying the assistant has not stalled. */}
        <span className="text-xs">{(running && activity) || LABEL[state]}</span>
      </Button>
      {running && (
        <Button
          type="button"
          variant="ghost"
          size="icon"
          title="Back to the voice view"
          aria-label="Back to the voice view"
          onClick={onExpand}
          className="size-8 rounded-full"
        >
          <IconArrowsMaximize className="size-4" />
        </Button>
      )}
      {state === "error" && error && (
        <span className="max-w-[24rem] text-[11px] leading-snug text-red-500 select-text">
          {error}
        </span>
      )}
    </div>
  );
}
