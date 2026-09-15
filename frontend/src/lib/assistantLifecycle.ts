import {
  cleanupAssistantArtifacts, createAssistant, deleteAssistant, runEvalExperiment, runSetup,
  type Assistant, type SetupInput,
} from "./api";

export type CleanupFailure = { artifact: string; error: string };

/** Preparation creates external resources; publication stores the assistant referencing them. */
export async function prepareAndPublishAssistant(input: SetupInput) {
  const result = await runSetup(input);
  const context = result.context || { ls_workspace: input.workspace };
  const assistant = await createAssistant({
    name: input.customer,
    context,
    metadata: result.metadata || { owner_name: input.owner, customer: input.customer },
  });
  const metadata = result.metadata || assistant.metadata || {};
  const dataset = metadata.ls_artifacts?.eval_dataset;
  if (dataset) {
    void runEvalExperiment({
      assistant_id: assistant.assistant_id, dataset, workspace: input.workspace, context,
    });
  }
  return { assistant, metadata };
}

/** Artifact failures are reported, but do not prevent deletion of the assistant record. */
export async function retireAssistant(
  id: string,
  assistant: Assistant | null,
  report: (failed: CleanupFailure[]) => void,
): Promise<void> {
  const artifacts = assistant?.metadata?.ls_artifacts;
  if (artifacts) {
    const { failed } = await cleanupAssistantArtifacts(artifacts);
    if (failed.length) report(failed);
  }
  await deleteAssistant(id);
}
