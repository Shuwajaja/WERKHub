import json

from werktools.cli import main
from werktools.tools.integration_gate import (
    add_connector,
    audit_recent,
    connectors,
    explain_policy,
    request_access,
    show_connector,
)


def _gate_with_connector(tmp_path, scopes=None, profiles=("default",)):
    root = tmp_path / "gate"
    connector = add_connector(
        root,
        "github",
        label="GitHub",
        provider="github.com",
        scopes=scopes
        or (
            {"name": "repo:read", "access": "read", "description": "Read repository contents"},
            {"name": "repo:write", "access": "write", "description": "Push commits"},
        ),
        profiles=profiles,
        docs_url="https://docs.github.com",
    )
    return root, connector


def test_add_connector_and_list_are_profile_filtered(tmp_path):
    root, _ = _gate_with_connector(tmp_path, profiles=("admin",))

    assert connectors(root, profile="default") == []
    assert [c.connector_id for c in connectors(root, profile="admin")] == ["github"]


def test_add_connector_rejects_secret_like_metadata(tmp_path):
    root = tmp_path / "gate"

    try:
        add_connector(
            root,
            "leaky",
            label="Leaky",
            provider="x",
            scopes=({"name": "s", "access": "read", "description": "d"},),
            metadata={"api_key": "sk-123"},
        )
    except ValueError as exc:
        assert "secret" in str(exc).lower()
    else:
        raise AssertionError("expected secret-like metadata to be rejected")


def test_add_connector_rejects_nested_secret_metadata(tmp_path):
    root = tmp_path / "gate"

    try:
        add_connector(
            root,
            "leaky",
            label="Leaky",
            provider="x",
            scopes=({"name": "s", "access": "read", "description": "d"},),
            metadata={"info": {"nested": {"api_key": "sk-123"}}},
        )
    except ValueError as exc:
        assert "secret" in str(exc).lower()
    else:
        raise AssertionError("expected nested secret-like metadata to be rejected")


def test_add_connector_rejects_empty_scope_name(tmp_path):
    root = tmp_path / "gate"

    try:
        add_connector(
            root,
            "broken",
            scopes=({"name": "", "access": "read", "description": "d"},),
        )
    except ValueError as exc:
        assert "name" in str(exc).lower()
    else:
        raise AssertionError("expected empty scope name to be rejected")


def test_explain_policy_does_not_disclose_hidden_connectors(tmp_path):
    root, _ = _gate_with_connector(tmp_path, profiles=("admin",))

    hidden = explain_policy(root, "github", profile="default", scope="repo:read")
    unknown = explain_policy(root, "missing", profile="default", scope="repo:read")

    assert hidden.decision == "deny"
    assert unknown.decision == "deny"
    assert hidden.reason == unknown.reason


def test_integration_cli_add_rejects_invalid_access(tmp_path, capsys):
    root = tmp_path / "gate"

    code = main(
        [
            "integration",
            "add",
            "typo",
            "--dir",
            str(root),
            "--scope",
            "s=reed:typo access",
        ]
    )

    assert code == 1
    assert "access" in capsys.readouterr().err.lower()


def test_show_connector_lists_scopes_before_any_approval(tmp_path):
    root, _ = _gate_with_connector(tmp_path)

    connector = show_connector(root, "github", profile="default")

    assert [scope.name for scope in connector.scopes] == ["repo:read", "repo:write"]
    assert [scope.access for scope in connector.scopes] == ["read", "write"]


def test_explain_policy_read_allows_write_requires_approval(tmp_path):
    root, _ = _gate_with_connector(tmp_path)

    read_decision = explain_policy(root, "github", profile="default", scope="repo:read")
    write_decision = explain_policy(root, "github", profile="default", scope="repo:write")

    assert read_decision.decision == "allow"
    assert write_decision.decision == "approval_required"


def test_explain_policy_fails_closed_on_unknowns(tmp_path):
    root, _ = _gate_with_connector(tmp_path)

    unknown_connector = explain_policy(root, "missing", profile="default", scope="x")
    unknown_scope = explain_policy(root, "github", profile="default", scope="nope")

    assert unknown_connector.decision == "deny"
    assert unknown_scope.decision == "deny"


def test_request_access_creates_pending_request_and_audits(tmp_path):
    root, _ = _gate_with_connector(tmp_path)

    request = request_access(root, "github", profile="default", scopes=("repo:write",))

    raw = json.loads((root / "approvals" / f"{request.request_id}.json").read_text(encoding="utf-8"))
    assert raw["status"] == "pending"
    assert raw["scopes"] == ["repo:write"]
    events = audit_recent(root, limit=1)
    assert events[0].event_type == "integration.access.requested"


def test_request_access_never_grants(tmp_path):
    root, _ = _gate_with_connector(tmp_path)

    request = request_access(root, "github", profile="default", scopes=("repo:write",))

    assert request.status == "pending"
    decision = explain_policy(root, "github", profile="default", scope="repo:write")
    assert decision.decision == "approval_required"


def test_load_connectors_corrupt_json_warns(tmp_path):
    # _load_connectors (via connectors()) must emit a UserWarning and return []
    # when connectors.json contains corrupt JSON.
    import warnings as _warnings

    root = tmp_path / "gate"
    root.mkdir()
    (root / "connectors.json").write_text("{corrupt", encoding="utf-8")

    with _warnings.catch_warnings(record=True) as caught:
        _warnings.simplefilter("always")
        result = connectors(root)

    assert result == []
    warning_messages = [str(w.message) for w in caught]
    assert any("corrupt" in m.lower() for m in warning_messages), (
        f"expected a 'corrupt' warning; got: {warning_messages}"
    )


def test_integration_cli_add_list_show_explain_request_audit(tmp_path, capsys):
    root = tmp_path / "gate"

    assert (
        main(
            [
                "integration",
                "add",
                "github",
                "--dir",
                str(root),
                "--label",
                "GitHub",
                "--provider",
                "github.com",
                "--scope",
                "repo:read=read:Read repository contents",
                "--scope",
                "repo:write=write:Push commits",
            ]
        )
        == 0
    )
    assert main(["integration", "list", "--dir", str(root)]) == 0
    assert "github" in capsys.readouterr().out

    assert main(["integration", "show", "github", "--dir", str(root)]) == 0
    assert "repo:write" in capsys.readouterr().out

    assert main(["integration", "explain", "github", "--dir", str(root), "--scope", "repo:write"]) == 0
    assert "approval_required" in capsys.readouterr().out

    assert main(["integration", "request-access", "github", "--dir", str(root), "--scope", "repo:write"]) == 0
    assert "pending" in capsys.readouterr().out

    assert main(["integration", "audit", "--dir", str(root), "--limit", "1"]) == 0
    assert "integration.access.requested" in capsys.readouterr().out
