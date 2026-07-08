# WERKHub Skill Library

A curated, **capability-mapped, scored** set of Agent Skills — each a spec-conformant
[`SKILL.md`](https://agentskills.io/specification) (cross-harness via `.agents/skills/`,
see [ADR-006](../adr/ADR-006-cross-harness-standard.md)). The goal is the *best skill per
capability*, not a flat list.

## How entries are produced — the prompt-engineering loop (skill factory)

Each entry is built by a reusable ECC dynamic workflow (`ecc-prompt-engineering-loop`):

1. **Research** — parallel scouts gather current best-practices (web) + ECC prior art.
2. **Draft** — a spec-conformant SKILL.md is drafted from the research.
3. **Refine** — a **generator/evaluator loop** scores the draft on a rubric (clarity,
   actionability, coverage, conformance, anti-template) and revises until it clears the
   bar (≥ 4.5/5) or 3 rounds — *the loop replaces the one-shot prompt*.

Re-run for any topic: `Workflow(scriptPath=…ecc-prompt-engineering-loop…, args="<topic>")`.

## Entries

_PD = progressive-disclosure package (tight SKILL.md + references/). Scores are LLM-judge panel medians (a relative quality signal, not an objective metric)._

| Capability | Skill | Risk | Score | What |
| --- | --- | --- | --- | --- |
| orchestration | [`antigravity-evidence-worker`](antigravity-evidence-worker/SKILL.md) | external | pending | Bounded Antigravity Flash scouting with explicit path scope, imported-evidence labels, and no autonomous execution. |
| media | [`local-first-gen-media-pipeline`](local-first-gen-media-pipeline/SKILL.md) · PD | external | 4/5 | Orchestration spine for local-first generative-media pipelines. Probes ComfyUI, Wan2… |
| media-generation | [`cinematic-ai-director`](cinematic-ai-director/SKILL.md) | external | 3.9/5 | Translate creative intent into structured cinematic shot directives (shot type, lens… |
| orchestration | [`loop-engineering`](loop-engineering/SKILL.md) | write | ?/5 | Designs and runs iterative generator-evaluator (GE) agent loops that replace one-sho… |
| video-production | [`lip-sync-avatar-reel`](lip-sync-avatar-reel/SKILL.md) | external | 3.8/5 | End-to-end pipeline skill for producing a lip-sync talking-head avatar reel: script … |
