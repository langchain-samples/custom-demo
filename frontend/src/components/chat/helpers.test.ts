import { describe, expect, it } from "vitest";
import { groupConsecutive } from "@/components/chat/helpers";

/** Only `name` matters to the grouping; `id` is here to make runs identifiable. */
const chip = (id: string, name: string) => ({ id, name });

describe("groupConsecutive", () => {
  it("is empty for no items", () => {
    expect(groupConsecutive([])).toEqual([]);
  });

  it("keeps a single call as a run of one", () => {
    expect(groupConsecutive([chip("a", "execute")])).toEqual([[chip("a", "execute")]]);
  });

  it("merges adjacent calls to the same tool", () => {
    const runs = groupConsecutive([
      chip("a", "execute"),
      chip("b", "execute"),
      chip("c", "execute"),
    ]);
    expect(runs).toHaveLength(1);
    expect(runs[0].map((c) => c.id)).toEqual(["a", "b", "c"]);
  });

  it("keeps different tools apart", () => {
    const runs = groupConsecutive([chip("a", "execute"), chip("b", "read_file")]);
    expect(runs.map((r) => r.length)).toEqual([1, 1]);
  });

  it("treats a later burst of the same tool as its own run", () => {
    // execute x2, read_file, execute x3 is THREE runs: the second burst followed a read
    // and is separate work, not a continuation of the first.
    const runs = groupConsecutive([
      chip("a", "execute"),
      chip("b", "execute"),
      chip("c", "read_file"),
      chip("d", "execute"),
      chip("e", "execute"),
      chip("f", "execute"),
    ]);
    expect(runs.map((r) => r.length)).toEqual([2, 1, 3]);
    expect(runs.map((r) => r[0].name)).toEqual(["execute", "read_file", "execute"]);
  });

  it("spans model turns, because it groups by position only", () => {
    // The agent called execute twice, saw the results, then called it three more times
    // in a later turn. Those land next to each other in one flat chip list, so they are
    // one run: where the turn boundary fell is not something a reader cares about.
    const turnOne = [chip("a", "execute"), chip("b", "execute")];
    const turnTwo = [chip("c", "execute"), chip("d", "execute"), chip("e", "execute")];
    const runs = groupConsecutive([...turnOne, ...turnTwo]);
    expect(runs).toHaveLength(1);
    expect(runs[0].map((c) => c.id)).toEqual(["a", "b", "c", "d", "e"]);
  });

  it("keys a growing run on its first item, so the row is stable while streaming", () => {
    const first = groupConsecutive([chip("a", "execute")]);
    const later = groupConsecutive([chip("a", "execute"), chip("b", "execute")]);
    expect(later[0][0].id).toBe(first[0][0].id);
  });

  it("reproduces the shape from the reported screenshot", () => {
    const runs = groupConsecutive([
      chip("1", "read_file"),
      chip("2", "ls"),
      chip("3", "read_file"),
      chip("4", "execute"),
      chip("5", "task"),
      chip("6", "task"),
      chip("7", "read_file"),
      chip("8", "execute"),
      chip("9", "execute"),
      chip("10", "execute"),
      chip("11", "read_file"),
      chip("12", "execute"),
      chip("13", "execute"),
      chip("14", "execute"),
      chip("15", "execute"),
      chip("16", "execute"),
      chip("17", "read_file"),
      chip("18", "write_file"),
    ]);
    // 18 rows become 11, and the two longest bursts are the ones worth folding away.
    expect(runs).toHaveLength(11);
    expect(runs.filter((r) => r.length > 1).map((r) => r.length)).toEqual([2, 3, 5]);
  });
});
