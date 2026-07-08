// ─────────────────────────────────────────────────────────────────────────────
// WERKHub access posture — the access level a connecting agent's PROFILE carries.
// This is WERKHub's real "role" axis (verified in werktools: admin / balanced /
// read_only). Distinct from the engineering-role taxonomy further down, which is
// a PORTABLE asset for WERKAgent's crew (see PORTABLE.md), not a WERKHub concept.
// ─────────────────────────────────────────────────────────────────────────────

export interface Access {
  level: string;
  label: string;
  color: string;
  known: boolean;
}

const ACCESS: Record<string, { label: string; color: string }> = {
  admin: { label: "ADMIN", color: "#b9c2d6" }, // audit — full governance power
  balanced: { label: "BALANCED", color: "#6b78d6" }, // interact — default posture
  read_only: { label: "READ-ONLY", color: "#97a0b0" }, // muted — observe only
  readonly: { label: "READ-ONLY", color: "#97a0b0" },
};

export function resolveAccess(level: string): Access {
  const key = level.trim().toLowerCase();
  const hit = ACCESS[key];
  if (hit) return { level: key, label: hit.label, color: hit.color, known: true };
  return { level: key, label: level.toUpperCase(), color: "#97a0b0", known: false };
}

// ─────────────────────────────────────────────────────────────────────────────
// PORTABLE ASSET (for WERKAgent crew badges — NOT used by WERKHub itself).
// Expert-role taxonomy: ~30+ ECC agent types collapse into 7 color families.
// Color = family, badge text = the specific role. Plus model-identity helpers.
// Self-contained: depends only on this file. Lift roles.ts + RoleBadge into
// WERKAgent as-is. See src/badges/PORTABLE.md.
// ─────────────────────────────────────────────────────────────────────────────

export type FamilyKey =
  | "orchestration"
  | "build"
  | "review"
  | "verify"
  | "govern"
  | "knowledge"
  | "ops"
  | "unknown";

export interface Family {
  key: FamilyKey;
  label: string;
  color: string;
}

export const FAMILIES: Record<FamilyKey, Family> = {
  orchestration: { key: "orchestration", label: "Orchestration", color: "#7b88db" },
  build: { key: "build", label: "Build", color: "#a988d6" },
  review: { key: "review", label: "Review", color: "#6fb0d8" },
  verify: { key: "verify", label: "Verify", color: "#5cae7a" },
  govern: { key: "govern", label: "Govern", color: "#d7a13a" },
  knowledge: { key: "knowledge", label: "Knowledge", color: "#4fb3a6" },
  ops: { key: "ops", label: "Ops", color: "#c08aa0" },
  unknown: { key: "unknown", label: "Role", color: "#97a0b0" },
};

// Explicit overrides win over the suffix heuristics below (e.g. security-reviewer
// is governance, not a plain code review; network-architect is ops, not planning).
const EXPLICIT: Record<string, FamilyKey> = {
  planner: "orchestration",
  architect: "orchestration",
  "code-architect": "orchestration",
  "code-explorer": "orchestration",
  conductor: "orchestration",
  plan: "orchestration",
  explore: "orchestration",

  "refactor-cleaner": "build",
  "code-simplifier": "build",
  "gan-generator": "build",
  "opensource-forker": "build",
  "opensource-packager": "build",

  "type-design-analyzer": "review",
  "comment-analyzer": "review",

  "tdd-guide": "verify",
  "e2e-runner": "verify",
  "pr-test-analyzer": "verify",
  "gan-evaluator": "verify",
  "agent-evaluator": "verify",
  "silent-failure-hunter": "verify",

  "security-reviewer": "govern",
  "a11y-architect": "govern",
  "swarm-merge-checker": "govern",
  "wc-merge-checker": "govern",
  "opensource-sanitizer": "govern",
  "network-config-reviewer": "govern",

  "swarm-researcher": "knowledge",
  "docs-lookup": "knowledge",
  "doc-updater": "knowledge",
  "marketing-agent": "knowledge",
  "seo-specialist": "knowledge",
  "spec-miner": "knowledge",
  "claude-code-guide": "knowledge",

  "database-reviewer": "ops",
  "network-architect": "ops",
  "network-troubleshooter": "ops",
  "homelab-architect": "ops",
  "harness-optimizer": "ops",
  "performance-optimizer": "ops",
  "loop-operator": "ops",
  "mle-reviewer": "ops",
};

export function resolveFamily(role: string): Family {
  const id = role.trim().toLowerCase();
  const explicit = EXPLICIT[id];
  if (explicit) return FAMILIES[explicit];
  if (id.endsWith("-build-resolver") || id.endsWith("-resolver")) return FAMILIES.build;
  if (id.endsWith("-reviewer")) return FAMILIES.review;
  if (id.endsWith("-architect")) return FAMILIES.orchestration;
  if (id.includes("frontend") || id.includes("backend")) return FAMILIES.build;
  return FAMILIES.unknown;
}

// ── model identity ──────────────────────────────────────────────────────────

export type Vendor = "anthropic" | "openai" | "google" | "other";

export const VENDOR_COLOR: Record<Vendor, string> = {
  anthropic: "#c4ccd6",
  openai: "#74aa9c",
  google: "#8ab4f8",
  other: "#97a0b0",
};

export function vendorOf(model: string): Vendor {
  const m = model.toLowerCase();
  if (m.includes("claude") || m.includes("opus") || m.includes("sonnet") || m.includes("haiku") || m.includes("fable"))
    return "anthropic";
  if (m.includes("gpt") || m.includes("codex") || m.startsWith("o1") || m.startsWith("o3")) return "openai";
  if (m.includes("gemini")) return "google";
  return "other";
}

// Terse human label: "claude-opus-4-8" -> "claude opus", "gpt-5.5" -> "gpt 5.5".
export function shortModel(model: string): string {
  const m = model.toLowerCase();
  const tier = ["opus", "sonnet", "haiku", "fable"].find((t) => m.includes(t));
  if (m.includes("claude") || tier) return tier ? `claude ${tier}` : "claude";
  if (m.includes("gemini")) return "gemini";
  if (m.includes("codex")) return "codex";
  if (m.includes("gpt")) {
    const ver = m.match(/gpt[-\s]?([0-9]+(?:\.[0-9]+)?)/);
    return ver ? `gpt ${ver[1]}` : "gpt";
  }
  return model;
}
