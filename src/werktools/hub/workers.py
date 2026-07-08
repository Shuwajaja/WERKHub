"""Governed model-worker router: cheap-model dispatch behind the gate.

Operator-configured workers (reviewer/planner/red-team) call an allowed
model through the same fail-closed policy as every tool. Provider keys live
ONLY in the hub-process environment — never an argument, field, ledger,
trace, cost event, or envelope. Budgets are checked BEFORE every dispatch.
The core is stdlib-only; httpx is imported (behind the [worker] extra) only
inside the actual dispatch, and tests drive a record-replay seam offline.
"""

from __future__ import annotations

import warnings as _warnings_mod
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from ..tools.cost import CostRollup, budget_check, load_cost_events, record_cost
from ..tools.trace import append_event

_PROVIDER_ENV = {
    "openrouter": "OPENROUTER_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
}


@dataclass(frozen=True)
class WorkerManifest:
    """One operator-configured model worker."""

    id: str
    label: str = ""
    provider: str = "openrouter"
    allowed_models: tuple[str, ...] = ()
    max_cost_usd: str = "1.00"
    max_tokens: int = 2048
    record_prompt: str = "redacted"
    record_response: str = "summary"
    tags: tuple[str, ...] = ("worker",)
    enabled: bool = True

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "WorkerManifest":
        return cls(
            id=str(raw["id"]),
            label=str(raw.get("label", raw["id"])),
            provider=str(raw.get("provider", "openrouter")),
            allowed_models=tuple(str(m) for m in raw.get("allowed_models", ())),
            max_cost_usd=str(raw.get("max_cost_usd", "1.00")),
            max_tokens=int(raw.get("max_tokens", 2048)),
            record_prompt=str(raw.get("record_prompt", "redacted")),
            record_response=str(raw.get("record_response", "summary")),
            tags=tuple(str(t) for t in raw.get("tags", ("worker",))) or ("worker",),
            enabled=bool(raw.get("enabled", True)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "provider": self.provider,
            "allowed_models": list(self.allowed_models),
            "max_cost_usd": self.max_cost_usd,
            "max_tokens": self.max_tokens,
            "record_prompt": self.record_prompt,
            "record_response": self.record_response,
            "tags": list(self.tags),
            "enabled": self.enabled,
        }


@dataclass(frozen=True)
class WorkerBudgetStatus:
    worker_id: str
    decision: str
    total: str
    budget: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "decision": self.decision,
            "total": self.total,
            "budget": self.budget,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class WorkerCallResult:
    ok: bool
    worker_id: str
    model: str
    summary: str = ""
    cost_usd: str = "0.00"
    tokens: int = 0
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "worker_id": self.worker_id,
            "model": self.model,
            "summary": self.summary,
            "cost_usd": self.cost_usd,
            "tokens": self.tokens,
            "reason": self.reason,
        }


def load_workers(src: str | Path | dict | list) -> list[WorkerManifest]:
    """Load worker manifests from a dict, a list, or a JSON file."""
    import json

    if isinstance(src, list):
        raw = src
    elif isinstance(src, dict):
        raw = src.get("workers", [])
    else:
        path = Path(src)
        if not path.exists():
            return []
        try:
            body = json.loads(path.read_text(encoding="utf-8") or "{}")
        except (json.JSONDecodeError, OSError) as exc:
            _warnings_mod.warn(
                f"load_workers: corrupt {path}: {exc}; returning empty list",
                stacklevel=2,
            )
            return []
        raw = body.get("workers", []) if isinstance(body, dict) else body
    return [WorkerManifest.from_dict(item) for item in raw if isinstance(item, dict)]


def get_worker(workers: list[WorkerManifest], worker_id: str) -> WorkerManifest | None:
    return next((w for w in workers if w.id == worker_id), None)


def _cost_path(ledger_path: str | Path) -> Path:
    return Path(ledger_path).parent / "hub-cost.jsonl"


def _trace_path(ledger_path: str | Path) -> Path:
    return Path(ledger_path).parent / "hub-trace.jsonl"


def check_budget(worker: WorkerManifest, ledger_path: str | Path) -> WorkerBudgetStatus:
    """Check the worker's own spend (cost events filtered to its id) vs cap."""
    events = [e for e in load_cost_events(_cost_path(ledger_path)) if e.mission == worker.id]
    total = Decimal("0")
    unknown = 0
    by_mission: dict[str, Decimal] = {}
    for event in events:
        if event.amount is None:
            unknown += 1
            continue
        total += event.amount
        by_mission[event.mission] = by_mission.get(event.mission, Decimal("0")) + event.amount
    rollup = CostRollup(
        total=str(total.quantize(Decimal("0.01"))),
        unknown_count=unknown,
        event_count=len(events),
        by_mission={k: str(v.quantize(Decimal("0.01"))) for k, v in by_mission.items()},
        by_task={},
        by_tool={},
        by_model={},
    )
    decision = budget_check(rollup, worker.max_cost_usd)
    return WorkerBudgetStatus(worker.id, decision.decision, decision.total, decision.budget, decision.reason)


def _env_key_for(provider: str) -> str:
    env = _PROVIDER_ENV.get(provider)
    if env is None:
        raise ValueError(f"unknown worker provider: {provider!r}")
    return env


_VALID_PROMPT_MODES = frozenset({"none", "redacted"})
_VALID_RESPONSE_MODES = frozenset({"none", "summary"})


def _redact_prompt(prompt: str, mode: str) -> str:
    if mode == "none":
        return ""
    if mode not in _VALID_PROMPT_MODES:
        _warnings_mod.warn(
            f"_redact_prompt: unrecognised mode {mode!r}; defaulting to 'redacted' (secure fallback)",
            stacklevel=2,
        )
    from ..redaction import mask_secret_text

    return mask_secret_text(prompt)[:2000]


def _summarise_response(text: str, mode: str) -> str:
    if mode == "none":
        return ""
    if mode not in _VALID_RESPONSE_MODES:
        _warnings_mod.warn(
            f"_summarise_response: unrecognised mode {mode!r}; defaulting to 'summary' (secure fallback)",
            stacklevel=2,
        )
    flat = " ".join(text.split())
    return flat[:500]


def _real_http_call(provider: str, model: str, prompt: str, max_tokens: int, api_key: str) -> dict[str, Any]:
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - exercised via the seam
        raise ImportError("model-worker dispatch requires httpx: pip install werktools[worker]") from exc
    url = "https://openrouter.ai/api/v1/chat/completions"
    resp = httpx.post(
        url,
        headers={"Authorization": f"Bearer {api_key}"},
        json={"model": model, "messages": [{"role": "user", "content": prompt}], "max_tokens": max_tokens},
        timeout=60,
    )
    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError as exc:
        # status code only, chain suppressed: a provider 401 body can echo the
        # (partial) API key back, and the httpx exc carries the request headers.
        raise RuntimeError(f"provider returned HTTP {exc.response.status_code}") from None
    body = resp.json()
    choices = body.get("choices") or []
    if not choices:
        raise RuntimeError("provider returned empty choices")
    msg = choices[0].get("message") or {}
    text = msg.get("content")
    if text is None:
        raise RuntimeError("provider response missing choices[0].message.content")
    usage = body.get("usage", {})
    raw_cost = usage.get("total_cost")
    cost_usd = str(raw_cost) if raw_cost is not None else None
    return {"text": text, "cost_usd": cost_usd, "tokens": int(usage.get("total_tokens", 0))}


def dispatch_worker(
    worker: WorkerManifest,
    model: str,
    prompt: str,
    max_tokens: int,
    ledger_path: str | Path,
    *,
    _http_call: Callable[..., dict[str, Any]] | None = None,
    _environ: dict[str, str] | None = None,
) -> WorkerCallResult:
    """Dispatch one worker call. Caller MUST check_budget first (fail-closed).

    Denies (no network) when disabled, no allowlist, or model not allowed.
    The provider key is read from the environment here and nowhere else.
    """
    import os

    env = _environ if _environ is not None else dict(os.environ)
    if not worker.enabled:
        return WorkerCallResult(False, worker.id, model, reason="worker disabled")
    if not worker.allowed_models or model not in worker.allowed_models:
        return WorkerCallResult(False, worker.id, model, reason=f"model {model!r} not in allowlist")

    http = _http_call or _real_http_call
    env_var = _env_key_for(worker.provider)
    api_key = env.get(env_var)
    if not api_key:
        raise KeyError(f"{env_var} is not set in the hub environment")

    trace = _trace_path(ledger_path)
    try:
        append_event(
            trace,
            "model_worker.call.requested",
            actor=worker.id,
            payload={"model": model, "prompt": _redact_prompt(prompt, worker.record_prompt)},
        )
    except OSError as exc:
        _warnings_mod.warn(
            f"model_worker pre-call trace write failed (call will still proceed): {type(exc).__name__}: {exc}",
            stacklevel=2,
        )
    try:
        out = http(worker.provider, model, prompt, max_tokens, api_key)
    except Exception as exc:
        from ..redaction import mask_secret_text as _mask
        reason = _mask(f"{type(exc).__name__}: {exc}")
        try:
            append_event(trace, "model_worker.call.failed", actor=worker.id, payload={"model": model, "reason": reason})
        except OSError as trace_exc:
            _warnings_mod.warn(
                f"model_worker failure trace write failed: {type(trace_exc).__name__}: {trace_exc}",
                stacklevel=2,
            )
        return WorkerCallResult(False, worker.id, model, reason=reason)

    summary = _summarise_response(str(out.get("text", "")), worker.record_response)
    cost_usd = out.get("cost_usd")  # None means absent (unknown spend), str means reported value
    tokens = int(out.get("tokens", 0))
    try:
        record_cost(_cost_path(ledger_path), mission=worker.id, task="call", tool="model_worker_call", model=model, amount=cost_usd)
        append_event(
            trace,
            "model_worker.call.completed",
            actor=worker.id,
            payload={"model": model, "summary": summary, "cost_usd": cost_usd, "tokens": tokens},
        )
    except OSError as exc:
        _warnings_mod.warn(
            f"model_worker audit write failed (call still succeeded): {type(exc).__name__}: {exc}",
            stacklevel=2,
        )
    return WorkerCallResult(True, worker.id, model, summary=summary, cost_usd=cost_usd if cost_usd is not None else "unknown", tokens=tokens)


def report_workers(workers: list[WorkerManifest], ledger_path: str | Path) -> list[dict[str, Any]]:
    """Summarize each worker's manifest + current budget status."""
    rows: list[dict[str, Any]] = []
    for worker in workers:
        status = check_budget(worker, ledger_path)
        rows.append({"worker": worker.to_dict(), "budget": status.to_dict()})
    return rows
