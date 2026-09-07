/**
 * Release a modal's pointer lock that outlived the modal.
 *
 * Radix marks the page inert while a Dialog or Sheet is open by writing
 * `pointer-events: none` onto `document.body`, and clears it when the layer
 * unmounts. Our overlays animate out (`data-closed:animate-out`), so that
 * unmount waits on `animationend` — and a background tab throttles or skips CSS
 * animations, so the event may never arrive. The layer stays mounted, the style
 * stays on the body, and when you tab back the whole page is unclickable with no
 * visible modal to close. A refresh is the only way out.
 *
 * This clears the lock, but ONLY when nothing is actually open, so a real modal
 * is never made click-through. Deliberately narrow: it treats the symptom at the
 * two moments it becomes visible to a person (tabbing back, refocusing the
 * window), rather than polling or patching Radix.
 */
import { useEffect } from "react";

/**
 * Anything Radix has open and expects to hold the lock for.
 *
 * `[data-state="open"]` alone is far too broad: a collapsible section or an
 * accordion carries it too, and neither locks the page. These are the layers
 * that do.
 */
const OPEN_LAYER = [
  '[role="dialog"][data-state="open"]',
  '[role="alertdialog"][data-state="open"]',
  "[data-radix-popper-content-wrapper]",
].join(", ");

/** Whether the body is inert because of an inline lock (not a stylesheet rule). */
function locked(): boolean {
  return document.body.style.pointerEvents === "none";
}

export function releaseStalePointerLock(): boolean {
  if (!locked() || document.querySelector(OPEN_LAYER)) return false;
  // Remove the property rather than setting "auto": Radix restores by clearing
  // it, and leaving an explicit value behind would beat a later real lock.
  document.body.style.removeProperty("pointer-events");
  return true;
}

/** Watch for the page coming back into view, and unstick it if it is stuck. */
export function usePointerLockGuard(): void {
  useEffect(() => {
    const check = () => {
      if (document.visibilityState !== "visible") return;
      // One frame later: a layer that IS mid-unmount should be allowed to finish
      // on its own now that animations are running again, so this only fires for
      // a lock with nothing left behind it.
      requestAnimationFrame(() => {
        if (releaseStalePointerLock()) {
          console.warn("[ui] cleared a stale pointer-events lock left by a closed overlay");
        }
      });
    };
    document.addEventListener("visibilitychange", check);
    window.addEventListener("focus", check);
    return () => {
      document.removeEventListener("visibilitychange", check);
      window.removeEventListener("focus", check);
    };
  }, []);
}
