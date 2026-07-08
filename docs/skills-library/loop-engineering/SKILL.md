---
name: loop-engineering
description: "Designs and runs iterative generator-evaluator (GE) agent loops that replace one-shot prompts with autonomous, quality-gated refinement cycles. A 3-round GE loop costs approximately 4–6x a single-shot call; cost-justified when one-shot failure rate on rubric dimensions exceeds ~40%."
metadata:
  werk_category: "orchestration"
  werk_risk: "write"
  werk_tags: "orchestration, agentic, refinement, evaluation, quality-gate, generator-evaluator, self-refine, rubric, feedback-loop, prompt-engineering"
  werk_source: "ecc-prompt-engineering-loop"
---
# Loop Engineering: Iterative Generator-Evaluator Agent Loops

Replace one-shot prompts with autonomous, quality-gated refinement cycles that exploit the **solver-verifier asymmetry**: LLMs verify more reliably than they generate. A separate evaluator agent consistently outperforms pure self-refinement because it eliminates shared bias between generator and critic.

---

## When to Use

Use a GE loop when ALL of the following hold:

1. You can write a rubric — a set of atomic, independently scorable dimensions with explicit numeric thresholds.
2. An external signal (compiler, test runner, schema validator, CoVe sub-queries) or a separate evaluator model can verify quality without seeing the generator's reasoning.
3. The quality gain from 2-3 additional iterations is worth the latency and token cost.
4. The task is not open-ended creative work without objective evaluation criteria.

**Cost heuristic (apply before committing to a loop):** A 3-round GE loop costs approximately 4–6x a single-shot call at the same model tier (generator call × N rounds + evaluator call × N rounds + overhead). The loop is cost-justified when your observed or estimated one-shot failure rate on rubric dimensions exceeds ~40%. Below that threshold, targeted prompt improvement on the single-shot path is usually cheaper. For code with compiler+test verification, mechanical confirmation of success makes the loop almost always justified regardless of one-shot failure rate.

**Do not use a GE loop** for pure creative work, world-knowledge retrieval tasks where errors are undetectable in generated content alone, or any case where you cannot pre-define rubric dimensions before the first iteration.

---

## Architecture Selection

Choose the pattern before you write any prompt:

| Domain | Evaluation Signal | Pattern |
|---|---|---|
| Code | Compiler + test runner | Schema/Validator (Pattern 4) |
| Factual prose | CoVe independent sub-queries | Chain of Verification (Pattern 3) |
| Structured data | Pydantic/Zod schema | Schema/Validator (Pattern 4) |
| Technical writing | Separate evaluator model + rubric | Evaluator-Refiner (Pattern 2) |
| Policy / safety text | Allowlist/blocklist checker | Schema/Validator (Pattern 4) |
| High-stakes output | Human review at threshold | Human-in-the-Loop (Pattern 6) |

**Never use the same model instance for both generator and evaluator** — it propagates identical biases and will amplify original errors rather than correct them.

---

## Step-by-Step Method

### Step 1 — Define the Rubric First (Mandatory)

Before writing any generation prompt, decompose the output requirement into atomic, independently scorable dimensions. Rubric errors corrupt every downstream iteration.

```yaml
# rubric.yaml
dimensions:
  faithfulness:
    description: "All claims are supported by provided context"
    threshold: 10   # must be 10/10 — hard gate
    weight: 1.0
  completeness:
    description: "All required sections are present and non-empty"
    threshold: 8
    weight: 0.8
  style:
    description: "Matches brand tone guidelines"
    threshold: 6
    weight: 0.5
  atomicity:
    description: "Each point is a single, non-compound statement"
    threshold: 7
    weight: 0.6

promotion_condition: "faithfulness == 10 AND weighted_average >= 7.5"
```

Faithfulness and correctness dimensions must be hard gates (all-or-nothing). Style and tone dimensions can be soft thresholds. Define `promotion_condition` before the first generator call.

### Step 2 — Initialize the Loop State Ledger

Track every variant in an append-only ledger. This keeps iteration history durable across context resets and makes rollback safe.

```jsonl
{"round": 0, "variant_id": "v0", "generator_prompt_hash": "abc123", "scores": {}, "weighted_avg": null, "status": "seed", "delta": null}
```

Also initialize a structured session-state document (four required fields):

```markdown
## Loop State
- **intent**: Generate a technical summary of the migration plan for audience: SRE team
- **changes_made**: (empty at round 0)
- **decisions_taken**: Rubric defined; faithfulness hard-gated at 10
- **next_steps**: Generate v0 draft
```

Update this document incrementally after each round. Do not re-summarize from scratch — append deltas only.

### Step 3 — Generate

Prompt the generator with the task, any few-shot examples, and a structured feedback block from the previous round (empty on round 0).

**Generator prompt template (literal — copy verbatim, substitute only the `{…}` placeholders):**

```
TASK: {task_description}

PREVIOUS_FEEDBACK:
{evaluator_structured_feedback_from_prior_round | "None — first iteration"}

CONSTRAINTS:
- Address every FAIL dimension listed in PREVIOUS_FEEDBACK before adding anything new
- Do not add new content to compensate for a failed dimension
```

The placeholders `{task_description}` and `{evaluator_structured_feedback_from_prior_round}` are the only lines you replace. The two `CONSTRAINTS` lines and the section headings (`TASK:`, `PREVIOUS_FEEDBACK:`) are load-bearing — preserve them verbatim in every round.

**Example instantiation (illustrative only — shows what filled-in values look like, not a template):**

```
TASK: Write a one-page SRE-facing summary of the database migration plan.
      Audience: on-call SRE team. Format: bullet list per phase, max 300 words.

PREVIOUS_FEEDBACK:
None — first iteration

CONSTRAINTS:
- Address every FAIL dimension listed in PREVIOUS_FEEDBACK before adding anything new
- Do not add new content to compensate for a failed dimension
```

The constraint block is critical: without it, generators on constrained tasks tend to produce progressively longer outputs that add rather than correct.

### Step 4 — Evaluate

Run the evaluator agent with the rubric attached. The evaluator must NOT see the generator's chain-of-thought — only the final output.

For code: run the compiler and test suite; treat exit code as the primary signal.

For prose: use a separate model with a structured scoring prompt:

```
You are an evaluator. Score the DRAFT against each rubric dimension.
For each dimension, output:
  - score (integer 0-10)
  - verdict (PASS | FAIL)
  - evidence (quote the specific text that drove the score)
  - required_change (one sentence, imperative, only on FAIL)

Do not suggest additions. Only flag what is wrong.

RUBRIC: {rubric_yaml}
DRAFT: {generator_output}
```

Output format (structured, parseable):

```json
{
  "round": 1,
  "scores": {"faithfulness": 10, "completeness": 7, "style": 5, "atomicity": 8},
  "weighted_avg": 7.3,
  "verdict": "FAIL",
  "failing_dimensions": [{"dimension": "style", "required_change": "Replace passive constructions with active voice in all three bullet points"}],
  "promotion_met": false
}
```

### Step 5 — Check Termination (Defense in Depth)

Check all five signals in order. Exit on the first signal that fires:

```python
def should_terminate(state):
    if state.promotion_met:               # threshold met
        return "PROMOTE"
    if state.round >= MAX_ITERATIONS:     # hard cap (default: 3, max: 5)
        return "CAP_REACHED"
    if state.last_n_actions_identical(n=2):  # duplicate action detection
        return "STUCK"
    if state.elapsed_seconds > WALL_CLOCK_TIMEOUT:  # wall-clock timeout
        return "TIMEOUT"
    if state.last_error in UNRECOVERABLE_ERROR_CLASSES:
        return "ERROR_ESCALATE"
    return None  # continue
```

Default `MAX_ITERATIONS = 3`. Evidence: rounds 1-2 capture approximately 75% of reachable improvement; diminishing returns dominate after round 3. Only raise the cap to 5 for tasks with objective external verifiers (compilers, test suites). Never exceed 5 for prose or subjective evaluation.

### Step 6 — Feed Back and Repeat (Delta-Scoped Evaluation)

Inject only the failing dimensions and their `required_change` strings into the next generator prompt. Do not re-inject passing dimensions — this wastes tokens and risks regression.

From round 2 onward, scope the evaluator to re-score only the dimensions that changed. Use this evaluator prompt fragment to enforce delta-scoped evaluation:

```
You are an evaluator running a DELTA-SCOPED re-evaluation.

The following dimensions were PASS in the previous round and have NOT changed.
Do NOT re-score them — carry their prior scores forward unchanged:
  SKIP (already PASS): {comma-separated list of passing dimension names, e.g. "faithfulness, atomicity"}

Evaluate ONLY these dimensions, which were FAIL and have been revised:
  EVALUATE: {comma-separated list of failing dimension names, e.g. "style, completeness"}

For each dimension in EVALUATE, output:
  - score (integer 0-10)
  - verdict (PASS | FAIL)
  - evidence (quote the specific text that drove the score)
  - required_change (one sentence, imperative, only on FAIL)

RUBRIC: {rubric_yaml}
DRAFT: {generator_output}
```

**Delta-scoped weighted average formula (apply after every delta re-evaluation):**

After the evaluator returns new scores for the EVALUATE-set dimensions, merge them with the carried-forward scores from the SKIP-set dimensions to compute the updated `weighted_avg`:

```
weighted_avg =
  ( sum of (score_i * weight_i) for every dimension i in EVALUATE-set using the NEW score
  + sum of (score_i * weight_i) for every dimension i in SKIP-set using the CARRIED-FORWARD score )
  / sum of weight_i for ALL dimensions (EVALUATE-set + SKIP-set combined)
```

Using the rubric from Step 1 as an example (weights: faithfulness 1.0, completeness 0.8, style 0.5, atomicity 0.6; total weight = 2.9):

- SKIP-set (carried forward): faithfulness = 10, atomicity = 8
- EVALUATE-set (new scores): completeness = 9, style = 8
- weighted_avg = (9×0.8 + 8×0.5 + 10×1.0 + 8×0.6) / 2.9 = (7.2 + 4.0 + 10.0 + 4.8) / 2.9 = 26.0 / 2.9 ≈ **8.97**

Re-check the `promotion_condition` against this merged result before deciding whether to continue or promote.

### Step 7 — Promote or Escalate

On `PROMOTE`: commit the winning output, append the final ledger entry with `status: promoted`, update the session-state document.

On `CAP_REACHED` without promotion: surface the best-scoring variant with its score and remaining failures. Do not silently return the last iteration as if it passed.

On `STUCK` or `TIMEOUT`: apply the following ordered triage. Stop when one attempt resolves the failure, and escalate to human review if all three steps are exhausted:

1. **Inject a worked counterexample** for the failing dimension. Provide one concrete example of the correct form directly in the generator system prompt (e.g., a sentence rewritten from passive to active voice, or a correctly atomized bullet). This is the cheapest fix and resolves ~60% of stuck cases.
2. **Add explicit chain-of-thought** to the generator system prompt: prepend "Before writing, reason step-by-step about how to satisfy the failing dimension, then write the output." Use this when the counterexample alone did not help or the failure involves multi-step reasoning (e.g., structural reorganization, faithfulness gaps).
3. **Escalate to human review** if steps 1 and 2 both failed to resolve the dimension.

**Definition of a variation attempt:** a variation attempt is a generator call that includes a changed system prompt — either a counterexample added (step 1) or a chain-of-thought instruction added (step 2). Re-running an identical prompt without any system-prompt change does NOT count as a new variation attempt; doing so should immediately trigger the next triage step rather than consuming an attempt slot. Cap at **2 variation attempts per dimension** (one for step 1, one for step 2) before escalating to human review.

On `ERROR_ESCALATE`: invoke `agent-introspection-debugging` for failure capture and contained recovery.

---

## Concrete Example: Technical Summary Refinement

**Task**: Generate a one-page SRE-facing summary of a database migration plan.

**Rubric**: faithfulness >= 10 (hard gate), completeness >= 8, style >= 7, atomicity >= 7. Promotion: faithfulness == 10 AND weighted_avg >= 7.5. Weights: faithfulness 1.0, completeness 0.8, style 0.5, atomicity 0.6 (total = 2.9).

**Round 0** (seed): Generator produces draft. Evaluator scores: faithfulness 10, completeness 6, style 5, atomicity 8. weighted_avg = (10×1.0 + 6×0.8 + 5×0.5 + 8×0.6) / 2.9 = (10.0 + 4.8 + 2.5 + 4.8) / 2.9 = 22.1 / 2.9 ≈ 7.6. Verdict: FAIL (style < 7 threshold; completeness < 8 threshold). Failing: completeness (missing rollback section), style (passive voice throughout).

**Round 1**: Generator receives PREVIOUS_FEEDBACK with two required_change entries: "Add a rollback section covering steps 3-5 of the runbook" and "Rewrite all passive constructions as active voice." Generator addresses only those two items. Evaluator runs delta-scoped re-evaluation on completeness and style only (faithfulness and atomicity carried forward as PASS). New scores: completeness 9, style 8. Merged weighted_avg = (10×1.0 + 9×0.8 + 8×0.5 + 8×0.6) / 2.9 = (10.0 + 7.2 + 4.0 + 4.8) / 2.9 = 26.0 / 2.9 ≈ 8.97. Verdict: PASS. Promotion gate met. Loop exits at round 1.

**Ledger** (final state):

```jsonl
{"round": 0, "variant_id": "v0", "scores": {"faithfulness": 10, "completeness": 6, "style": 5, "atomicity": 8}, "weighted_avg": 7.6, "status": "fail", "delta": null}
{"round": 1, "variant_id": "v1", "scores": {"faithfulness": 10, "completeness": 9, "style": 8, "atomicity": 8}, "weighted_avg": 8.97, "status": "promoted", "delta": "+1.37"}
```

---

## Pitfalls

**P1 — Same model for generation and evaluation (critical).**
The evaluator inherits the generator's blind spots. Self-refine without external grounding amplifies original errors. Use a separate model, a deterministic verifier, or Chain of Verification sub-queries that run without seeing the draft.

**P2 — No exit condition defined before round 0 (critical).**
Without a pre-defined `promotion_condition`, the loop runs until the cap and the result is arbitrary. Define the rubric and promotion gate before any generation call.

**P3 — Strategy unchanged on repeated failure (critical).**
If round N and round N+1 both fail on the same dimension with the same `required_change`, the generator is stuck. Follow the ordered triage in Step 7: counterexample first, then chain-of-thought injection, then escalate after two failed variation attempts. Re-running the same prompt without a system-prompt change does not count as a variation attempt.

**P4 — Full re-evaluation of unchanged sections from round 2 onward.**
Full re-evaluation wastes tokens and reduces evaluator precision (the evaluator will find new problems in already-passing sections). Use the delta-scoped evaluator prompt from Step 6 and merge scores using the explicit weighted average formula.

**P5 — Context explosion.**
Full conversation history accumulated across 5 rounds of a long-output task can consume most of the context window. Use the structured session-state document (intent / changes-made / decisions-taken / next-steps) and strip prior drafts from context after extracting evaluator feedback.

**P6 — Loops on tasks without objective evaluation criteria.**
Applying a GE loop to open-ended creative writing or tasks requiring external world knowledge produces the appearance of improvement without the substance. The evaluator will reward style improvements while missing factual errors it cannot detect.

**P7 — Cap set too high for prose tasks.**
For prose, style, and non-deterministic domains, setting MAX_ITERATIONS above 3 rarely adds value and substantially increases cost and latency. Reserve higher caps for code with compiler+test verification.

**P8 — Feedback injection without constraint block.**
Without explicit "address FAIL dimensions before adding anything new," generators on constrained tasks produce progressively longer outputs that add rather than correct, causing completeness scores to rise while introducing new atomicity or style failures.

---

## Related ECC Skills

- `agent-self-evaluation` — 5-axis rubric format; adapt as the evaluator's scoring output schema.
- `benchmark-optimization-loop` — variant-tracking table format and JSONL ledger pattern with promotion gates; reuse for the loop state ledger (covers the recursive-decision-ledger pattern).
- `autonomous-loops` — structural template (Dispatch → Evaluate → Refine → Loop, max N cycles) for bounded GE loops applied to RAG/context narrowing (covers the iterative-retrieval pattern).
- `agent-introspection-debugging` — failure capture and recovery for ERROR_ESCALATE exits.
