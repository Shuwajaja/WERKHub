"""Offline, deterministic tests for werktools.policy."""

from __future__ import annotations

import pytest

from werktools.policy import BRIDGE_TOOLS, PolicySnapshot, decide, resolve
from werktools.profile import Profile


def _reg(*tools: dict) -> list[dict]:
    return list(tools)


def _tool(
    id: str,
    kind: str = "skill",
    read_only: bool = True,
    destructive: bool = False,
    description: str = "",
) -> dict:
    return {
        "id": id,
        "kind": kind,
        "read_only": read_only,
        "destructive": destructive,
        "description": description,
    }


def _profile(
    tools_allowed: tuple[str, ...] = (),
    tools_visible: tuple[str, ...] = (),
) -> Profile:
    return Profile(
        id="agent-1",
        role="worker",
        tools_allowed=tools_allowed,
        tools_visible=tools_visible,
    )


class TestBridgeTools:
    def test_is_frozenset(self):
        assert isinstance(BRIDGE_TOOLS, frozenset)

    def test_contains_expected_meta_tools(self):
        assert "tool_search" in BRIDGE_TOOLS
        assert "tool_describe" in BRIDGE_TOOLS
        assert "tool_call" in BRIDGE_TOOLS

    def test_non_empty(self):
        assert len(BRIDGE_TOOLS) >= 3


class TestPolicySnapshotDataclass:
    def _make(self, **overrides) -> PolicySnapshot:
        defaults = dict(
            visible=("a",),
            allowed=("a",),
            blocked=(),
            hidden=(),
            risk="low",
            counts={"visible": 1, "allowed": 1, "blocked": 0, "hidden": 0},
            decisions={"a": "allow"},
        )
        defaults.update(overrides)
        return PolicySnapshot(**defaults)

    def test_is_frozen(self):
        snap = self._make()
        with pytest.raises((AttributeError, TypeError)):
            snap.risk = "high"  # type: ignore[misc]

    def test_fields_present(self):
        snap = self._make()
        assert hasattr(snap, "visible")
        assert hasattr(snap, "allowed")
        assert hasattr(snap, "blocked")
        assert hasattr(snap, "hidden")
        assert hasattr(snap, "risk")
        assert hasattr(snap, "counts")
        assert hasattr(snap, "decisions")

    def test_decisions_is_dict_like(self):
        snap = self._make()
        assert snap.decisions["a"] == "allow"

    def test_counts_is_dict(self):
        snap = self._make()
        assert isinstance(snap.counts, dict)


class TestResolvePermissionAxis:
    def test_allowed_tool_appears_in_allowed(self):
        reg = _reg(_tool("read-db", kind="skill"))
        prof = _profile(tools_allowed=("read-db",), tools_visible=("read-db",))
        snap = resolve(prof, reg)
        assert "read-db" in snap.allowed

    def test_not_allowed_tool_appears_in_blocked(self):
        reg = _reg(_tool("exec-shell", kind="skill"))
        prof = _profile(tools_allowed=(), tools_visible=())
        snap = resolve(prof, reg)
        assert "exec-shell" in snap.blocked

    def test_blocked_tool_not_in_allowed(self):
        reg = _reg(_tool("exec-shell", kind="skill"))
        prof = _profile(tools_allowed=(), tools_visible=())
        snap = resolve(prof, reg)
        assert "exec-shell" not in snap.allowed

    def test_unknown_tool_not_in_registry_is_ignored(self):
        reg = _reg(_tool("real-tool", kind="skill"))
        prof = _profile(
            tools_allowed=("real-tool", "ghost-tool"),
            tools_visible=("real-tool",),
        )
        snap = resolve(prof, reg)
        assert "ghost-tool" not in snap.allowed
        assert "ghost-tool" not in snap.blocked

    def test_empty_registry_yields_empty_allowed_and_blocked(self):
        prof = _profile()
        snap = resolve(prof, [])
        assert snap.allowed == ()
        assert snap.blocked == ()


class TestResolveVisibilityAxis:
    def test_visible_tool_appears_in_visible(self):
        reg = _reg(_tool("list-files"))
        prof = _profile(tools_allowed=("list-files",), tools_visible=("list-files",))
        snap = resolve(prof, reg)
        assert "list-files" in snap.visible

    def test_allowed_but_not_visible_appears_in_hidden(self):
        reg = _reg(_tool("background-job"))
        prof = _profile(tools_allowed=("background-job",), tools_visible=())
        snap = resolve(prof, reg)
        assert "background-job" in snap.hidden
        assert "background-job" not in snap.visible

    def test_blocked_tool_never_in_visible(self):
        reg = _reg(_tool("forbidden"))
        prof = _profile(tools_allowed=(), tools_visible=("forbidden",))
        snap = resolve(prof, reg)
        assert "forbidden" not in snap.visible
        assert "forbidden" in snap.blocked

    def test_visible_is_subset_of_allowed(self):
        reg = _reg(_tool("a"), _tool("b"), _tool("c"))
        prof = _profile(tools_allowed=("a", "b"), tools_visible=("a",))
        snap = resolve(prof, reg)
        for visible in snap.visible:
            assert visible in snap.allowed


class TestResolveRisk:
    def test_all_read_only_is_low(self):
        reg = _reg(_tool("r1", read_only=True, destructive=False))
        prof = _profile(tools_allowed=("r1",), tools_visible=("r1",))
        snap = resolve(prof, reg)
        assert snap.risk == "low"

    def test_writable_visible_tool_is_medium(self):
        reg = _reg(_tool("w1", read_only=False, destructive=False))
        prof = _profile(tools_allowed=("w1",), tools_visible=("w1",))
        snap = resolve(prof, reg)
        assert snap.risk in ("medium", "high", "critical")

    def test_destructive_visible_tool_at_least_high(self):
        reg = _reg(_tool("nuke", read_only=False, destructive=True))
        prof = _profile(tools_allowed=("nuke",), tools_visible=("nuke",))
        snap = resolve(prof, reg)
        assert snap.risk in ("high", "critical")

    def test_empty_visible_defaults_low(self):
        prof = _profile()
        snap = resolve(prof, [])
        assert snap.risk == "low"


class TestResolveCounts:
    def test_counts_match_tuple_lengths(self):
        reg = _reg(_tool("a"), _tool("b"), _tool("c"))
        prof = _profile(tools_allowed=("a", "b"), tools_visible=("a",))
        snap = resolve(prof, reg)
        assert snap.counts["visible"] == len(snap.visible)
        assert snap.counts["allowed"] == len(snap.allowed)
        assert snap.counts["blocked"] == len(snap.blocked)
        assert snap.counts["hidden"] == len(snap.hidden)


class TestDecide:
    def _snap_with(self, decisions: dict) -> PolicySnapshot:
        allowed = tuple(key for key, value in decisions.items() if value == "allow")
        blocked = tuple(key for key, value in decisions.items() if value == "deny")
        hidden = tuple(key for key, value in decisions.items() if value == "hidden")
        visible = tuple(key for key, value in decisions.items() if value == "allow")
        counts = {
            "visible": len(visible),
            "allowed": len(allowed),
            "blocked": len(blocked),
            "hidden": len(hidden),
        }
        return PolicySnapshot(
            visible=visible,
            allowed=allowed,
            blocked=blocked,
            hidden=hidden,
            risk="low",
            counts=counts,
            decisions=decisions,
        )

    def test_known_allow_returns_allow(self):
        snap = self._snap_with({"my-tool": "allow"})
        assert decide(snap, "my-tool") == "allow"

    def test_known_deny_returns_deny(self):
        snap = self._snap_with({"bad-tool": "deny"})
        assert decide(snap, "bad-tool") == "deny"

    def test_unknown_tool_returns_deny_fail_closed(self):
        snap = self._snap_with({"known": "allow"})
        assert decide(snap, "completely-unknown-id") == "deny"

    def test_empty_snapshot_returns_deny(self):
        snap = PolicySnapshot(
            visible=(),
            allowed=(),
            blocked=(),
            hidden=(),
            risk="low",
            counts={"visible": 0, "allowed": 0, "blocked": 0, "hidden": 0},
            decisions={},
        )
        assert decide(snap, "anything") == "deny"

    def test_bridge_tools_always_allow(self):
        snap = PolicySnapshot(
            visible=(),
            allowed=(),
            blocked=(),
            hidden=(),
            risk="low",
            counts={"visible": 0, "allowed": 0, "blocked": 0, "hidden": 0},
            decisions={},
        )
        for bridge_tool in BRIDGE_TOOLS:
            assert decide(snap, bridge_tool) == "allow"

    def test_hidden_tool_is_reachable(self):
        snap = self._snap_with({"hidden-tool": "hidden"})
        result = decide(snap, "hidden-tool")
        assert result != "deny"

    def test_returns_string(self):
        snap = self._snap_with({"t": "allow"})
        assert isinstance(decide(snap, "t"), str)


class TestResolveDecideRoundTrip:
    def test_allowed_tool_decidable_as_allow(self):
        reg = _reg(_tool("safe-read"))
        prof = _profile(tools_allowed=("safe-read",), tools_visible=("safe-read",))
        snap = resolve(prof, reg)
        assert decide(snap, "safe-read") != "deny"

    def test_blocked_tool_decidable_as_deny(self):
        reg = _reg(_tool("blocked-tool"))
        prof = _profile(tools_allowed=(), tools_visible=())
        snap = resolve(prof, reg)
        assert decide(snap, "blocked-tool") == "deny"

    def test_bridge_tools_decidable_after_resolve(self):
        prof = _profile()
        snap = resolve(prof, [])
        for bridge_tool in BRIDGE_TOOLS:
            assert decide(snap, bridge_tool) == "allow"

    def test_multiple_tools_partitioned_correctly(self):
        reg = _reg(
            _tool("visible-a"),
            _tool("hidden-b"),
            _tool("blocked-c"),
        )
        prof = _profile(
            tools_allowed=("visible-a", "hidden-b"),
            tools_visible=("visible-a",),
        )
        snap = resolve(prof, reg)
        assert "visible-a" in snap.visible
        assert "hidden-b" in snap.hidden
        assert "blocked-c" in snap.blocked
        assert decide(snap, "blocked-c") == "deny"
        assert decide(snap, "hidden-b") != "deny"
