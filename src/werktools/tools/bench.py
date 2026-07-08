"""Bench: offline benchmark matrix + Pareto frontier + live runner + judge.

Domain: WERKHub measures MCP-tool / workflow VARIANTS (solo vs panel vs loop on
the same spec).  The MODEL matrix / value-router is WERKCommander's domain — do
NOT add model-selection logic here.

Phase bench-a: fully offline — no live model calls, no daemon threads.
Phase bench-b: pluggable live runner + record/replay (offline-testable).
Phase bench-c: pluggable quality judge (offline-testable).

Key constraints:
- Pure stdlib in the import path (no new dependencies).
- The only model-call path is via hub/workers.py:dispatch_worker (optional
  [worker] extra) reached through injected seams for offline testing.
- No live model calls at import time or in tests.
- Key handling: PRESENCE-ONLY.  Never read / log / return a key value.
- Honesty: 'panel'/'loop' without a caller-supplied executor -> 'skipped' with
  a clear reason.  NEVER fabricate numbers.
- Immutability: frozen dataclasses, from_dict/to_dict, no shared-state mutation.
"""

from __future__ import annotations

import csv
import io
import json
import os
import statistics
import time
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

# ---------------------------------------------------------------------------
# Data model — frozen dataclasses with from_dict / to_dict
# ---------------------------------------------------------------------------

_VALID_STATUSES: frozenset[str] = frozenset({"ok", "skipped"})


@dataclass(frozen=True)
class BenchmarkSpec:
    """One benchmark task and its acceptance criterion."""

    task: str
    acceptance: dict[str, Any]

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "BenchmarkSpec":
        acceptance = raw.get("acceptance", {})
        if not isinstance(acceptance, dict):
            acceptance = {}
        return cls(
            task=str(raw.get("task", "")),
            acceptance=dict(acceptance),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"task": self.task, "acceptance": self.acceptance}


@dataclass(frozen=True)
class Variant:
    """One workflow variant to benchmark (e.g. solo, panel, loop)."""

    label: str
    model: str
    effort: str
    workflow: str
    params: dict[str, Any]

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Variant":
        params = raw.get("params", {})
        if not isinstance(params, dict):
            params = {}
        return cls(
            label=str(raw.get("label", "")),
            model=str(raw.get("model", "")),
            effort=str(raw.get("effort", "")),
            workflow=str(raw.get("workflow", "")),
            params=dict(params),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "model": self.model,
            "effort": self.effort,
            "workflow": self.workflow,
            "params": self.params,
        }


@dataclass(frozen=True)
class Result:
    """Recorded outcome of one variant run.

    ``status`` is either 'ok' or 'skipped'.  A 'skipped' result contains the
    numbers that were recorded (usually zeros); we never fabricate values.

    ``quality`` (0–5) is a JUDGMENT made by a judge, not an objective metric.
    It must never be presented as ground truth — see render_report.

    ``reason`` is set when status='skipped' to explain why (never empty for
    skipped results).  Empty string for status='ok'.

    ``judge_rationale`` captures the free-text reasoning from a judge call
    (empty string when not judged or judged=False).
    """

    variant: Variant
    tokens_in: int
    tokens_out: int
    cost_usd: float
    correct: bool
    error_count: int
    quality: int
    duration_s: float
    output_ref: str
    judged: bool
    status: Literal["ok", "skipped"]
    reason: str = ""
    judge_rationale: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Result":
        """Fail-closed: missing or invalid fields default to safe/zero values."""
        variant_raw = raw.get("variant", {})
        if not isinstance(variant_raw, dict):
            variant_raw = {}
        variant = Variant.from_dict(variant_raw)

        status_raw = str(raw.get("status", "skipped"))
        status: Literal["ok", "skipped"] = "skipped" if status_raw not in _VALID_STATUSES else status_raw  # type: ignore[assignment]

        def _int(key: str) -> int:
            try:
                return int(raw.get(key, 0) or 0)
            except (TypeError, ValueError):
                return 0

        def _float(key: str) -> float:
            try:
                return float(raw.get(key, 0.0) or 0.0)
            except (TypeError, ValueError):
                return 0.0

        def _bool(key: str) -> bool:
            v = raw.get(key, False)
            return bool(v) if v is not None else False

        quality = _int("quality")
        # Clamp quality to 0–5
        quality = max(0, min(5, quality))

        return cls(
            variant=variant,
            tokens_in=_int("tokens_in"),
            tokens_out=_int("tokens_out"),
            cost_usd=_float("cost_usd"),
            correct=_bool("correct"),
            error_count=_int("error_count"),
            quality=quality,
            duration_s=_float("duration_s"),
            output_ref=str(raw.get("output_ref", "")),
            judged=_bool("judged"),
            status=status,
            reason=str(raw.get("reason", "")),
            judge_rationale=str(raw.get("judge_rationale", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "variant": self.variant.to_dict(),
            "tokens_in": self.tokens_in,
            "tokens_out": self.tokens_out,
            "cost_usd": self.cost_usd,
            "correct": self.correct,
            "error_count": self.error_count,
            "quality": self.quality,
            "duration_s": self.duration_s,
            "output_ref": self.output_ref,
            "judged": self.judged,
            "status": self.status,
            "reason": self.reason,
            "judge_rationale": self.judge_rationale,
        }


# ---------------------------------------------------------------------------
# Matrix structure
# ---------------------------------------------------------------------------

# Stable column order for the matrix / CSV output.
_COLUMNS: tuple[str, ...] = (
    "label",
    "workflow",
    "model",
    "effort",
    "tokens_in",
    "tokens_out",
    "cost_usd",
    "correct",
    "error_count",
    "quality",
    "duration_s",
    "judged",
    "status",
)


@dataclass(frozen=True)
class BenchMatrix:
    """A matrix of benchmark results.  Rows preserve insertion order."""

    columns: tuple[str, ...]
    rows: list[dict[str, Any]]


def _result_to_row(result: Result) -> dict[str, Any]:
    """Project a Result into a flat row dict using the stable column order."""
    return {
        "label": result.variant.label,
        "workflow": result.variant.workflow,
        "model": result.variant.model,
        "effort": result.variant.effort,
        "tokens_in": result.tokens_in,
        "tokens_out": result.tokens_out,
        "cost_usd": result.cost_usd,
        "correct": result.correct,
        "error_count": result.error_count,
        "quality": result.quality,
        "duration_s": result.duration_s,
        "judged": result.judged,
        "status": result.status,
    }


def build_matrix(results: list[Result]) -> BenchMatrix:
    """Build a comparison matrix from recorded results.

    'skipped' variants appear as a row with their recorded values (never
    fabricated numbers) — they do NOT crash the matrix.  Fail-closed on
    missing / partial fields (already handled by Result.from_dict).
    """
    rows = [_result_to_row(r) for r in results]
    return BenchMatrix(columns=_COLUMNS, rows=rows)


# ---------------------------------------------------------------------------
# Pareto frontier
# ---------------------------------------------------------------------------


def pareto_frontier(results: list[Result]) -> list[Result]:
    """Return the non-dominated set on (quality UP, total_tokens DOWN).

    A result A dominates B when A.quality >= B.quality AND A.total_tokens <=
    B.total_tokens (with at least one strict inequality).

    'skipped' variants are excluded from the frontier because their numbers are
    not meaningful for comparison.

    NOTE: 'quality' is a JUDGMENT (judged by a judge against a rubric), not an
    objective metric.  The Pareto computation is mechanical, but the quality
    input is subjective.  Callers must not present the frontier as objectively
    optimal without disclosing this.
    """
    candidates = [r for r in results if r.status == "ok"]
    if not candidates:
        return []

    frontier: list[Result] = []
    for candidate in candidates:
        c_tokens = candidate.tokens_in + candidate.tokens_out
        c_quality = candidate.quality

        dominated = False
        for other in candidates:
            if other is candidate:
                continue
            o_tokens = other.tokens_in + other.tokens_out
            o_quality = other.quality
            # 'other' dominates 'candidate' when other is at least as good on
            # both dimensions and strictly better on at least one.
            if o_quality >= c_quality and o_tokens <= c_tokens:
                if o_quality > c_quality or o_tokens < c_tokens:
                    dominated = True
                    break
        if not dominated:
            frontier.append(candidate)

    return frontier


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BenchReport:
    """Rendered benchmark report (markdown + CSV bytes)."""

    markdown: str
    csv_bytes: bytes


def _recommended_config(frontier: list[Result]) -> str:
    """One-line recommendation: best quality per token from the Pareto frontier.

    Returns a presence-only string (no fabricated values) when no frontier
    exists.  Quality is a judgment — we declare this explicitly.
    """
    if not frontier:
        return "No recommendation: no Pareto-frontier candidates available."

    # Score = quality / total_tokens (higher is better).  Avoid division by
    # zero: if tokens == 0 treat score as quality alone (unlikely in real data).
    def _score(r: Result) -> float:
        total = r.tokens_in + r.tokens_out
        if total == 0:
            return float(r.quality)
        return r.quality / total

    best = max(frontier, key=_score)
    total_tok = best.tokens_in + best.tokens_out
    return (
        f"Recommended config: {best.variant.label!r} "
        f"(quality={best.quality}/5 [judged], "
        f"total_tokens={total_tok}, "
        f"workflow={best.variant.workflow!r})"
    )


def render_report(matrix: BenchMatrix) -> BenchReport:
    """Render a deterministic markdown + CSV report from a BenchMatrix.

    Stability guarantee: for the same matrix input the output bytes are
    identical across calls.  Column order and row order follow the matrix.

    Quality disclaimer: the quality column is a JUDGMENT, not an objective
    metric.  This is stated explicitly in the report header.
    """
    # Rebuild Result objects for Pareto (we need the structured data).
    results_for_pareto: list[Result] = []
    for row in matrix.rows:
        # Reconstruct a minimal Result from the flat row.
        variant = Variant(
            label=str(row.get("label", "")),
            model=str(row.get("model", "")),
            effort=str(row.get("effort", "")),
            workflow=str(row.get("workflow", "")),
            params={},
        )
        status_raw = str(row.get("status", "skipped"))
        status: Literal["ok", "skipped"] = "skipped" if status_raw not in _VALID_STATUSES else status_raw  # type: ignore[assignment]
        results_for_pareto.append(
            Result(
                variant=variant,
                tokens_in=int(row.get("tokens_in", 0) or 0),
                tokens_out=int(row.get("tokens_out", 0) or 0),
                cost_usd=float(row.get("cost_usd", 0.0) or 0.0),
                correct=bool(row.get("correct", False)),
                error_count=int(row.get("error_count", 0) or 0),
                quality=int(row.get("quality", 0) or 0),
                duration_s=float(row.get("duration_s", 0.0) or 0.0),
                output_ref=str(row.get("output_ref", "")),
                judged=bool(row.get("judged", False)),
                status=status,
            )
        )

    frontier = pareto_frontier(results_for_pareto)
    recommendation = _recommended_config(frontier)
    frontier_labels = {r.variant.label for r in frontier}

    # --- Markdown ---
    md_lines: list[str] = [
        "# WERK Bench Report",
        "",
        "> **Quality column note:** `quality` (0–5) is a JUDGMENT made by a",
        "> judge against the spec rubric.  It is not an objective metric.",
        "> Never present it as ground truth.",
        "",
        f"Results: {len(matrix.rows)}",
        f"Pareto-frontier candidates: {len(frontier)}",
        "",
        f"**{recommendation}**",
        "",
    ]

    # Table header
    header_cols = list(matrix.columns)
    md_lines.append("| " + " | ".join(header_cols) + " | frontier |")
    md_lines.append("| " + " | ".join(["---"] * (len(header_cols) + 1)) + " |")

    for row in matrix.rows:
        label = str(row.get("label", ""))
        on_frontier = "yes" if label in frontier_labels else "-"
        cells = [str(row.get(col, "")) for col in header_cols]
        cells.append(on_frontier)
        md_lines.append("| " + " | ".join(cells) + " |")

    md_lines.append("")

    # Skipped variants section
    skipped = [row for row in matrix.rows if row.get("status") == "skipped"]
    if skipped:
        md_lines.extend(["## Skipped variants", ""])
        for row in skipped:
            md_lines.append(f"- `{row.get('label', '')}` (skipped — no result recorded)")
        md_lines.append("")

    markdown = "\n".join(md_lines)

    # --- CSV ---
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(matrix.columns), extrasaction="ignore", lineterminator="\n")
    writer.writeheader()
    for row in matrix.rows:
        writer.writerow({col: row.get(col, "") for col in matrix.columns})
    csv_bytes = buf.getvalue().encode("utf-8")

    return BenchReport(markdown=markdown, csv_bytes=csv_bytes)


# ---------------------------------------------------------------------------
# load_results — read recorded Result fixtures from JSON / JSONL
# ---------------------------------------------------------------------------


def load_results(path: str | Path) -> list[Result]:
    """Load recorded Result objects from a JSON array or JSONL file.

    Fail-closed on missing or corrupt input:
    - Missing file → [] with no exception.
    - Corrupt JSON array → [] with warnings.warn.
    - Corrupt JSONL line → line skipped with warnings.warn; valid lines parsed.
    - Partial / invalid Result fields → Result.from_dict degrades safely.
    """
    source = Path(path)
    if not source.exists():
        return []

    text = source.read_text(encoding="utf-8")

    # Try JSON array first.
    stripped = text.strip()
    if stripped.startswith("["):
        try:
            raw_list = json.loads(stripped)
        except json.JSONDecodeError as exc:
            warnings.warn(f"load_results: corrupt JSON in {source}: {exc}", stacklevel=2)
            return []
        if not isinstance(raw_list, list):
            warnings.warn(f"load_results: expected JSON array in {source}, got {type(raw_list)}", stacklevel=2)
            return []
        results: list[Result] = []
        for idx, item in enumerate(raw_list):
            if not isinstance(item, dict):
                warnings.warn(f"load_results: skipping non-object at index {idx} in {source}", stacklevel=2)
                continue
            results.append(Result.from_dict(item))
        return results

    # Fall back to JSONL.
    results = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            warnings.warn(f"load_results: skipping corrupt line {lineno} in {source}: {exc}", stacklevel=2)
            continue
        if not isinstance(item, dict):
            warnings.warn(f"load_results: skipping non-object at line {lineno} in {source}", stacklevel=2)
            continue
        results.append(Result.from_dict(item))
    return results


# ---------------------------------------------------------------------------
# bench-b: RunOutput — the raw output of one executor call
# ---------------------------------------------------------------------------

# Workflow shapes that require an orchestrator that werktools does not provide.
# A variant with one of these shapes and NO caller-supplied executor is
# always 'skipped' with an honest reason.
_ORCHESTRATOR_SHAPES: frozenset[str] = frozenset({"panel", "loop"})

# The sole-call ('solo') shape that the default executor can handle.
_SOLO_SHAPE = "solo"

# Provider env-var lookup (mirrors hub/workers.py but does NOT import it to
# avoid any module-scope side-effects from the optional httpx dependency).
_PROVIDER_ENV_KEY = {
    "openrouter": "OPENROUTER_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}

# Default provider assumed when building a transient worker for bench-b.
_DEFAULT_PROVIDER = "openrouter"


@dataclass(frozen=True)
class RunOutput:
    """Raw output captured from one executor call.

    ``total_tokens`` is the ONLY token field exposed here because the worker
    layer (WorkerCallResult.tokens) reports a combined total.  The in/out split
    is unavailable from that layer and must NOT be invented.  Pareto math uses
    total_tokens; the Result stores the split as (total_tokens, 0) to be honest.
    """

    text: str
    cost_usd: float
    total_tokens: int
    duration_s: float
    extra: dict[str, Any]

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "RunOutput":
        def _float(k: str) -> float:
            try:
                return float(raw.get(k, 0.0) or 0.0)
            except (TypeError, ValueError):
                return 0.0

        def _int(k: str) -> int:
            try:
                return int(raw.get(k, 0) or 0)
            except (TypeError, ValueError):
                return 0

        extra = raw.get("extra", {})
        if not isinstance(extra, dict):
            extra = {}
        return cls(
            text=str(raw.get("text", "")),
            cost_usd=_float("cost_usd"),
            total_tokens=_int("total_tokens"),
            duration_s=_float("duration_s"),
            extra=dict(extra),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "cost_usd": self.cost_usd,
            "total_tokens": self.total_tokens,
            "duration_s": self.duration_s,
            "extra": self.extra,
        }


# ---------------------------------------------------------------------------
# bench-b: default executor (solo-only, via dispatch_worker seam)
# ---------------------------------------------------------------------------

def _build_transient_worker(variant: Variant) -> Any:
    """Build a minimal WorkerManifest from a Variant for single-call dispatch."""
    from ..hub.workers import WorkerManifest

    return WorkerManifest(
        id=f"bench-b-{variant.label}",
        label=f"bench-b {variant.label}",
        provider=_DEFAULT_PROVIDER,
        allowed_models=(variant.model,),
        max_cost_usd="10.00",
        max_tokens=4096,
        record_prompt="redacted",
        record_response="summary",
        tags=("bench",),
        enabled=True,
    )


def _key_present(provider: str, environ: dict[str, str]) -> bool:
    """Return True iff the provider's env key exists in environ.

    Presence-only: we never read, log, or return the value.
    """
    env_key = _PROVIDER_ENV_KEY.get(provider)
    if env_key is None:
        return False
    return env_key in environ and bool(environ[env_key])


def _default_executor(
    spec: "BenchmarkSpec",
    variant: "Variant",
    *,
    environ: dict[str, str],
    _http_call: Callable[..., dict[str, Any]] | None,
) -> "RunOutput":
    """Default executor: one solo model-call via dispatch_worker.

    Returns a RunOutput with real-shaped cost/tokens from WorkerCallResult.
    Raises _SkipReason when the call cannot proceed (no key, wrong shape,
    disabled worker, etc.) so that run_variant can record an honest 'skipped'.
    """
    # Import inside the function so we never trigger module-scope side-effects
    # from the optional httpx/worker layer at import time.
    import tempfile

    from ..hub.workers import dispatch_worker

    worker = _build_transient_worker(variant)
    prompt = spec.task

    # We need a ledger path for dispatch_worker's audit writes.  Use a
    # temporary file so bench-b leaves no debris when run as part of a test
    # or in a directory without a .werktools/ hierarchy.
    with tempfile.TemporaryDirectory() as tmpdir:
        ledger_path = Path(tmpdir) / "bench-b-trace.jsonl"
        t0 = time.monotonic()
        try:
            call_result = dispatch_worker(
                worker,
                variant.model,
                prompt,
                worker.max_tokens,
                ledger_path,
                _http_call=_http_call,
                _environ=environ,
            )
        except KeyError as exc:
            raise _SkipReason(str(exc)) from exc
        duration = time.monotonic() - t0

    if not call_result.ok:
        raise _SkipReason(call_result.reason)

    try:
        cost = float(call_result.cost_usd) if call_result.cost_usd not in (None, "unknown") else 0.0
    except (TypeError, ValueError):
        cost = 0.0

    return RunOutput(
        text=call_result.summary,
        cost_usd=cost,
        total_tokens=call_result.tokens,
        duration_s=duration,
        extra={"cost_usd_raw": call_result.cost_usd},
    )


class _SkipReason(Exception):
    """Internal sentinel: the executor cannot run this variant; skip it."""


# ---------------------------------------------------------------------------
# bench-b: run_variant
# ---------------------------------------------------------------------------

# Executor callable type: (BenchmarkSpec, Variant) -> RunOutput
_ExecutorCallable = Callable[["BenchmarkSpec", "Variant"], "RunOutput"]


def run_variant(
    spec: "BenchmarkSpec",
    variant: "Variant",
    executor: _ExecutorCallable | None = None,
    *,
    environ: dict[str, str] | None = None,
    _http_call: Callable[..., dict[str, Any]] | None = None,
) -> "Result":
    """Run one variant and return a Result.

    Parameters
    ----------
    spec:
        The benchmark task and acceptance rubric.
    variant:
        The workflow variant to run.
    executor:
        Optional pluggable callable ``(spec, variant) -> RunOutput``.  When
        ``None`` the default executor is used (single-call via dispatch_worker).
    environ:
        Environment mapping for key-presence checks.  Defaults to os.environ.
        Presence-only: values are never read, logged, or returned.
    _http_call:
        Injectable HTTP callable for the default executor (tests use this to
        avoid real network calls).

    Returns
    -------
    Result with status='ok' on success or status='skipped' on any failure,
    with an honest reason string.  Numbers are NEVER fabricated.
    """
    env: dict[str, str] = environ if environ is not None else dict(os.environ)

    # Non-solo shapes need a caller-supplied orchestrator; be honest if absent.
    if executor is None and variant.workflow in _ORCHESTRATOR_SHAPES:
        reason = (
            f"workflow shape {variant.workflow!r} needs a caller-supplied executor; "
            "werktools has no orchestrator"
        )
        return _make_skipped_result(variant, reason)

    # Key-presence gate for the default executor (solo).
    if executor is None and variant.workflow == _SOLO_SHAPE:
        if not _key_present(_DEFAULT_PROVIDER, env):
            env_key = _PROVIDER_ENV_KEY.get(_DEFAULT_PROVIDER, "")
            reason = (
                f"provider key absent: {env_key!r} not set in environ "
                "(presence-only check; value not read)"
            )
            return _make_skipped_result(variant, reason)

    t0 = time.monotonic()
    try:
        if executor is not None:
            output = executor(spec, variant)
        else:
            output = _default_executor(spec, variant, environ=env, _http_call=_http_call)
    except _SkipReason as exc:
        return _make_skipped_result(variant, str(exc))
    except Exception as exc:
        # Fail-closed: unexpected errors become skipped results, never crashes.
        reason = f"{type(exc).__name__}: {exc}"
        warnings.warn(f"run_variant: executor raised unexpectedly: {reason}", stacklevel=2)
        return _make_skipped_result(variant, reason)

    duration = time.monotonic() - t0 if output.duration_s == 0.0 else output.duration_s

    # Token split: the worker layer only exposes a total.  Store it honestly
    # as (total, 0) and document that the split is unavailable.
    total_tokens = output.total_tokens
    return Result(
        variant=variant,
        tokens_in=total_tokens,   # NOTE: total reported as tokens_in; split unavailable from worker layer
        tokens_out=0,
        cost_usd=output.cost_usd,
        correct=False,            # correctness requires domain evaluation; never assumed
        error_count=0,
        quality=0,                # quality requires judging; never assumed
        duration_s=duration,
        output_ref="",
        judged=False,
        status="ok",
        reason="",
        judge_rationale="",
    )


def _make_skipped_result(variant: "Variant", reason: str) -> "Result":
    """Return a zero-valued skipped Result with an honest reason."""
    return Result(
        variant=variant,
        tokens_in=0,
        tokens_out=0,
        cost_usd=0.0,
        correct=False,
        error_count=0,
        quality=0,
        duration_s=0.0,
        output_ref="",
        judged=False,
        status="skipped",
        reason=reason,
        judge_rationale="",
    )


# ---------------------------------------------------------------------------
# bench-b: run_matrix with record/replay
# ---------------------------------------------------------------------------

def _cassette_path(cache_dir: Path, variant: "Variant") -> Path:
    """Deterministic cassette path for a variant inside cache_dir."""
    # Use label only (labels must be unique per matrix; caller's responsibility).
    safe_label = "".join(c if c.isalnum() or c in "-_" else "_" for c in variant.label)
    return cache_dir / f"{safe_label}.json"


@dataclass(frozen=True)
class _CachedEntry:
    """Internal: the full cassette payload for record/replay."""
    output: "RunOutput"
    status: str
    reason: str


def _load_cached_entry(path: Path) -> "_CachedEntry | None":
    """Return a cached entry if the cassette exists and is valid."""
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        warnings.warn(f"run_matrix: ignoring corrupt cassette {path}: {exc}", stacklevel=3)
        return None
    if not isinstance(raw, dict):
        return None
    output_raw = raw.get("output", {})
    if not isinstance(output_raw, dict):
        return None
    return _CachedEntry(
        output=RunOutput.from_dict(output_raw),
        status=str(raw.get("status", "ok")),
        reason=str(raw.get("reason", "")),
    )


def _save_cached_entry(path: Path, output: "RunOutput", status: str, reason: str) -> None:
    """Write a cassette entry to disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "replayed": False,
        "status": status,
        "reason": reason,
        "output": output.to_dict(),
    }
    path.write_text(json.dumps(payload, sort_keys=True, indent=2), encoding="utf-8")


def run_matrix(
    spec: "BenchmarkSpec",
    variants: list["Variant"],
    executor: _ExecutorCallable | None = None,
    *,
    cache_dir: "Path | str | None" = None,
    environ: dict[str, str] | None = None,
    _http_call: Callable[..., dict[str, Any]] | None = None,
) -> list["Result"]:
    """Run each variant via run_variant; record/replay via cache_dir.

    If ``cache_dir`` is given:
    - On first run: the RunOutput is written to a per-variant cassette JSON.
    - On subsequent runs: the cassette is loaded and replayed; the executor
      and _http_call are NOT invoked.

    This makes the full matrix re-derivable offline with zero model calls.

    Parameters
    ----------
    spec:      The benchmark task and rubric.
    variants:  Ordered list of variants to run.
    executor:  Optional pluggable executor callable; passed through to run_variant.
    cache_dir: Optional directory for cassette record/replay.
    environ:   Environment mapping for key-presence checks.
    _http_call: Injectable HTTP callable (test seam).

    Returns
    -------
    list[Result] in the same order as ``variants``.
    """
    cache: Path | None = Path(cache_dir) if cache_dir is not None else None
    env: dict[str, str] = environ if environ is not None else dict(os.environ)
    results: list[Result] = []

    for variant in variants:
        if cache is not None:
            cassette = _cassette_path(cache, variant)
            cached = _load_cached_entry(cassette)
            if cached is not None:
                # Replay: rebuild Result from cassette without calling executor or http.
                result = _result_from_cached_entry(variant, cached)
                results.append(result)
                continue

        # Live run (or no cache).
        result = run_variant(spec, variant, executor, environ=env, _http_call=_http_call)
        results.append(result)

        # Record if cache enabled.
        if cache is not None:
            cassette = _cassette_path(cache, variant)
            output = _output_from_result(result)
            _save_cached_entry(cassette, output, result.status, result.reason)

    return results


def _result_from_cached_entry(variant: "Variant", cached: "_CachedEntry") -> "Result":
    """Reconstruct a Result from a cassette entry (replay path).

    The cassette stores status and reason so that replayed skipped results are
    identical to the original live run — numbers are never fabricated.
    """
    status_raw = cached.status
    status: Literal["ok", "skipped"] = "skipped" if status_raw not in _VALID_STATUSES else status_raw  # type: ignore[assignment]
    if status == "skipped":
        return _make_skipped_result(variant, cached.reason)
    return Result(
        variant=variant,
        tokens_in=cached.output.total_tokens,
        tokens_out=0,
        cost_usd=cached.output.cost_usd,
        correct=False,
        error_count=0,
        quality=0,
        duration_s=cached.output.duration_s,
        output_ref="",
        judged=False,
        status="ok",
        reason="",
        judge_rationale="",
    )


def _output_from_result(result: "Result") -> "RunOutput":
    """Extract a RunOutput from a Result for caching.

    For skipped results, saves a zero-valued output so replay produces the
    same skipped Result (via the cache path being absent or the status recorded
    in extra).
    """
    return RunOutput(
        text="",
        cost_usd=result.cost_usd,
        total_tokens=result.tokens_in + result.tokens_out,
        duration_s=result.duration_s,
        extra={"status": result.status, "reason": result.reason},
    )


# ---------------------------------------------------------------------------
# bench-c: judge_quality and judge_quality_multi
# ---------------------------------------------------------------------------

# Judge callable type: (BenchmarkSpec, Result) -> dict with keys 'score' and 'rationale'
_JudgeCallable = Callable[["BenchmarkSpec", "Result"], dict[str, Any]]

_JUDGE_PROMPT_TEMPLATE = """\
You are a benchmark quality judge.

Task: {task}

Acceptance rubric:
{rubric}

Variant output:
{output_text}

Score the output on a 0-5 scale where:
  0 = completely wrong or missing
  1 = mostly wrong
  2 = partially correct
  3 = correct but weak
  4 = correct and solid
  5 = correct, comprehensive, and exemplary

Respond with JSON only, in this exact format:
{{"score": <integer 0-5>, "rationale": "<one sentence>"}}
"""


def _call_judge_model(
    spec: "BenchmarkSpec",
    result: "Result",
    *,
    environ: dict[str, str],
    _http_call: Callable[..., dict[str, Any]] | None,
) -> dict[str, Any]:
    """Call the default judge via dispatch_worker.

    Returns a dict with 'score' and 'rationale'.
    Raises _SkipReason if the key is absent or the call fails.
    """
    import tempfile

    from ..hub.workers import WorkerManifest, dispatch_worker

    if not _key_present(_DEFAULT_PROVIDER, environ):
        env_key = _PROVIDER_ENV_KEY.get(_DEFAULT_PROVIDER, "")
        raise _SkipReason(f"judge: provider key absent: {env_key!r} not set")

    # Use a small judge-model worker — haiku/fast by default.
    judge_model = "openai/gpt-4o-mini"
    worker = WorkerManifest(
        id="bench-c-judge",
        label="bench-c judge",
        provider=_DEFAULT_PROVIDER,
        allowed_models=(judge_model,),
        max_cost_usd="5.00",
        max_tokens=512,
        record_prompt="redacted",
        record_response="summary",
        tags=("bench-judge",),
        enabled=True,
    )
    rubric = json.dumps(spec.acceptance, sort_keys=True) if spec.acceptance else "(no rubric provided)"
    prompt = _JUDGE_PROMPT_TEMPLATE.format(
        task=spec.task,
        rubric=rubric,
        output_text=result.judge_rationale or result.output_ref or "(no output text available)",
    )
    with tempfile.TemporaryDirectory() as tmpdir:
        ledger_path = Path(tmpdir) / "bench-c-trace.jsonl"
        try:
            call_result = dispatch_worker(
                worker,
                judge_model,
                prompt,
                worker.max_tokens,
                ledger_path,
                _http_call=_http_call,
                _environ=environ,
            )
        except KeyError as exc:
            raise _SkipReason(str(exc)) from exc

    if not call_result.ok:
        raise _SkipReason(f"judge call failed: {call_result.reason}")

    # Parse the JSON response from the judge.
    response_text = call_result.summary
    try:
        parsed = json.loads(response_text)
        if isinstance(parsed, dict) and "score" in parsed:
            score = max(0, min(5, int(parsed["score"])))
            rationale = str(parsed.get("rationale", ""))
            return {"score": score, "rationale": rationale}
    except (json.JSONDecodeError, ValueError, TypeError):
        pass
    # Fallback: try to extract a digit from the response.
    import re as _re

    match = _re.search(r'"score"\s*:\s*(\d)', response_text)
    if match:
        score = max(0, min(5, int(match.group(1))))
        return {"score": score, "rationale": response_text[:200]}
    # Cannot parse -> fail closed
    raise _SkipReason(f"judge: could not parse score from response: {response_text[:100]!r}")


def _vote_score(vote: Any) -> "int | None":
    """Extract a clamped 0-5 score from a judge vote, or None if absent/invalid.

    Honest-degrade: a vote that carries no usable ``score`` returns None so the
    caller can leave the result UNJUDGED rather than fabricate a misleading 0
    (which would be indistinguishable from a genuine "scored 0 / terrible").
    """
    if not isinstance(vote, dict) or "score" not in vote:
        return None
    try:
        return max(0, min(5, int(vote["score"])))
    except (TypeError, ValueError):
        return None


def judge_quality(
    spec: "BenchmarkSpec",
    result: "Result",
    judge: _JudgeCallable | None = None,
    *,
    environ: dict[str, str] | None = None,
    _http_call: Callable[..., dict[str, Any]] | None = None,
) -> "Result":
    """Score the quality of a result against spec.acceptance.

    quality (0–5) is a DECLARED JUDGMENT, never an objective metric.

    Parameters
    ----------
    spec:    The benchmark task and rubric to judge against.
    result:  The Result to evaluate.
    judge:   Optional pluggable callable ``(spec, result) -> {"score": int, "rationale": str}``.
             When None, calls the default judge model via dispatch_worker.
    environ: Environment mapping for key-presence checks.
    _http_call: Injectable HTTP callable (test seam).

    Returns
    -------
    A NEW Result (immutable) with updated ``quality``, ``judged``, and
    ``judge_rationale``.  If the judge cannot run (no key, call failed), returns
    the original Result with ``judged=False`` and ``quality`` unchanged.
    """
    env: dict[str, str] = environ if environ is not None else dict(os.environ)

    # Key-presence gate for the default judge.
    if judge is None and not _key_present(_DEFAULT_PROVIDER, env):
        # Cannot judge without a key: return unchanged, judged=False.
        return result

    try:
        if judge is not None:
            vote = judge(spec, result)
        else:
            vote = _call_judge_model(spec, result, environ=env, _http_call=_http_call)
    except _SkipReason:
        return result
    except Exception as exc:
        warnings.warn(f"judge_quality: judge raised unexpectedly: {type(exc).__name__}: {exc}", stacklevel=2)
        return result

    score = _vote_score(vote)
    if score is None:
        warnings.warn(
            "judge_quality: judge returned no usable 'score'; leaving result unjudged (honest-degrade)",
            stacklevel=2,
        )
        return result
    rationale = str(vote.get("rationale", "")) if isinstance(vote, dict) else ""

    # Return a new immutable Result with updated judge fields.
    return Result(
        variant=result.variant,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        cost_usd=result.cost_usd,
        correct=result.correct,
        error_count=result.error_count,
        quality=score,
        duration_s=result.duration_s,
        output_ref=result.output_ref,
        judged=True,
        status=result.status,
        reason=result.reason,
        judge_rationale=rationale,
    )


def judge_quality_multi(
    spec: "BenchmarkSpec",
    result: "Result",
    judges: int = 3,
    judge: _JudgeCallable | None = None,
    *,
    environ: dict[str, str] | None = None,
    _http_call: Callable[..., dict[str, Any]] | None = None,
) -> "Result":
    """Aggregate N judge votes to reduce single-judge noise.

    Scores: median of all votes.
    Rationales: all per-vote rationales joined and stored in judge_rationale.
    Disagreement: always surfaced (variance in judge_rationale metadata).

    Parameters
    ----------
    spec:    The benchmark task and rubric.
    result:  The Result to evaluate.
    judges:  Number of independent judge calls.
    judge:   Optional pluggable judge callable.
    environ: Environment mapping.
    _http_call: Injectable HTTP seam.

    Returns
    -------
    A NEW Result with aggregated quality and multi-vote rationale.
    If the key is absent or all calls fail, returns result with judged=False.
    """
    env: dict[str, str] = environ if environ is not None else dict(os.environ)

    # Key-presence gate for the default judge.
    if judge is None and not _key_present(_DEFAULT_PROVIDER, env):
        return result

    scores: list[int] = []
    rationales: list[str] = []

    for _ in range(judges):
        try:
            if judge is not None:
                vote = judge(spec, result)
            else:
                vote = _call_judge_model(spec, result, environ=env, _http_call=_http_call)
            score = _vote_score(vote)
            if score is None:
                warnings.warn(
                    "judge_quality_multi: vote returned no usable 'score'; skipping it (honest-degrade)",
                    stacklevel=2,
                )
                continue
            rationale = str(vote.get("rationale", ""))
            scores.append(score)
            rationales.append(rationale)
        except _SkipReason:
            continue
        except Exception as exc:
            warnings.warn(f"judge_quality_multi: vote raised: {type(exc).__name__}: {exc}", stacklevel=2)
            continue

    if not scores:
        return result

    median_score = max(0, min(5, round(statistics.median(scores))))
    variance = statistics.variance(scores) if len(scores) > 1 else 0.0
    combined_rationale = (
        f"votes={scores} variance={variance:.2f} | "
        + " | ".join(f"[{i + 1}] {r}" for i, r in enumerate(rationales))
    )

    return Result(
        variant=result.variant,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        cost_usd=result.cost_usd,
        correct=result.correct,
        error_count=result.error_count,
        quality=median_score,
        duration_s=result.duration_s,
        output_ref=result.output_ref,
        judged=True,
        status=result.status,
        reason=result.reason,
        judge_rationale=combined_rationale,
    )
