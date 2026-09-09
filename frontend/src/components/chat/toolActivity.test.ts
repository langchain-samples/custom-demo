import { describe, expect, it } from "vitest";
import type { ChipData } from "./ToolChip";
import { ToolActivity, freezePendingChips } from "./toolActivity";

describe("ToolActivity", () => {
  it("admits only real IDs and preserves insertion order across updates", () => {
    const activity = new ToolActivity();
    expect(activity.upsert({ name: "read_file" })).toBe(false);
    expect(activity.upsert({ id: "", name: "read_file" })).toBe(false);
    expect(activity.snapshot()).toEqual([]);
    expect(activity.upsert({ id: "10", name: "read_file", args: { file_path: "first" } })).toBe(true);
    expect(activity.upsert({ id: "2", name: "read_file" })).toBe(true);
    expect(activity.upsert({ id: "10", name: "write_file", args: { file_path: "updated" } })).toBe(false);
    expect(activity.snapshot()).toEqual([
      { id: "10", name: "read_file", arg: "updated", result: null },
      { id: "2", name: "read_file", arg: "", result: null },
    ]);
  });

  it("does not guess a name that was absent from the first frame", () => {
    const activity = new ToolActivity();
    activity.upsert({ id: "call" });
    activity.upsert({ id: "call", name: "execute", args: { command: "ls" } });
    expect(activity.snapshot()[0]).toEqual({ id: "call", name: "", arg: "ls", result: null });
  });

  it("ignores unknown results and retains even an empty result across later argument updates", () => {
    const activity = new ToolActivity();
    expect(activity.complete(undefined, "ignored")).toBe(false);
    expect(activity.complete("missing", "ignored")).toBe(false);
    expect(activity.snapshot()).toEqual([]);
    activity.upsert({ id: "call", name: "execute" });
    expect(activity.complete("call", "")).toBe(true);
    activity.upsert({ id: "call", name: "execute", args: { command: "ls" } });
    expect(activity.snapshot()[0].result).toBe("");
    expect(activity.complete("call", "updated result")).toBe(true);
    expect(activity.snapshot()[0].result).toBe("updated result");
  });

  it("captures code only when enabled and retains it when a later frame has no code tool name", () => {
    const main = new ToolActivity({ includeCode: true });
    const sub = new ToolActivity();
    const call = { id: "call", name: "eval", args: { code: "await task({})" } };
    main.upsert(call);
    sub.upsert(call);
    expect(main.snapshot()[0]).toMatchObject({ code: "await task({})", codeLang: "js" });
    expect(main.codeFor("call")).toBe("await task({})");
    expect(main.codeFor("missing")).toBe("");
    expect(sub.codeFor("call")).toBe("");
    expect(sub.snapshot()[0]).not.toHaveProperty("code");
    expect(sub.snapshot()[0]).not.toHaveProperty("codeLang");
    main.upsert({ id: "call", args: { file_path: "file" } });
    expect(main.codeFor("call")).toBe("await task({})");
    main.upsert({ id: "call", name: "execute", args: { command: "python report.py\nprint(1)" } });
    expect(main.snapshot()[0]).toMatchObject({ name: "eval", arg: "python report.py", code: "python report.py\nprint(1)", codeLang: "bash" });
    main.upsert({ id: "call", name: "execute" });
    expect(main.snapshot()[0]).toMatchObject({ code: "", codeLang: "bash", arg: "" });
  });

  it("returns detached snapshots and keeps independent stores isolated", () => {
    const main = new ToolActivity();
    const sub = new ToolActivity();
    main.upsert({ id: "shared", name: "read_file" });
    sub.upsert({ id: "shared", name: "execute" });
    const snapshot = main.snapshot();
    main.complete("shared", "main result");
    expect(snapshot[0].result).toBeNull();
    snapshot[0].arg = "external mutation";
    snapshot.push({ id: "external", name: "read_file", arg: "", result: null });
    expect(main.snapshot()).toHaveLength(1);
    expect(main.snapshot()[0]).toMatchObject({ arg: "", result: "main result" });
    expect(sub.snapshot()[0]).toMatchObject({ name: "execute", result: null });
  });

  it("resumes only pending chips, resets stopped state, and does not mutate the paused snapshot", () => {
    const paused: ChipData[] = [
      { id: "done", name: "read_file", arg: "data", result: "" },
      { id: "stopped", name: "eval", arg: "code", result: null, stopped: true, code: "task({})", codeLang: "js" },
      { id: "waiting", name: "ask_user", arg: "choice", result: null },
    ];
    const resumed = new ToolActivity({ includeCode: true });
    resumed.resume(paused);
    expect(resumed.snapshot()).toEqual([
      { ...paused[1], stopped: false },
      { ...paused[2], stopped: false },
    ]);
    expect(resumed.upsert({ id: "waiting", name: "ask_user" })).toBe(false);
    expect(resumed.complete("stopped", "resumed result")).toBe(true);
    expect(resumed.snapshot()[0]).toMatchObject({ code: "task({})", codeLang: "js", result: "resumed result", stopped: false });
    expect(paused[1]).toMatchObject({ stopped: true, result: null });
    expect(paused[2].stopped).toBeUndefined();
  });
});

describe("freezePendingChips", () => {
  it("stops only null results without modifying previous state", () => {
    const chips: ChipData[] = [
      { id: "pending", name: "execute", arg: "", result: null },
      { id: "empty", name: "execute", arg: "", result: "" },
      { id: "done", name: "execute", arg: "", result: "result", stopped: false },
    ];
    const frozen = freezePendingChips(chips);
    expect(frozen).toEqual([{ ...chips[0], stopped: true }, chips[1], chips[2]]);
    expect(chips[0].stopped).toBeUndefined();
    expect(freezePendingChips(frozen)).toEqual(frozen);
  });
});
