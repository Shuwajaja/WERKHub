"""Local cost rollup and budget helpers for WERK Cost."""

from __future__ import annotations

import json
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CostEvent:
    """One local cost-relevant event."""

    mission: str
    task: str
    tool: str
    model: str
    amount: Decimal | None
    currency: str
    source_event: str


@dataclass(frozen=True)
class CostRollup:
    """Known and unknown local spend rollup."""

    total: str
    unknown_count: int
    event_count: int
    by_mission: dict[str, str]
    by_task: dict[str, str]
    by_tool: dict[str, str]
    by_model: dict[str, str]


@dataclass(frozen=True)
class BudgetDecision:
    """Local budget check result."""

    decision: str
    total: str
    budget: str
    unknown_count: int
    reason: str


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _money(value: Decimal) -> str:
    return str(value.quantize(Decimal("0.01")))


def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    # NaN/Infinity would poison the rollup total; treat them as unknown cost.
    return parsed if parsed.is_finite() else None


def _payload(raw: dict[str, Any]) -> dict[str, Any]:
    payload = raw.get("payload", raw)
    return payload if isinstance(payload, dict) else {}


def _amount(payload: dict[str, Any]) -> Decimal | None:
    for key in ("amount", "cost", "cost_usd", "usd"):
        if key in payload:
            return _decimal(payload.get(key))
    return None


def _is_cost_relevant(raw: dict[str, Any], payload: dict[str, Any]) -> bool:
    event_type = str(raw.get("event_type", raw.get("type", "")))
    if event_type.startswith(("cost.", "model.", "tool.")):
        return True
    # mission/task/tool/model are context fields on lifecycle events, not cost
    # indicators; only an explicit amount-style key marks other events as spend.
    return any(key in payload for key in ("amount", "cost", "cost_usd", "usd"))


def record_cost(
    path: str | Path,
    mission: str = "",
    task: str = "",
    tool: str = "",
    model: str = "",
    amount: str | Decimal | None = None,
    currency: str = "USD",
) -> Path:
    """Append one local cost event."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "mission": mission,
        "task": task,
        "tool": tool,
        "model": model,
        "amount": str(amount) if amount is not None else None,
        "currency": currency,
    }
    event = {"event_type": "cost.recorded", "created_at": _now_iso(), "payload": payload}
    with target.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")
    return target


def load_cost_events(path: str | Path) -> list[CostEvent]:
    """Load cost-relevant events from JSONL."""
    source = Path(path)
    if not source.exists():
        return []
    events: list[CostEvent] = []
    for line in source.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            warnings.warn(f"load_cost_events: skipping corrupt line in {source}: {exc}", stacklevel=2)
            continue
        if not isinstance(raw, dict):
            continue
        payload = _payload(raw)
        if not _is_cost_relevant(raw, payload):
            continue
        events.append(
            CostEvent(
                mission=str(payload.get("mission", "")),
                task=str(payload.get("task", "")),
                tool=str(payload.get("tool", "")),
                model=str(payload.get("model", "")),
                amount=_amount(payload),
                currency=str(payload.get("currency", "USD")),
                source_event=str(raw.get("event_type", raw.get("type", ""))),
            )
        )
    return events


def _add_bucket(bucket: dict[str, Decimal], key: str, amount: Decimal) -> None:
    bucket_key = key or "unknown"
    bucket[bucket_key] = bucket.get(bucket_key, Decimal("0")) + amount


def _format_bucket(bucket: dict[str, Decimal]) -> dict[str, str]:
    return {key: _money(value) for key, value in sorted(bucket.items())}


def rollup_costs(path: str | Path) -> CostRollup:
    """Roll up known spend and count unknown-cost events."""
    total = Decimal("0")
    unknown_count = 0
    by_mission: dict[str, Decimal] = {}
    by_task: dict[str, Decimal] = {}
    by_tool: dict[str, Decimal] = {}
    by_model: dict[str, Decimal] = {}
    events = load_cost_events(path)
    for event in events:
        if event.amount is None:
            unknown_count += 1
            continue
        total += event.amount
        _add_bucket(by_mission, event.mission, event.amount)
        _add_bucket(by_task, event.task, event.amount)
        _add_bucket(by_tool, event.tool, event.amount)
        _add_bucket(by_model, event.model, event.amount)
    return CostRollup(
        total=_money(total),
        unknown_count=unknown_count,
        event_count=len(events),
        by_mission=_format_bucket(by_mission),
        by_task=_format_bucket(by_task),
        by_tool=_format_bucket(by_tool),
        by_model=_format_bucket(by_model),
    )


def budget_check(rollup: CostRollup, budget: str | Decimal) -> BudgetDecision:
    """Check local known spend against a budget, failing closed on unknowns."""
    parsed_budget = _decimal(budget)
    if parsed_budget is None:
        return BudgetDecision(
            "error",
            rollup.total,
            str(budget),
            rollup.unknown_count,
            f"budget {budget!r} is not a finite number",
        )
    budget_amount = parsed_budget
    total_amount = Decimal(rollup.total)
    if rollup.unknown_count:
        return BudgetDecision(
            "unknown",
            rollup.total,
            _money(budget_amount),
            rollup.unknown_count,
            "unknown cost events require review before approving more spend",
        )
    if total_amount > budget_amount:
        return BudgetDecision(
            "deny",
            rollup.total,
            _money(budget_amount),
            rollup.unknown_count,
            f"known spend {rollup.total} exceeds budget {_money(budget_amount)}",
        )
    return BudgetDecision(
        "allow",
        rollup.total,
        _money(budget_amount),
        rollup.unknown_count,
        f"known spend {rollup.total} is within budget {_money(budget_amount)}",
    )


def write_report(path: str | Path, out: str | Path, budget: str | Decimal | None = None) -> Path:
    """Write a readable local Markdown cost report."""
    rollup = rollup_costs(path)
    target = Path(out)
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# WERK Cost Report",
        "",
        "This is a local estimate from provided trace/cost events, not a billing statement.",
        "",
        f"Events: {rollup.event_count}",
        f"Known total: {rollup.total}",
        f"Unknown cost events: {rollup.unknown_count}",
        "",
    ]
    if budget is not None:
        decision = budget_check(rollup, budget)
        lines.extend(
            [
                "## Budget",
                "",
                f"- decision: `{decision.decision}`",
                f"- budget: `{decision.budget}`",
                f"- reason: {decision.reason}",
                "",
            ]
        )
    for title, bucket in (
        ("By mission", rollup.by_mission),
        ("By task", rollup.by_task),
        ("By tool", rollup.by_tool),
        ("By model", rollup.by_model),
    ):
        lines.extend([f"## {title}", ""])
        if not bucket:
            lines.extend(["- none", ""])
            continue
        for key, value in bucket.items():
            lines.append(f"- `{key}`: {value}")
        lines.append("")
    target.write_text("\n".join(lines), encoding="utf-8")
    return target
