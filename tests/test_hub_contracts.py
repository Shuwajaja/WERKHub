from werktools.hub.contracts import (
    DECISIONS,
    EVENT_NAMES,
    RISK_CLASSES,
    DownstreamServer,
    HubConfig,
    HubProfile,
    PolicyDecision,
    RegistryCandidate,
    ToolCard,
)


def test_tool_card_round_trip_preserves_unknown_risk():
    card = ToolCard.from_dict({"id": "mystery.tool", "name": "mystery"})

    assert card.risk == "unknown"
    assert card.to_dict()["risk"] == "unknown"


def test_downstream_to_dict_redact_masks_headers_env_and_args():
    # SF11: to_dict(redact=True) masks auth headers, env values, and secret-
    # looking args for trace/inspection; the default keeps the real secrets so
    # a host config can still connect. Construct the dataclass directly to
    # bypass from_dict's secret-env-key guard.
    server = DownstreamServer(
        id="r",
        command="",
        args=("--token=abc123secret",),
        enabled=True,
        transport="http",
        url="https://x/mcp",
        headers={"Authorization": "Bearer sk-123", "X-Tag": "v1"},
        env={"API_KEY_REF": "leakme"},
    )

    plain = server.to_dict()
    assert plain["headers"]["Authorization"] == "Bearer sk-123"
    assert plain["env"]["API_KEY_REF"] == "leakme"
    assert "abc123secret" in str(plain["args"])

    masked = server.to_dict(redact=True)
    assert masked["headers"]["Authorization"] == "[redacted]"
    assert masked["headers"]["X-Tag"] == "v1"  # non-secret key preserved
    assert masked["env"]["API_KEY_REF"] == "[redacted]"
    assert "abc123secret" not in str(masked["args"])


def test_tool_card_tags_and_annotations_are_json_friendly():
    card = ToolCard.from_dict(
        {
            "id": "docs.search",
            "name": "search",
            "tags": ["docs", "read"],
            "source_annotations": {"readOnlyHint": True},
        }
    )

    assert card.tags == ("docs", "read")
    assert card.to_dict()["source_annotations"]["readOnlyHint"] is True


def test_hub_profile_defaults_are_cautious():
    profile = HubProfile.from_dict({"id": "claude-reviewer"})

    assert profile.permission_profile == "cautious"
    assert profile.visible_tags == ("read",)


def test_hub_config_round_trip():
    config = HubConfig.from_dict(
        {
            "name": "local",
            "default_profile": "codex-builder",
            "ledger_path": ".werktools/hub-ledger.jsonl",
            "profiles": [{"id": "codex-builder", "permission_profile": "balanced"}],
            "tools": [{"id": "docs.search", "name": "search", "risk": "read"}],
        }
    )

    body = config.to_dict()
    assert body["name"] == "local"
    assert body["profiles"][0]["id"] == "codex-builder"
    assert body["tools"][0]["id"] == "docs.search"


def test_policy_decision_shape():
    decision = PolicyDecision(
        decision="deny",
        tool_id="shell.run",
        profile_id="claude-reviewer",
        reason="destructive tools are denied for cautious profiles",
        risk="destructive",
    )

    assert decision.to_dict()["decision"] == "deny"
    assert decision.ok is False


def test_contract_vocabularies_include_required_values():
    assert set(RISK_CLASSES) >= {
        "read",
        "write",
        "destructive",
        "external",
        "secret",
        "unknown",
    }
    assert set(DECISIONS) >= {"allow", "deny", "approval_required", "hidden"}
    assert "policy.explained" in EVENT_NAMES


def test_p1_event_names_added_once_total_43():
    # Verified base = 40 (ends "policy.explained"); P1 adds exactly +3.
    assert {
        "runtime.probed",
        "registry.allowlist.error",
        "registry.allowlist.tier_downgrade",
    } <= set(EVENT_NAMES)
    assert len(EVENT_NAMES) == 43
    assert len(set(EVENT_NAMES)) == len(EVENT_NAMES)  # still unique


def test_registry_candidate_oversized_fields_are_truncated():
    """Untrusted external registry data must be capped at the stated boundaries."""
    raw = {
        "id": "x",
        "name": "A" * 200,
        "description": "B" * 600,
        "source_url": "C" * 300,
        "version": "D" * 50,
        "packages": [],
        "metadata": {"long_val": "E" * 300},
    }
    cand = RegistryCandidate.from_dict(raw)
    assert len(cand.name) <= 128
    assert len(cand.description) <= 512
    assert len(cand.source_url) <= 256
    assert len(cand.registry_version) <= 32
    assert all(len(v) <= 256 for v in cand.metadata.values() if isinstance(v, str))


def test_downstream_server_http_transport_without_url_raises():
    """DownstreamServer.from_dict must raise ValueError when http/sse/ws transport has no url."""
    import pytest

    from werktools.hub.contracts import DownstreamServer

    for transport in ("http", "sse", "ws"):
        with pytest.raises(ValueError, match="no url"):
            DownstreamServer.from_dict({"id": "x", "transport": transport})


def test_downstream_server_cwd_round_trips():
    """An optional working directory survives from_dict/to_dict; absent cwd
    stays None and is omitted from the serialized form."""
    from werktools.hub.contracts import DownstreamServer

    with_cwd = DownstreamServer.from_dict({"id": "x", "command": "foo", "cwd": "C:/work/dir"})
    assert with_cwd.cwd == "C:/work/dir"
    assert with_cwd.to_dict()["cwd"] == "C:/work/dir"

    without = DownstreamServer.from_dict({"id": "y", "command": "foo"})
    assert without.cwd is None
    assert "cwd" not in without.to_dict()


def test_downstream_server_cwd_normalizes_blank_and_caps_length():
    """Whitespace-only cwd -> None (never spawn into a blank dir); over-long cwd
    is length-capped like the other string fields."""
    from werktools.hub.contracts import _CWD_MAX, DownstreamServer

    assert DownstreamServer.from_dict({"id": "x", "command": "f", "cwd": "   "}).cwd is None
    assert DownstreamServer.from_dict({"id": "y", "command": "f", "cwd": ""}).cwd is None
    long_cwd = "/" + "d" * 5000
    capped = DownstreamServer.from_dict({"id": "z", "command": "f", "cwd": long_cwd}).cwd
    assert capped is not None and len(capped) == _CWD_MAX
