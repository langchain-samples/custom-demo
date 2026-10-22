/**
 * A deep link that opens the "New customer demo" dialog already filled in.
 *
 * Other tools (the GTM agent's artifact links) send people here with
 * `/ui/?customer_name=Walmart&customer_website=walmart.com&use_case=...&username=Josiah%20Coad&workspace=Josiah%20Coad's%20Workspace`.
 * Every parameter is optional; the dialog opens when at least one is present. The
 * server keeps the query string through the sign-in page (`custom_demo/web/spa.py`).
 */
import type { Workspace } from "@/lib/api";

export interface DemoPrefill {
  customer: string;
  website: string;
  useCase: string;
  owner: string;
  /** A workspace NAME (matched case-insensitively) or id. */
  workspace: string;
}

/** URL parameter for each prefilled field. Part of the link contract other tools build. */
export const PREFILL_PARAMS: Record<keyof DemoPrefill, string> = {
  customer: "customer_name",
  website: "customer_website",
  useCase: "use_case",
  owner: "username",
  workspace: "workspace",
};

/** The prefill a query string asks for, or null when it carries none of the parameters. */
export function readDemoPrefill(search: string): DemoPrefill | null {
  const params = new URLSearchParams(search);
  const read = (key: keyof DemoPrefill) => (params.get(PREFILL_PARAMS[key]) || "").trim();
  const prefill: DemoPrefill = {
    customer: read("customer"),
    website: read("website"),
    useCase: read("useCase"),
    owner: read("owner"),
    workspace: read("workspace"),
  };
  return Object.values(prefill).some(Boolean) ? prefill : null;
}

/**
 * Drop the prefill parameters from the address bar, so a reload does not reopen the
 * dialog and a copied URL does not carry someone else's customer.
 */
export function clearDemoPrefillFromUrl(): void {
  const url = new URL(window.location.href);
  for (const name of Object.values(PREFILL_PARAMS)) url.searchParams.delete(name);
  window.history.replaceState(window.history.state, "", url);
}

/** The workspace a prefill names: exact id first, then name ignoring case. */
export function matchWorkspace(wanted: string, workspaces: Workspace[]): Workspace | undefined {
  const want = wanted.trim().toLowerCase();
  if (!want) return undefined;
  return (
    workspaces.find((w) => w.id.toLowerCase() === want) ||
    workspaces.find((w) => (w.name || "").trim().toLowerCase() === want)
  );
}
