"""Local planning and handoff helpers for WERK Swarm."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_AGENT_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]+\Z")


@dataclass(frozen=True)
class WorkPacket:
    """One bounded handoff packet for an external agent host."""

    packet_id: str
    agent_id: str
    title: str
    repo: str
    allowed_scope: str
    instructions: str
    done_criteria: str
    stop_condition: str

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "WorkPacket":
        try:
            agent_id = str(raw["agent_id"])
            if not _AGENT_ID_PATTERN.match(agent_id):
                raise ValueError(f"invalid agent_id (letters, digits, _ and - only): {agent_id!r}")
            return cls(
                packet_id=str(raw["packet_id"]),
                agent_id=agent_id,
                title=str(raw["title"]),
                repo=str(raw["repo"]),
                allowed_scope=str(raw["allowed_scope"]),
                instructions=str(raw["instructions"]),
                done_criteria=str(raw["done_criteria"]),
                stop_condition=str(raw["stop_condition"]),
            )
        except KeyError as exc:
            raise ValueError(f"work packet is missing required field {exc.args[0]!r}") from exc

    def to_dict(self) -> dict[str, str]:
        return {
            "packet_id": self.packet_id,
            "agent_id": self.agent_id,
            "title": self.title,
            "repo": self.repo,
            "allowed_scope": self.allowed_scope,
            "instructions": self.instructions,
            "done_criteria": self.done_criteria,
            "stop_condition": self.stop_condition,
        }


@dataclass(frozen=True)
class SwarmPlan:
    """Static local swarm plan."""

    goal_file: str
    repo: str
    packets: tuple[WorkPacket, ...]
    created_at: str

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "SwarmPlan":
        return cls(
            goal_file=str(raw["goal_file"]),
            repo=str(raw["repo"]),
            packets=tuple(WorkPacket.from_dict(item) for item in raw.get("packets", ()) if isinstance(item, dict)),
            created_at=str(raw.get("created_at", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal_file": self.goal_file,
            "repo": self.repo,
            "packets": [packet.to_dict() for packet in self.packets],
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class ReportSummary:
    """Summary extracted from local agent report files."""

    report_count: int
    statuses: tuple[str, ...]
    evidence: tuple[str, ...]
    files: tuple[str, ...]


@dataclass(frozen=True)
class ReviewResult:
    """Checklist result for collected reports."""

    ok: bool
    findings: tuple[str, ...]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sections(goal_file: Path) -> list[tuple[str, str]]:
    text = goal_file.read_text(encoding="utf-8")
    sections: list[tuple[str, list[str]]] = []
    current_title = ""
    current_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            if current_title:
                sections.append((current_title, current_lines))
            current_title = stripped.lstrip("#").strip()
            current_lines = []
            continue
        if current_title:
            current_lines.append(line)
    if current_title:
        sections.append((current_title, current_lines))
    if not sections:
        title = goal_file.stem.replace("-", " ").replace("_", " ").title() or "Goal"
        sections.append((title, text.splitlines()))
    return [(title, "\n".join(lines).strip()) for title, lines in sections]


def _bounded_sections(goal_file: Path, agents: int) -> list[tuple[str, str]]:
    values = _sections(goal_file)
    limit = max(1, agents)
    if len(values) <= limit:
        return values
    head = values[: limit - 1]
    tail = values[limit - 1 :]
    tail_title = " / ".join(title for title, _ in tail)
    tail_body = "\n\n".join(f"## {title}\n{body}" for title, body in tail)
    return head + [(tail_title, tail_body)]


def plan_from_goal(goal_file: str | Path, repo: str = ".", agents: int = 3) -> SwarmPlan:
    """Create bounded local work packets from a goal Markdown file."""
    path = Path(goal_file)
    packets: list[WorkPacket] = []
    for index, (title, body) in enumerate(_bounded_sections(path, agents), start=1):
        packets.append(
            WorkPacket(
                packet_id=f"packet-{index}",
                agent_id=f"agent-{index}",
                title=title,
                repo=repo,
                allowed_scope=f"Work only inside {repo} and only on the packet topic: {title}.",
                instructions=body or title,
                done_criteria="Return a report with Status: done and Evidence: lines for changed files and checks.",
                stop_condition=(
                    "Stop if work would require another repository, a live key, network-only verification, "
                    "or an autonomous runtime/scheduler."
                ),
            )
        )
    return SwarmPlan(goal_file=str(path), repo=repo, packets=tuple(packets), created_at=_now_iso())


def render_packet(plan: SwarmPlan, agent_id: str) -> str:
    """Render one packet as Markdown."""
    packet = next((item for item in plan.packets if item.agent_id == agent_id), None)
    if packet is None:
        raise KeyError(f"unknown agent id: {agent_id}")
    lines = [
        f"# Work Packet: {packet.agent_id}",
        "",
        f"Packet: `{packet.packet_id}`",
        f"Title: {packet.title}",
        f"Repo: `{packet.repo}`",
        "",
        "## Allowed Scope",
        "",
        packet.allowed_scope,
        "",
        "## Instructions",
        "",
        packet.instructions,
        "",
        "## Done Criteria",
        "",
        packet.done_criteria,
        "",
        "## Stop Condition",
        "",
        packet.stop_condition,
        "",
    ]
    return "\n".join(lines)


def write_plan(plan: SwarmPlan, out_dir: str | Path) -> Path:
    """Write a swarm plan and packet Markdown files."""
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)
    plan_path = root / "swarm_plan.json"
    plan_path.write_text(json.dumps(plan.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for packet in plan.packets:
        (root / f"{packet.agent_id}.md").write_text(render_packet(plan, packet.agent_id), encoding="utf-8")
    return plan_path


def load_plan(out_dir: str | Path) -> SwarmPlan:
    """Load a local swarm plan from a directory."""
    raw = json.loads((Path(out_dir) / "swarm_plan.json").read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("swarm_plan.json must contain an object")
    return SwarmPlan.from_dict(raw)


def _prefixed(lines: list[str], prefix: str) -> list[str]:
    values: list[str] = []
    for line in lines:
        if line.lower().startswith(prefix.lower()):
            values.append(line.split(":", 1)[1].strip())
    return values


def collect_reports(reports_dir: str | Path) -> ReportSummary:
    """Collect status and evidence lines from local report Markdown files."""
    root = Path(reports_dir)
    files = sorted(root.glob("*.md")) if root.exists() else []
    statuses: list[str] = []
    evidence: list[str] = []
    for path in files:
        lines = path.read_text(encoding="utf-8").splitlines()
        statuses.extend(_prefixed(lines, "Status:"))
        evidence.extend(_prefixed(lines, "Evidence:"))
    return ReportSummary(
        report_count=len(files),
        statuses=tuple(statuses),
        evidence=tuple(evidence),
        files=tuple(str(path) for path in files),
    )


def review_reports(reports_dir: str | Path) -> ReviewResult:
    """Review whether local reports contain minimum completion evidence."""
    summary = collect_reports(reports_dir)
    findings: list[str] = []
    if summary.report_count == 0:
        findings.append("no reports found")
    if not summary.evidence:
        findings.append("missing evidence")
    non_done = [status for status in summary.statuses if status.lower() != "done"]
    if non_done:
        findings.append(f"non-done statuses: {', '.join(non_done)}")
    if summary.report_count and not summary.statuses:
        findings.append("missing status")
    return ReviewResult(ok=not findings, findings=tuple(findings))
