# Bench matrix

WERKHub ships a lightweight benchmark harness for comparing MCP tool variants
across models, effort levels, and workflow shapes. The goal: find the
configuration that is cheaper in tokens while equal or better in quality.

The full benchmark design rationale lives in the internal design notes and is
summarized here in the public docs.

---

## Core concepts

**BenchmarkSpec** — one task with an acceptance criterion (either an eval cassette
for correctness, or a rubric for a quality judge).

**Variant** — one way to run the task: a `label`, a `model`, an `effort` level, a
`workflow` shape (`solo` / `panel` / `loop` / …), plus free `params`.

**Result** — what you get back: `tokens_in`, `tokens_out`, `cost_usd`, `correct`
(bool), `error_count`, `quality` (0–5, judge-assigned), `duration_s`, `judged`
(bool).

| Metric | Source | Objective? |
|---|---|---|
| Tokens / cost | Usage + `cost.py` | Yes |
| Correctness | `eval` cassette pass/fail + error count | Yes |
| Quality (0–5) | Judge agent vs the spec rubric | No — declared a judgment |
| Duration | Wall-clock | Yes |

Quality is explicitly declared as a judgment, not an objective metric. If a
variant cannot run (no API key, no executor), it is recorded as `skipped` with a
reason — never silently crashed, never a fabricated result.

---

## Workflow

### 1. Write a spec and variants

Create a JSON spec file and a variants file:

```json
// bench-spec.json
{
  "task": "Summarize the PR diff and suggest review comments",
  "acceptance": {"cassette": "tests/cassettes/pr_review.json"}
}
```

```json
// bench-variants.json
[
  {"label": "gpt4o-solo",   "model": "openai/gpt-4o",         "workflow": "solo"},
  {"label": "sonnet-solo",  "model": "anthropic/claude-sonnet","workflow": "solo"},
  {"label": "haiku-panel",  "model": "anthropic/claude-haiku", "workflow": "panel"}
]
```

### 2. Run the matrix

```bash
werktools bench run bench-spec.json bench-variants.json --out results.json
```

Variants run offline-safe: without provider API keys, each is recorded as
`skipped` with a clear reason. With keys present, results are written to
`results.json` for later re-scoring without re-running.

### 3. (Optional) Judge quality

```bash
werktools bench judge results.json bench-spec.json --out judged.json
```

Runs a quality judgment pass over recorded results. Offline-safe: with no
provider key, results are written with `judged=false` rather than a fabricated
score. A scoreless judge vote leaves the result `UNJUDGED`.

### 4. Generate the report

```bash
werktools bench report results.json
werktools bench report results.json --csv-out matrix.csv
```

Output: a Markdown matrix (row = variant, columns = metrics) plus the
**Pareto frontier** — the variants that are not dominated on quality vs tokens —
and a one-line recommended configuration.

---

## Record / replay

Each run writes its output + usage to the results file. The matrix can be
re-derived from recorded results — re-scoring is cheap and deterministic without
re-running models or spending tokens.

---

## Offline-safe behavior

WERKHub never fabricates a result:

- No API key → variant is `skipped`, reason recorded.
- Multi-agent workflow shapes (`panel`, `loop`) without a caller-supplied executor → `skipped`.
- Judge call fails → `judged=false`, result still written.
- Missing results file → error reported, nothing invented.

---

## Relationship to the capability registry

The bench harness measures **MCP tool / workflow variants** — e.g. "solo vs
panel on the same task using github.create_pr." It is not a model-selection
router; model-matrix and value-routing live in WERKCommander. WERKHub's bench
answers: "given this tool and task, which configuration is cheaper and better?"
