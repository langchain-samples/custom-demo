/**
 * The compact voice control in the header: state, a stop, and a way back to the stage.
 *
 * Shown once the immersive view (VoiceStage) has been exited, so a running conversation is
 * never invisible. `useVoiceSession` owns the session; this only renders it, which is why
 * leaving the stage does not hang up.
 *
 * Clicking it goes TO the voice view - starting a session if there is not one - because
 * starting a conversation and then hunting for the orb was two clicks for one intention.
 */

import { IconMicrophone, IconMicrophoneOff, IconLoader2 } from "@tabler/icons-react";
import { Button } from "@/components/motion/button";
import type { VoiceSessionView } from "@/lib/hooks/use-voice-session";
import type { VoiceState } from "@/lib/voice";

export interface VoiceButtonProps {
  voice: VoiceSessionView;
  /** Open the immersive view. Called for a fresh start as well as for a return. */
  onOpen: () => void;
}

const LABEL: Record<VoiceState, string> = {
  idle: "Talk to the assistant",
  connecting: "Connecting…",
  listening: "Listening",
  thinking: "Working on it",
  error: "Voice failed, tap to retry",
};

export function VoiceButton({ voice, onOpen }: VoiceButtonProps) {
  const { state, running, activity, error } = voice;
  const Icon = state === "connecting" || state === "thinking" ? IconLoader2 : IconMicrophone;
  const spin = state === "connecting" || state === "thinking";

  /**
   * One button, one meaning: GO TO THE VOICE VIEW. Starting a conversation from the chat
   * view used to leave you in the chat view with a session running somewhere off screen, and
   * a separate expander to find the orb - two clicks for the obvious thing. Now the header
   * takes you there and the orb itself is what starts and stops.
   */
  const open = () => {
    if (!running) voice.start();
    onOpen();
  };

  return (
    <div className="flex items-center gap-2">
      <Button
        type="button"
        variant={running ? "primary" : "outline"}
        size="sm"
        title={error || LABEL[state]}
        aria-label={LABEL[state]}
        onClick={open}
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
      {/* Stop stays reachable from the chat view, so ending a conversation does not require
          going back to the orb first. */}
      {running && (
        <Button
          type="button"
          variant="ghost"
          size="icon"
          title="Stop the conversation"
          aria-label="Stop the conversation"
          onClick={voice.stop}
          className="size-8 rounded-full"
        >
          <IconMicrophoneOff className="size-4" />
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
