import type { Assistant, UpdateAssistantInput } from "./api";

type Edit = (current: Assistant) => UpdateAssistantInput;
type Channel = "branding" | "model" | "tools" | "mcp";

interface AssistantStore {
  get: (id: string) => Assistant | undefined;
  save: (id: string, patch: UpdateAssistantInput) => Promise<Assistant>;
  saved: (assistant: Assistant) => void;
}

/** PATCH replaces whole objects, so each assistant has one ordered write queue. */
export class AssistantEdits {
  private readonly timers = new Map<string, ReturnType<typeof setTimeout>>();
  private readonly pending = new Map<string, Promise<void>>();

  private readonly store: AssistantStore;

  constructor(store: AssistantStore) {
    this.store = store;
  }

  schedule(id: string, channel: Channel, edit: Edit, delay: number): void {
    const key = `${id}:${channel}`;
    clearTimeout(this.timers.get(key));
    this.timers.set(key, setTimeout(() => {
      this.timers.delete(key);
      const previous = this.pending.get(id) ?? Promise.resolve();
      const next = previous.then(async () => {
        const current = this.store.get(id);
        if (!current) return;
        try {
          this.store.saved(await this.store.save(id, edit(current)));
        } catch {
          // The session keeps its preview; only acknowledged edits enter the saved cache.
        }
      });
      this.pending.set(id, next);
      void next.then(() => {
        if (this.pending.get(id) === next) this.pending.delete(id);
      });
    }, delay));
  }

  dispose(): void {
    for (const timer of this.timers.values()) clearTimeout(timer);
    this.timers.clear();
  }
}
