import { beforeEach, describe, expect, it, vi } from "vitest";
import { prepareAndPublishAssistant, retireAssistant } from "./assistantLifecycle";

const api = vi.hoisted(() => ({ runSetup: vi.fn(), createAssistant: vi.fn(), runEvalExperiment: vi.fn(), cleanupAssistantArtifacts: vi.fn(), deleteAssistant: vi.fn() }));
vi.mock("./api", () => api);
beforeEach(() => vi.resetAllMocks());

describe("demo publication and retirement", () => {
  it("publishes the prepared context then starts its baseline without awaiting it", async () => {
    const context = { agent_repo: "acme-agent", enabled_tools: [] };
    const metadata = { ls_artifacts: { workspace: "ws", eval_dataset: "dataset" }, demo_brief: ["Present"] };
    api.runSetup.mockResolvedValue({ context, metadata });
    api.createAssistant.mockResolvedValue({ assistant_id: "id", context, metadata });
    api.runEvalExperiment.mockReturnValue(new Promise(() => {}));
    const out = await prepareAndPublishAssistant({ workspace: "ws", customer: "Acme", owner: "Jo", push_prompts: true });
    expect(api.createAssistant).toHaveBeenCalledWith({ name: "Acme", context, metadata });
    expect(api.runEvalExperiment).toHaveBeenCalledWith({ assistant_id: "id", dataset: "dataset", workspace: "ws", context });
    expect(api.runSetup.mock.invocationCallOrder[0]).toBeLessThan(api.createAssistant.mock.invocationCallOrder[0]);
    expect(api.createAssistant.mock.invocationCallOrder[0]).toBeLessThan(api.runEvalExperiment.mock.invocationCallOrder[0]);
    expect(out.metadata).toEqual(metadata);
  });

  it("does not publish or start evaluations after preparation fails", async () => {
    api.runSetup.mockRejectedValue(new Error("setup failed"));
    await expect(prepareAndPublishAssistant({ workspace: "ws", customer: "Acme" })).rejects.toThrow("setup failed");
    expect(api.createAssistant).not.toHaveBeenCalled();
    expect(api.runEvalExperiment).not.toHaveBeenCalled();
  });

  it("reports cleanup failures before deleting the assistant anyway", async () => {
    const failed = [{ artifact: "project:Acme", error: "not permitted" }];
    const artifacts = { workspace: "ws", project: "Acme" };
    api.cleanupAssistantArtifacts.mockResolvedValue({ deleted: [], failed });
    api.deleteAssistant.mockResolvedValue(undefined);
    const report = vi.fn();
    await retireAssistant("id", { assistant_id: "id", graph_id: "dashboard_agent", metadata: { ls_artifacts: artifacts } }, report);
    expect(api.cleanupAssistantArtifacts).toHaveBeenCalledWith(artifacts);
    expect(report).toHaveBeenCalledWith(failed);
    expect(report.mock.invocationCallOrder[0]).toBeLessThan(api.deleteAssistant.mock.invocationCallOrder[0]);
    expect(api.deleteAssistant).toHaveBeenCalledWith("id");
  });
});
