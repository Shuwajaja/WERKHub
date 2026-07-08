"""Offline cassette comparison helpers for WERK Eval."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class EvalCase:
    """One deterministic eval case."""

    name: str
    expected: Any
    actual: Any


@dataclass(frozen=True)
class EvalResult:
    """Result of comparing one cassette."""

    cassette_id: str
    path: str
    total: int
    passed: int
    failed: int
    diffs: tuple[str, ...]


def list_cassettes(root: str | Path) -> list[Path]:
    """List local JSON cassette files."""
    path = Path(root)
    if path.is_file() and path.suffix.lower() == ".json":
        return [path]
    if not path.exists():
        return []
    return sorted(item for item in path.glob("*.json") if item.is_file())


def load_cases(path: str | Path) -> tuple[str, tuple[EvalCase, ...]]:
    """Load eval cases from a JSON cassette."""
    cassette = Path(path)
    raw = json.loads(cassette.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Eval cassette must be a JSON object")
    cassette_id = str(raw.get("id", cassette.stem))
    raw_cases = raw.get("cases", ())
    if not isinstance(raw_cases, list):
        raise ValueError("Eval cassette cases must be a list")
    cases: list[EvalCase] = []
    for index, item in enumerate(raw_cases, start=1):
        if not isinstance(item, dict):
            continue
        cases.append(
            EvalCase(
                name=str(item.get("name", f"case-{index}")),
                expected=item.get("expected"),
                actual=item.get("actual", item.get("input")),
            )
        )
    return cassette_id, tuple(cases)


def _compact(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def run_cassette(path: str | Path) -> EvalResult:
    """Compare one cassette without executing external code."""
    cassette_id, cases = load_cases(path)
    diffs: list[str] = []
    passed = 0
    for case in cases:
        if case.actual == case.expected:
            passed += 1
            continue
        diffs.append(f"{case.name}: expected {_compact(case.expected)} got {_compact(case.actual)}")
    total = len(cases)
    return EvalResult(
        cassette_id=cassette_id,
        path=str(path),
        total=total,
        passed=passed,
        failed=total - passed,
        diffs=tuple(diffs),
    )


def write_report(root: str | Path, out: str | Path) -> Path:
    """Write a CI-friendly Markdown eval report for a directory or file."""
    results = [run_cassette(path) for path in list_cassettes(root)]
    total = sum(result.total for result in results)
    passed = sum(result.passed for result in results)
    failed = sum(result.failed for result in results)
    target = Path(out)
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# WERK Eval Report",
        "",
        f"Cassettes: {len(results)}",
        f"Cases: {total}",
        f"Passed: {passed}",
        f"Failed: {failed}",
        "",
    ]
    for result in results:
        lines.extend(
            [
                f"## {result.cassette_id}",
                "",
                f"- path: `{result.path}`",
                f"- passed: `{result.passed}`",
                f"- failed: `{result.failed}`",
                "",
            ]
        )
        for diff in result.diffs:
            lines.append(f"- {diff}")
        if result.diffs:
            lines.append("")
    target.write_text("\n".join(lines), encoding="utf-8")
    return target
