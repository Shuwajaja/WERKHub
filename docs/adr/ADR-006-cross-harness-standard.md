# ADR-006 — Cross-harness skill + hook standard: adopt, don't invent

**Status:** Accepted — 2026-06-21

## Context

WERKHub wants one source of truth for skills, rules, and hooks that every agent
harness (Claude Code, OpenAI Codex, Cursor, Windsurf, Gemini CLI, GitHub Copilot,
Zed, …) can consume — instead of a thousand per-harness copies. The open question
was: invent a WERKHub format, or ride an existing standard?

A research sweep (5 parallel scouts + synthesis, 2026-06-21) plus maintainer
verification against primary sources found that real cross-vendor standards now
exist, so the answer is **adopt, not invent**:

- **AGENTS.md** is an open standard under the **Linux Foundation / Agentic AI
  Foundation** (OpenAI/Anthropic/Block co-founded the AAIF on 2025-12-09, donating
  AGENTS.md + MCP + goose; 60,000+ repos; adopted by Codex, Cursor, Gemini CLI,
  Copilot, VS Code, Devin, Amp, Factory, Jules). *Verified.*
- **SKILL.md** is a published open spec at **agentskills.io** — required frontmatter
  is exactly `name` (≤64, lowercase + single hyphens, matches the skill's directory
  name) and `description` (≤1024, non-empty); optional `license`/`compatibility`/
  `metadata`/`allowed-tools`; `scripts/`, `references/`, `assets/` dirs; progressive
  disclosure. The cross-harness install path is `.agents/skills/<name>/SKILL.md`.
  *Spec format verified at agentskills.io/specification; per-harness `.agents/skills/`
  adoption is high-confidence research, not all re-verified here.*
- **Hooks** have **no** cross-vendor standard, and MCP has no native hook path:
  notifications are fire-and-forget (cannot gate a call in flight); MCP **prompts**
  were explicitly rejected as a skill carrier by the "Skills over MCP" WG (they are
  user-controlled, not agent-discoverable). *Verified for prompts/notifications;
  SEP-2640 and AgentHook below are research-stage, not independently verified.*

## Decision

1. **Rules → AGENTS.md** as the single authoritative project-context source.
   `export_rules` already treats it as the convergence point (CLAUDE.md `@import`s
   it; Cursor gets `.mdc`).
2. **Skills → SKILL.md (agentskills.io), exported to `.agents/skills/<name>/SKILL.md`.**
   `render_skill_md` emits spec-conformant frontmatter (`name` + `description`); the
   werktools-specific fields (`id`/`title`/`tags`/`risk`/`profiles`/`source`) live under
   the optional `metadata` map so strict parsers accept the file. One directory per
   skill, `name` == directory name. *(Implemented in the change that adds this ADR.)*
3. **MCP-native skill delivery → MCP Resources (SEP-2640, `skill://`) is the future
   path** — track it; do **not** use MCP prompts (WG-rejected). Not yet wired: no
   shipping host is confirmed to consume `skill://index.json`. *(Research-stage.)*
4. **Hooks → portable manifest + per-host shim.** Keep `hook-catalog.json` as the
   single declaration of what hooks a skill wants (event/host/description); the
   per-host config (Claude `settings.json`, Codex `hooks.json`, Cursor `.cursor/
   hooks.json`, …) stays a thin shim the operator owns. Converge on the common
   envelope (`session_id`/`cwd`/`event`/`tool_*`; exit-2 = block). Watch **AgentHook
   v0.2** (pre-1.0, not broadly adopted) but do not block on it. *(Research-stage.)*
5. **Agent-body (`wc-agentbody`) wiring is out of scope** for WERKHub — it is a
   WERKCommander-internal concern (see ADR-003 WERK/WERKHub boundary).

## Consequences

- WERKHub's exported skills are now spec-conformant SKILL.md and load natively in the
  `.agents/skills/`-reading harnesses — one source, many harnesses, no per-harness
  rewrite.
- The hub does NOT execute hooks; it inventories them. Hook execution stays host-side
  by design (no foreign-code runner in the core).
- Honest gaps remaining: the MCP-native (`skill://`) delivery and any hook standard are
  not yet shippable; both are tracked, not claimed.

## Sources (verified 2026-06-21)

- Linux Foundation — AAIF formation (AGENTS.md + MCP + goose):
  <https://www.linuxfoundation.org/press/linux-foundation-announces-the-formation-of-the-agentic-ai-foundation>
- agentskills.io — SKILL.md specification: <https://agentskills.io/specification>
- TechCrunch (2025-12-09) — AAIF launch:
  <https://techcrunch.com/2025/12/09/openai-anthropic-and-block-join-new-linux-foundation-effort-to-standardize-the-ai-agent-era/>

*Research-stage (not independently verified): SEP-2640 "Skills over MCP" Resources
extension, exact per-harness `.agents/skills/` adoption matrix, AgentHook v0.2.*
