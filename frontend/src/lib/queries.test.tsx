// @vitest-environment jsdom
/**
 * Cache-invalidation semantics that bit us in production.
 *
 * Not a test of react-query itself - a test that OUR defaults and OUR call sites
 * compose the way the UI needs, which is exactly where the bug lived.
 */
import { describe, expect, it, vi } from "vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render } from "@testing-library/react";
import { qk, useRefetchAssistants } from "@/lib/queries";
import * as api from "@/lib/api";

/** Mirrors the client in main.tsx: the 30s staleTime is the whole point. */
function client() {
  return new QueryClient({
    defaultOptions: { queries: { refetchOnWindowFocus: false, staleTime: 30_000, retry: 1 } },
  });
}

describe("useRefetchAssistants", () => {
  it("goes to the network even when the cached list is still fresh", async () => {
    // The delete bug: the list was seconds old, so fetchQuery served it from cache and
    // the deleted assistant stayed on screen until a hard refresh.
    const qc = client();
    const stale = [{ assistant_id: "gone", name: "Mondelez" }];
    qc.setQueryData(qk.assistants(), stale);
    const spy = vi
      .spyOn(api, "listAssistants")
      .mockResolvedValue([] as unknown as Awaited<ReturnType<typeof api.listAssistants>>);

    let refetch!: () => Promise<unknown>;
    function Probe() {
      refetch = useRefetchAssistants();
      return null;
    }
    render(
      <QueryClientProvider client={qc}>
        <Probe />
      </QueryClientProvider>,
    );

    let got: unknown;
    await act(async () => {
      got = await refetch();
    });
    expect(spy).toHaveBeenCalledTimes(1);
    expect(got).toEqual([]);
    spy.mockRestore();
  });
});
