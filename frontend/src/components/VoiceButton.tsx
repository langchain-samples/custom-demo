/**
 * The voice control, as an icon button INSIDE the composer next to send.
 *
 * It sits there because it is the same act as send: the other way to ask this assistant a
 * question. As a labelled "Talk to the assistant" button in the header it read as a mode
 * switch somewhere off to the side, and it competed with New Chat for the one part of the
 * header the eye treats as "actions".
 *
 * ONE button, because listening and the orb are now the same state: the orb covers the chat
 * rail while a conversation is live, and leaving the orb hangs up (see App). So by the time
 * this is on screen the session is always idle, and there is nothing to stop. It used to sit
 * beside a second, stop button - the cost of letting "running, but not on the orb" exist.
 *
 * The state-driven icon and tooltip stay anyway: they cost nothing, and if a future change
 * ever makes a live session visible from here again, this degrades to something honest
 * rather than a mic button that lies about what is happening.
 */

import { IconMicrophone, IconLoader2 } from "@tabler/icons-react";
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
   * One button, one meaning: START TALKING. Which is also GO TO THE ORB, because those are
   * the same act now - the session and the view begin and end together.
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
    </>
  );
}
