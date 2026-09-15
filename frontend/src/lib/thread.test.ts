// @vitest-environment jsdom
/**
 * The thread id in the URL, which is what makes a refresh keep the conversation.
 *
 * In the query string rather than storage on purpose: the link is worth
 * sending, Back works, and a second tab is a second conversation instead of
 * two views fighting over one id.
 */
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ensureThread, resetThread, savedThreadId } from "@/lib/api";

beforeEach(() => {
  resetThread();
  window.history.replaceState(null, "", "/");
});
afterEach(() => vi.unstubAllGlobals());

it("keeps a minted thread in the URL so a refresh resumes it", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => ({ ok: true, json: async () => ({ thread_id: "t-1" }) })));
  expect(await ensureThread()).toBe("t-1");
  expect(savedThreadId()).toBe("t-1");
});

it("adopts the thread already in the URL instead of minting one", async () => {
  const f = vi.fn(async () => ({ ok: true, json: async () => ({ thread_id: "t-new" }) }));
  vi.stubGlobal("fetch", f);
  window.history.replaceState(null, "", "/?thread=t-existing");
  expect(await ensureThread()).toBe("t-existing");
  expect(f).not.toHaveBeenCalled();
});

it("clears the URL on New Chat, or a refresh resumes the old conversation", () => {
  window.history.replaceState(null, "", "/?thread=t-old");
  resetThread();
  expect(savedThreadId()).toBeNull();
});
