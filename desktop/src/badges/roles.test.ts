import { describe, it, expect } from "vitest";
import { resolveFamily, shortModel, vendorOf, FAMILIES, resolveAccess } from "./roles";

describe("resolveAccess (WERKHub posture)", () => {
  it("maps the real WERKHub access levels", () => {
    expect(resolveAccess("admin")).toMatchObject({ label: "ADMIN", known: true });
    expect(resolveAccess("balanced")).toMatchObject({ label: "BALANCED", known: true });
    expect(resolveAccess("read_only")).toMatchObject({ label: "READ-ONLY", known: true });
  });
  it("flags unknown postures but still renders a label", () => {
    const a = resolveAccess("user");
    expect(a.known).toBe(false);
    expect(a.label).toBe("USER");
  });
});

describe("resolveFamily", () => {
  it("maps core orchestration / build / verify / knowledge roles", () => {
    expect(resolveFamily("planner").key).toBe("orchestration");
    expect(resolveFamily("architect").key).toBe("orchestration");
    expect(resolveFamily("tdd-guide").key).toBe("verify");
    expect(resolveFamily("doc-updater").key).toBe("knowledge");
  });

  it("uses suffix heuristics for the long-tail reviewer/resolver agents", () => {
    expect(resolveFamily("react-reviewer").key).toBe("review");
    expect(resolveFamily("python-reviewer").key).toBe("review");
    expect(resolveFamily("go-build-resolver").key).toBe("build");
    expect(resolveFamily("rust-build-resolver").key).toBe("build");
  });

  it("lets explicit governance/ops overrides beat the -reviewer/-architect heuristic", () => {
    expect(resolveFamily("security-reviewer").key).toBe("govern");
    expect(resolveFamily("a11y-architect").key).toBe("govern");
    expect(resolveFamily("network-config-reviewer").key).toBe("govern");
    expect(resolveFamily("network-architect").key).toBe("ops");
    expect(resolveFamily("database-reviewer").key).toBe("ops");
  });

  it("is case-insensitive and falls back to a muted unknown family", () => {
    expect(resolveFamily("PLANNER").key).toBe("orchestration");
    const unknown = resolveFamily("totally-made-up-agent");
    expect(unknown.key).toBe("unknown");
    expect(unknown.color).toBeTruthy();
  });

  it("every family has a label and a hex color", () => {
    for (const fam of Object.values(FAMILIES)) {
      expect(fam.label.length).toBeGreaterThan(0);
      expect(fam.color).toMatch(/^#|var\(/);
    }
  });
});

describe("shortModel", () => {
  it("shortens canonical model ids to a terse human label", () => {
    expect(shortModel("claude-opus-4-8")).toBe("claude opus");
    expect(shortModel("claude-sonnet-4-6")).toBe("claude sonnet");
    expect(shortModel("claude-haiku-4-5-20251001")).toBe("claude haiku");
    expect(shortModel("gpt-5.5")).toBe("gpt 5.5");
    expect(shortModel("gemini-3-pro")).toBe("gemini");
  });

  it("passes through an already-short label unchanged", () => {
    expect(shortModel("codex")).toBe("codex");
  });
});

describe("vendorOf", () => {
  it("derives the vendor from the model id", () => {
    expect(vendorOf("claude-opus-4-8")).toBe("anthropic");
    expect(vendorOf("gpt-5.5")).toBe("openai");
    expect(vendorOf("gemini-3-pro")).toBe("google");
    expect(vendorOf("mystery-model")).toBe("other");
  });
});
