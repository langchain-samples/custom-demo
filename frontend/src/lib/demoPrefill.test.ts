import { describe, expect, it } from "vitest";
import { matchWorkspace, readDemoPrefill } from "@/lib/demoPrefill";

describe("readDemoPrefill", () => {
  it("reads every field the GTM link carries", () => {
    const search =
      "?customer_name=Walmart&customer_website=walmart.com&use_case=Store%20ops&username=Josiah%20Coad&workspace=Josiah%20Coad's%20Workspace";
    expect(readDemoPrefill(search)).toEqual({
      customer: "Walmart",
      website: "walmart.com",
      useCase: "Store ops",
      owner: "Josiah Coad",
      workspace: "Josiah Coad's Workspace",
    });
  });

  it("leaves absent fields blank", () => {
    expect(readDemoPrefill("?customer_name=Acme")).toEqual({
      customer: "Acme",
      website: "",
      useCase: "",
      owner: "",
      workspace: "",
    });
  });

  it("is null when the link carries none of the parameters", () => {
    expect(readDemoPrefill("")).toBeNull();
    expect(readDemoPrefill("?other=1&customer_name=%20")).toBeNull();
  });
});

describe("matchWorkspace", () => {
  const workspaces = [
    { id: "aaa", name: "Default Workspace" },
    { id: "bbb", name: "Josiah Coad's Workspace" },
  ];

  it("matches a name ignoring case and whitespace", () => {
    expect(matchWorkspace("  josiah coad's workspace ", workspaces)?.id).toBe("bbb");
  });

  it("matches an id", () => {
    expect(matchWorkspace("aaa", workspaces)?.id).toBe("aaa");
  });

  it("finds nothing for a workspace this org does not have", () => {
    expect(matchWorkspace("Somebody Else", workspaces)).toBeUndefined();
  });
});
