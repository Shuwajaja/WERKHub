"""Local policy explanation for static WERK Hub tool cards."""

from __future__ import annotations

from werktools.policy import PolicySnapshot, resolve
from werktools.profile import Profile

from .contracts import HubConfig, HubProfile, PolicyDecision, ToolCard
from .registry import get_profile, get_tool, visible_tools


def _decision(
    decision: str,
    profile: HubProfile | None,
    tool_id: str,
    reason: str,
    risk: str = "unknown",
    requires_approval: bool = False,
    visible: bool = True,
) -> PolicyDecision:
    return PolicyDecision(
        decision=decision,
        tool_id=tool_id,
        profile_id=profile.id if profile is not None else "",
        reason=reason,
        risk=risk,
        requires_approval=requires_approval,
        visible=visible,
    )


def _risk_decision(profile: HubProfile, tool: ToolCard) -> tuple[str, str]:
    permission = profile.permission_profile
    risk = tool.risk

    if risk == "read":
        return "allow", "read tool is allowed for this profile"

    if risk in {"destructive", "secret"}:
        if permission == "admin":
            return "approval_required", f"{risk} tool requires human approval"
        return "deny", f"{risk} tools are denied for {permission} profiles"

    if risk == "unknown":
        if permission == "admin":
            return "approval_required", "unknown-risk tool requires human approval"
        return "deny", "unknown-risk tools fail closed"

    if permission == "cautious":
        return "deny", f"{risk} tools are denied for cautious profiles"

    if permission == "balanced":
        if risk in {"write", "external"}:
            return "approval_required", f"{risk} tool requires approval"
        return "deny", f"{risk} tool is outside balanced profile policy"

    if permission == "admin":
        if risk in {"write", "external"}:
            return "allow", f"{risk} tool is allowed for admin profile"
        return "approval_required", f"{risk} tool requires human approval"

    return "deny", f"unknown permission profile {permission!r} fails closed"


def enforcement_snapshot(config: HubConfig, profile_id: str | None = None) -> PolicySnapshot:
    """Project the hub explanation policy onto the core enforcement axis.

    A tool is allowed if and only if its hub explanation is exactly `allow`;
    approval_required, hidden, and deny all fail closed (ADR-001).
    """
    profile = get_profile(config, profile_id)
    visible_ids = [tool.id for tool in visible_tools(config, profile.id)]
    allowed_ids = [
        tool_id for tool_id in visible_ids if explain(config, profile.id, tool_id).decision == "allow"
    ]
    core_profile = Profile(
        id=profile.id,
        role=profile.permission_profile,
        tools_visible=tuple(visible_ids),
        tools_allowed=tuple(allowed_ids),
    )
    registry = [tool.to_dict() for tool in config.tools]
    return resolve(core_profile, registry)


def enforce(config: HubConfig, profile_id: str | None, tool_id: str) -> PolicyDecision:
    """Single fail-closed execution gate: enforce() == explain()-is-allow.

    Executes only when the hub explanation is exactly `allow`; every other
    decision (approval_required, hidden, deny) is returned unchanged and fails
    closed. This is NOT a two-axis defense-in-depth check: `enforcement_snapshot`
    derives its allow-set directly from this same explanation, so a snapshot
    round-trip here would be a tautology, not an independent axis. The core
    BRIDGE_TOOLS bypass cannot leak through, because `explain` knows no bridge
    verb and denies it before any snapshot is consulted. `enforcement_snapshot`
    is retained for the core-projection consumers/tests that need the snapshot.
    """
    return explain(config, profile_id or config.default_profile, tool_id)


def explain(config: HubConfig, profile_id: str, tool_id: str) -> PolicyDecision:
    """Explain local policy for a profile/tool pair without executing anything."""
    try:
        profile = get_profile(config, profile_id)
    except KeyError:
        return _decision(
            "deny",
            None,
            tool_id,
            f"unknown profile {profile_id!r} fails closed",
        )

    try:
        tool = get_tool(config, tool_id)
    except KeyError:
        return _decision(
            "deny",
            profile,
            tool_id,
            f"unknown tool {tool_id!r} fails closed",
        )

    visible_ids = {item.id for item in visible_tools(config, profile.id)}
    if tool.id not in visible_ids:
        return _decision(
            "hidden",
            profile,
            tool.id,
            f"tool {tool.id!r} is not visible to profile {profile.id!r}",
            risk=tool.risk,
            visible=False,
        )

    decision, reason = _risk_decision(profile, tool)
    if decision == "allow" and tool.requires_approval:
        decision = "approval_required"
        reason = "local tool card requires approval"

    return _decision(
        decision,
        profile,
        tool.id,
        reason,
        risk=tool.risk,
        requires_approval=decision == "approval_required",
    )
