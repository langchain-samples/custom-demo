/**
 * The countdown shown in a create button while assistant setup runs.
 *
 * Shared by both create paths - the Settings "+ New" dialog and first-run onboarding -
 * because they run the SAME setup and a reader should not have to learn two conventions
 * for how long it takes.
 */
import { useEffect, useRef, useState } from "react";

/** Setup's typical wall clock, measured rather than guessed. Not a deadline. */
const SETUP_SECONDS = 40;

/**
 * Seconds remaining of the expected setup time, to one decimal, or null when not running.
 * Forty seconds of a blank progressless button is long enough that people assume it has
 * hung and click away.
 *
 * It counts an ESTIMATE, not a deadline, and it KEEPS GOING past zero into negatives.
 * That looks wrong and is not: nothing here can know when the LLM will finish, so a run
 * showing "-30.0" is reporting that it took seventy seconds. Freezing at 0.0 would throw
 * away the only number that says a run was slow, which is the one worth hearing about.
 *
 * 100ms so the tenths actually move. Interval rather than an animation frame: this is one
 * short text node, and rAF would repaint it 60 times a second to show the same digit.
 */
export function useSetupCountdown(running: boolean): number | null {
  const [left, setLeft] = useState<number | null>(null);
  const startedAt = useRef(0);

  useEffect(() => {
    if (!running) {
      setLeft(null);
      return;
    }
    startedAt.current = Date.now();
    setLeft(SETUP_SECONDS);
    const id = setInterval(
      () => setLeft(SETUP_SECONDS - (Date.now() - startedAt.current) / 1000),
      100,
    );
    return () => clearInterval(id);
  }, [running]);

  return left;
}
