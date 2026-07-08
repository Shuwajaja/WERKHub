"""Tests for capability selection + presence-only key status (hub/capability_select.py)."""

from __future__ import annotations

from werktools.hub import capability_select as cs
from werktools.hub.registry_db import Capability


def cap(cid, *, kind="tool", category="other", trust="Official", what="",
        deluxe=False, needs_keys=(), risk="unknown") -> Capability:
    return Capability(
        id=cid, kind=kind, category=category, trust_tier=trust, what_it_is=what,
        maintainer="", maintenance="active", popularity="unverified", security_note="",
        deluxe_reason="", deluxe_base=deluxe, verified=False, needs_keys=tuple(needs_keys), risk=risk,
    )


def test_key_status_presence_only(monkeypatch):
    monkeypatch.setenv("WERK_TEST_KEY", "super-secret-value")
    monkeypatch.delenv("WERK_TEST_ABSENT", raising=False)
    st = cs.key_status(["WERK_TEST_KEY", "WERK_TEST_ABSENT"])
    assert st == {"WERK_TEST_KEY": True, "WERK_TEST_ABSENT": False}
    # never leaks the value, only presence booleans
    assert "super-secret-value" not in repr(st)
    assert all(isinstance(v, bool) for v in st.values())


def test_keys_satisfied(monkeypatch):
    monkeypatch.setenv("HAVE_KEY", "x")
    monkeypatch.delenv("NO_KEY", raising=False)
    assert cs.keys_satisfied(cap("a", needs_keys=("HAVE_KEY",))) is True
    assert cs.keys_satisfied(cap("b", needs_keys=("NO_KEY",))) is False
    assert cs.keys_satisfied(cap("c")) is True


def test_select_trust_and_relevance():
    caps = [
        cap("github-mcp", category="dev", what="git pull requests", trust="Official"),
        cap("sketchy", category="dev", what="git stuff", trust="Community-Unverified"),
        cap("weather", category="other", what="forecast", trust="Official"),
    ]
    sel = cs.select_capabilities(caps, "review a git pull request")
    inc = {d.capability.id for d in sel.included}
    assert "github-mcp" in inc
    assert "sketchy" not in inc  # trust denied (deny-by-default)
    assert "weather" not in inc  # irrelevant
    assert all(d.reason for d in sel.included + sel.excluded)  # every verdict has a reason
    assert any("trust" in d.reason for d in sel.excluded)


def test_select_key_missing_excludes(monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    caps = [cap("stripe", category="payments", what="stripe payments", needs_keys=("STRIPE_SECRET_KEY",))]
    sel = cs.select_capabilities(caps, "take a stripe payment")
    assert not sel.included
    assert any("missing key" in d.reason for d in sel.excluded)
    # require_keys=False lets it enter (operator override)
    sel2 = cs.select_capabilities(caps, "take a stripe payment", require_keys=False)
    assert {d.capability.id for d in sel2.included} == {"stripe"}


def test_select_risk_gate():
    caps = [cap("danger", category="files", what="delete files", risk="destructive")]
    sel = cs.select_capabilities(caps, "delete files")
    assert not sel.included
    assert any("approval token" in d.reason for d in sel.excluded)


def test_select_budget_caps():
    caps = [cap(f"t{i}", category="dev", what="git code review") for i in range(10)]
    sel = cs.select_capabilities(caps, "git code review", budget=3)
    assert len(sel.included) == 3
    assert any("over budget" in d.reason for d in sel.excluded)


def test_budget_minus_one_uses_cap_n_not_raw_budget():
    # Regression: budget=-1 should clamp to cap_n=0 and the exclusion reason
    # must say 'top 0', not 'top -1'.
    caps = [cap("t0", category="dev", what="git code review")]
    sel = cs.select_capabilities(caps, "git code review", budget=-1)
    assert len(sel.included) == 0
    for d in sel.excluded:
        if "over budget" in d.reason:
            assert "top -1" not in d.reason, f"reason must not expose raw budget=-1: {d.reason!r}"
            assert "top 0" in d.reason, f"reason must use clamped cap_n=0: {d.reason!r}"


def test_deluxe_breaks_ties():
    caps = [
        cap("plain", category="dev", what="git code", deluxe=False),
        cap("starred", category="dev", what="git code", deluxe=True),
    ]
    sel = cs.select_capabilities(caps, "git code", budget=1)
    assert [d.capability.id for d in sel.included] == ["starred"]


def test_load_skill_capabilities(tmp_path):
    (tmp_path / "review.md").write_text(
        "# Review Policy\nrisk: read\ntags: review, policy\n\nHow to review the policy module.\n",
        encoding="utf-8",
    )
    rows = cs.load_skill_capabilities(tmp_path)
    assert rows and rows[0]["kind"] == "skill"
    assert rows[0]["id"] == "review"
    assert rows[0]["category"] == "review"


def test_load_skill_capabilities_missing(tmp_path):
    assert cs.load_skill_capabilities(tmp_path / "nope") == []


def test_select_capabilities_empty_task_warns(tmp_path):
    """select_capabilities must emit a warning and return an empty included list
    when the task string produces no tokens (Fix 3)."""
    import pytest

    from werktools.hub.registry_db import build_registry

    db = tmp_path / "r.db"
    build_registry(db, [{"server_id": "x", "trust_tier": "Official", "what_it_is": "something"}])
    caps_list = [
        cs.Capability(
            id="x", kind="tool", category="other", trust_tier="Official",
            what_it_is="something", maintainer="", maintenance="active",
            popularity="unverified", security_note="", deluxe_reason="",
            deluxe_base=False, verified=False, needs_keys=(), risk="read",
        )
    ]
    with pytest.warns(UserWarning, match="no tokens"):
        sel = cs.select_capabilities(caps_list, "")
    assert list(sel.included) == []
