import { describe, expect, it } from "vitest";
import { guessWebsite } from "@/lib/website";

describe("guessWebsite", () => {
  it("collapses a multi word brand into a dot com", () => {
    expect(guessWebsite("Home Depot")).toBe("homedepot.com");
    expect(guessWebsite("Frost Bank")).toBe("frostbank.com");
  });

  it("drops punctuation a domain label cannot hold", () => {
    expect(guessWebsite("AT&T")).toBe("att.com");
    expect(guessWebsite("Ben & Jerry's")).toBe("benjerrys.com");
  });

  it("drops one trailing corporate suffix", () => {
    expect(guessWebsite("Acme Inc")).toBe("acme.com");
    expect(guessWebsite("Acme Inc.")).toBe("acme.com");
    expect(guessWebsite("Contoso Holdings")).toBe("contoso.com");
  });

  it("does not eat a suffix word that is part of the name", () => {
    expect(guessWebsite("Incentive Labs")).toBe("incentivelabs.com");
  });

  it("keeps a domain or URL the user already pasted", () => {
    expect(guessWebsite("homedepot.com")).toBe("homedepot.com");
    expect(guessWebsite("https://www.homedepot.com/careers")).toBe("homedepot.com");
    expect(guessWebsite("shop.example.co.uk")).toBe("shop.example.co.uk");
  });

  it("is empty for empty or unusable input", () => {
    expect(guessWebsite("")).toBe("");
    expect(guessWebsite("   ")).toBe("");
    expect(guessWebsite("!!!")).toBe("");
  });
});
