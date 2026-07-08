from werktools.hub.policy import enforce, enforcement_snapshot, explain
from werktools.hub.registry import default_config
from werktools.policy import decide


def test_reviewer_can_read_docs():
    decision = explain(default_config(), "claude-reviewer", "docs.search")

    assert decision.decision == "allow"
    assert decision.ok is True


def test_reviewer_hides_write_tool():
    decision = explain(default_config(), "claude-reviewer", "filesystem.write_file")

    assert decision.decision == "hidden"
    assert decision.ok is False
    assert "not visible" in decision.reason


def test_builder_external_write_requires_approval():
    decision = explain(default_config(), "codex-builder", "github.create_pr")

    assert decision.decision == "approval_required"
    assert decision.ok is False
    assert "approval" in decision.reason.lower()


def test_shell_run_denied_for_builder():
    decision = explain(default_config(), "codex-builder", "shell.run")

    assert decision.decision == "deny"
    assert "destructive" in decision.reason.lower()


def test_unknown_tool_denies_fail_closed():
    decision = explain(default_config(), "codex-builder", "missing.tool")

    assert decision.decision == "deny"
    assert decision.risk == "unknown"


def test_source_annotations_cannot_loosen_policy():
    decision = explain(default_config(), "codex-builder", "shell.run")

    assert decision.decision == "deny"


def test_enforcement_snapshot_allows_only_explained_allow():
    snapshot = enforcement_snapshot(default_config(), "claude-reviewer")

    assert decide(snapshot, "docs.search") == "allow"
    assert decide(snapshot, "filesystem.write_file") == "deny"
    assert decide(snapshot, "shell.run") == "deny"
    assert decide(snapshot, "missing.tool") == "deny"


def test_enforce_allows_read_tool():
    decision = enforce(default_config(), "claude-reviewer", "docs.search")

    assert decision.ok is True
    assert decision.decision == "allow"


def test_enforce_fails_closed_on_approval_required():
    decision = enforce(default_config(), "codex-builder", "github.create_pr")

    assert decision.ok is False
    assert decision.decision == "approval_required"


def test_enforce_fails_closed_on_hidden_and_unknown():
    hidden = enforce(default_config(), "claude-reviewer", "filesystem.write_file")
    unknown = enforce(default_config(), "claude-reviewer", "missing.tool")

    assert hidden.ok is False
    assert unknown.ok is False
    assert unknown.decision == "deny"


def test_community_profiles_are_policy_correct():
    from werktools.hub.registry import community_default_config, default_config, load_config

    base = default_config().to_dict()
    community = community_default_config().to_dict()
    merged = load_config({**community, "tools": base["tools"]})

    # reader: read tool allow, write tool hidden (cautious read/docs only)
    assert explain(merged, "community-reader", "docs.search").decision == "allow"
    assert explain(merged, "community-reader", "filesystem.write_file").decision == "hidden"
    # builder: write/external require approval (not allow)
    assert explain(merged, "community-builder", "filesystem.write_file").decision == "approval_required"
    assert explain(merged, "community-builder", "github.create_pr").decision == "approval_required"
    # admin: an unknown-risk tool requires approval, not deny (vs cautious/balanced fail-closed)
    assert explain(merged, "community-admin", "mystery.tool").decision == "approval_required"
    # destructive tools stay hidden (not in any community visible_tags)
    assert explain(merged, "community-admin", "shell.run").decision == "hidden"


def test_enforce_denies_bridge_verbs_as_downstream_targets():
    # tool_call("tool_call", ...) must not recurse through the always-allow
    # bridge list; the hub explanation knows no such tool and fails closed.
    decision = enforce(default_config(), "human-admin", "tool_call")

    assert decision.ok is False
    assert decision.decision == "deny"
