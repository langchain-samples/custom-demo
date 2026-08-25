/**
 * The voice control, as an icon button INSIDE the composer next to send.
 *
 * It sits there because it is the same act as send: the other way to ask this assistant a
 * question. As a labelled "Talk to the assistant" button in the header it read as a mode
 * switch somewhere off to the side, and it competed with New Chat for the one part of the
 * header the eye treats as "actions".
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
  const spin = state === "connecting" || state === "thinking";
  /**
   * A PLAIN mic at rest, never the crossed-out one. As a labelled header button the
   * crossed-out mic read as "voice is currently off"; as a bare icon next to send it reads
   * as "stop talking", which is the opposite of what clicking it does. The struck-through
   * mic now means exactly one thing: the stop button.
   */
  const Icon = spin ? IconLoader2 : IconMicrophone;

  /**
   * One button, one meaning: GO TO THE VOICE VIEW. Starting a conversation from the chat
   * view used to leave you in the chat view with a session running somewhere off screen, and
   * a separate expander to find the orb - two clicks for the obvious thing. Now this takes
   * you there and the orb itself is what starts and stops.
   */
  const open = () => {
    if (!running) voice.start();
    onOpen();
  };

  /**
   * The activity line wins over the state label, and the error over both: in an icon-only
   * control the tooltip is the ONLY place either can appear. The full error text still
   * shows on the stage, which is where a retry happens.
   */
  const hint = error || (running && activity) || LABEL[state];

  return (
    <>
      <Button
        type="button"
        variant={running ? "primary" : "ghost"}
        size="icon"
        title={hint}
        aria-label={hint}
        onClick={open}
        className={
          "size-8 rounded-full" +
          (state === "error" && !running ? " text-destructive" : "")
        }
      >
        <Icon className={spin ? "size-4 animate-spin" : "size-4"} />
      </Button>
      {/* Stop stays reachable from the chat view, so ending a conversation does not require
          going back to the orb first. Only while running, so the resting composer keeps to
          one voice control. */}
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
    </>
  );
}
