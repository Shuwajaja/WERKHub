"""Two-axis fail-closed tool policy resolver."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from werktools.profile import Profile

BRIDGE_TOOLS: frozenset[str] = frozenset(
    {"tool_search", "tool_describe", "tool_call"}
)


@dataclass(frozen=True)
class PolicySnapshot:
    visible: tuple[str, ...]
    allowed: tuple[str, ...]
    blocked: tuple[str, ...]
    hidden: tuple[str, ...]
    risk: str
    counts: dict[str, int]
    decisions: dict[str, str]


def _risk_from_visible(visible_ids: tuple[str, ...], registry: list[dict]) -> str:
    by_id = {str(tool.get("id")): tool for tool in registry if tool.get("id")}
    risk = "low"
    for tool_id in visible_ids:
        tool = by_id.get(tool_id)
        if tool is None:
            continue
        if bool(tool.get("destructive", False)):
            risk = "high"
            continue
        if not bool(tool.get("read_only", True)) and risk == "low":
            risk = "medium"
    return risk


def resolve(profile: "Profile", registry: list[dict]) -> PolicySnapshot:
    """Resolve profile allow/visible lists against a tool registry."""
    tools_allowed = frozenset(profile.tools_allowed or ())
    tools_visible = frozenset(profile.tools_visible or ())

    visible: list[str] = []
    allowed: list[str] = []
    blocked: list[str] = []
    hidden: list[str] = []
    decisions: dict[str, str] = {}

    for tool in registry:
        tool_id_raw = tool.get("id")
        if not tool_id_raw:
            continue
        tool_id = str(tool_id_raw)

        if tool_id not in tools_allowed:
            blocked.append(tool_id)
            decisions[tool_id] = "deny"
            continue

        allowed.append(tool_id)
        if tool_id in tools_visible:
            visible.append(tool_id)
            decisions[tool_id] = "allow"
        else:
            hidden.append(tool_id)
            decisions[tool_id] = "hidden"

    counts = {
        "visible": len(visible),
        "allowed": len(allowed),
        "blocked": len(blocked),
        "hidden": len(hidden),
    }

    return PolicySnapshot(
        visible=tuple(visible),
        allowed=tuple(allowed),
        blocked=tuple(blocked),
        hidden=tuple(hidden),
        risk=_risk_from_visible(tuple(visible), registry),
        counts=counts,
        decisions=decisions,
    )


def decide(snapshot: PolicySnapshot, tool_id: str) -> str:
    """Return the call-time verdict for a tool id."""
    if tool_id in BRIDGE_TOOLS:
        return "allow"

    verdict = snapshot.decisions.get(tool_id)
    if verdict is None:
        return "deny"
    if verdict == "hidden":
        return "allow"
    if verdict == "deny":
        return "deny"
    return str(verdict)
