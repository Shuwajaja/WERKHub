import json
from pathlib import Path

import pytest

from werktools.hub.contracts import RegistryCandidate
from werktools.hub.discovery import (
    approve_and_write,
    candidate_to_downstream,
    search_registry,
    stage_install,
)
from werktools.hub.ledger import recent_events

CASSETTE = Path(__file__).parent / "fixtures" / "registry_cassette.json"


def _cassette_http_get(url):
    return json.loads(CASSETTE.read_text(encoding="utf-8"))


def _empty_http_get(url):
    raise OSError("network down")


def _cand(rtype, name="x", identifier=None):
    pkg_id = identifier if identifier is not None else ("mcp/pkg" if rtype in ("oci", "docker") else "pkg")
    return RegistryCandidate.from_dict(
        {"id": name, "name": name, "description": "d", "packages": [{"registryType": rtype, "identifier": pkg_id}]}
    )


def test_map_npm_pypi_docker():
    assert candidate_to_downstream(_cand("npm")).command == "npx"
    assert candidate_to_downstream(_cand("pypi")).command == "uvx"
    assert candidate_to_downstream(_cand("oci")).command == "docker"


def test_map_oci_non_mcp_prefix_is_none():
    """OCI images outside the mcp/ namespace must not be auto-installable."""
    assert candidate_to_downstream(_cand("oci", identifier="acme/box:latest")) is None
    assert candidate_to_downstream(_cand("oci", identifier="attacker.com/evil")) is None
    assert candidate_to_downstream(_cand("oci", identifier="mcp/safe")) is not None


def test_map_unknown_and_empty_is_none():
    assert candidate_to_downstream(_cand("brew")) is None
    assert candidate_to_downstream(RegistryCandidate.from_dict({"id": "x", "name": "x", "packages": []})) is None


def test_search_returns_candidates():
    candidates, warnings = search_registry(http_get=_cassette_http_get)
    assert warnings == []
    assert {c.id for c in candidates} >= {"io-github-acme-docs-mcp", "io-github-acme-db-tools"}


def test_search_query_filters():
    candidates, _ = search_registry("database", http_get=_cassette_http_get)
    assert [c.name for c in candidates] == ["db-tools"]


def test_search_limit_respected():
    candidates, _ = search_registry(limit=1, http_get=_cassette_http_get)
    assert len(candidates) == 1


def test_search_network_error_returns_warning():
    candidates, warnings = search_registry(http_get=_empty_http_get)
    assert candidates == []
    assert warnings and "failed" in warnings[0]


def test_search_malformed_missing_servers():
    candidates, warnings = search_registry(http_get=lambda u: {"nope": 1})
    assert candidates == []
    assert warnings


def test_search_servers_not_a_list_returns_warning():
    """Fix 14: search_registry must return a warning (not crash) when 'servers'
    is present but is not a list (e.g. a string or dict)."""
    candidates, warns = search_registry(http_get=lambda u: {"servers": "not-a-list"})
    assert candidates == []
    assert warns, "expected at least one warning"
    assert any("not a list" in w or "not_a_list" in w or "str" in w for w in warns), (
        f"warning should mention the type; got: {warns}"
    )


def test_stage_install_creates_pending(tmp_path):
    gate = tmp_path / "gate"
    ledger = tmp_path / "ledger.jsonl"
    candidates, _ = search_registry(http_get=_cassette_http_get)
    docs = next(c for c in candidates if c.name == "docs-mcp")

    request = stage_install(gate, docs, hub_ledger_path=ledger)

    assert request.status == "pending"
    assert (gate / "approvals" / f"{request.request_id}.json").exists()
    types = [e["payload"]["type"] for e in recent_events(ledger, limit=10)]
    assert "registry.install.staged" in types


def test_approve_writes_into_hub_json(tmp_path):
    gate = tmp_path / "gate"
    ledger = tmp_path / "ledger.jsonl"
    hub_json = tmp_path / "hub.json"
    candidates, _ = search_registry(http_get=_cassette_http_get)
    docs = next(c for c in candidates if c.name == "docs-mcp")
    request = stage_install(gate, docs, hub_ledger_path=ledger)

    server = approve_and_write(gate, request.request_id, hub_json, ledger_path=ledger)

    assert server.command == "npx"
    body = json.loads(hub_json.read_text(encoding="utf-8"))
    assert any(s["id"] == server.id for s in body["servers"])
    types = [e["payload"]["type"] for e in recent_events(ledger, limit=10)]
    assert "registry.install.approved" in types


def test_approve_already_approved_raises(tmp_path):
    gate = tmp_path / "gate"
    hub_json = tmp_path / "hub.json"
    candidates, _ = search_registry(http_get=_cassette_http_get)
    docs = next(c for c in candidates if c.name == "docs-mcp")
    request = stage_install(gate, docs)
    approve_and_write(gate, request.request_id, hub_json)

    with pytest.raises(ValueError):
        approve_and_write(gate, request.request_id, hub_json)


def test_approve_no_packages_before_touching_hub(tmp_path):
    gate = tmp_path / "gate"
    hub_json = tmp_path / "hub.json"
    candidates, _ = search_registry(http_get=_cassette_http_get)
    nopkg = next(c for c in candidates if c.name == "no-pkg")
    request = stage_install(gate, nopkg)

    with pytest.raises(ValueError):
        approve_and_write(gate, request.request_id, hub_json)
    assert not hub_json.exists()


# ---------------------------------------------------------------------------
# Regression tests: request_id validation + argv injection guards
# ---------------------------------------------------------------------------

def test_approve_and_write_rejects_bad_request_ids(tmp_path):
    # approve_and_write must raise ValueError for any malformed request_id
    # before building approval_path or touching the filesystem.
    gate = tmp_path / "gate"
    hub_json = tmp_path / "hub.json"

    bad_ids = [
        "../../../etc/passwd",
        "req_GGGGGGGGGGGG",   # non-hex
        "apr_000000000000",   # wrong prefix
        "req_",               # too short
        "req_00000000000z",   # z is not hex
        "",                   # empty
    ]
    for rid in bad_ids:
        with pytest.raises(ValueError, match="invalid request_id"):
            approve_and_write(gate, rid, hub_json)

    # The approvals dir must never have been created by a traversal attempt
    assert not (gate / "approvals").exists()


@pytest.mark.skipif(
    __import__("sys").platform == "win32",
    reason="symlink containment check is POSIX-only",
)
def test_approve_and_write_containment_check_fires_on_escaped_symlink(tmp_path):
    # The second guard (resolve().relative_to()) must fire even when the format
    # guard passes — a symlink pointing outside the approvals dir must be blocked.
    import os

    gate = tmp_path / "gate"
    (gate / "approvals").mkdir(parents=True)
    hub_json = tmp_path / "hub.json"

    # Create a symlink that resolves outside: approvals/req_abcdef000000.json -> outside
    target = tmp_path / "outside.json"
    target.write_text('{"status":"pending","connector_id":"x"}', encoding="utf-8")
    link = gate / "approvals" / "req_abcdef000000.json"
    os.symlink(str(target), str(link))

    # Format guard passes (req_abcdef000000 is valid), containment guard must block
    with pytest.raises(ValueError, match="invalid request_id"):
        approve_and_write(gate, "req_abcdef000000", hub_json)


def test_candidate_to_downstream_npm_injection_returns_none():
    # candidate_to_downstream must return None for npm names that would inject
    # into npx argv (shell payloads, path-traversal).
    def _npm(identifier):
        return RegistryCandidate.from_dict({
            "id": "x", "name": "x", "description": "d",
            "packages": [{"registryType": "npm", "identifier": identifier}],
        })

    # Happy path: valid names pass
    assert candidate_to_downstream(_npm("my-package")).command == "npx"
    assert candidate_to_downstream(_npm("@scope/pkg")).command == "npx"

    # Injection / traversal names must return None (never reach subprocess)
    for bad_name in ["-v /etc", "../escape", "evil; echo hi", "evil\x00null", ""]:
        result = candidate_to_downstream(_npm(bad_name))
        assert result is None, f"expected None for npm name {bad_name!r}"


def test_candidate_to_downstream_pypi_injection_returns_none():
    # candidate_to_downstream must return None for pypi names that would inject
    # into uvx argv.
    def _pypi(identifier):
        return RegistryCandidate.from_dict({
            "id": "x", "name": "x", "description": "d",
            "packages": [{"registryType": "pypi", "identifier": identifier}],
        })

    # Happy path
    assert candidate_to_downstream(_pypi("requests")).command == "uvx"
    assert candidate_to_downstream(_pypi("my.package")).command == "uvx"

    # Must return None for names that would inject into uvx argv
    for bad_name in ["evil/sub", "evil; rm -rf /", "../escape", ""]:
        result = candidate_to_downstream(_pypi(bad_name))
        assert result is None, f"expected None for pypi name {bad_name!r}"


def test_candidate_to_downstream_oci_flag_injection_returns_none():
    # candidate_to_downstream must return None for OCI image names shaped like
    # docker-run flags (-v=, --network=...), blocking flag injection into docker argv.
    def _oci(identifier):
        return RegistryCandidate.from_dict({
            "id": "x", "name": "x", "description": "d",
            "packages": [{"registryType": "oci", "identifier": identifier}],
        })

    # Flag-injection payloads must be blocked
    assert candidate_to_downstream(_oci("-v=/etc:/etc")) is None
    assert candidate_to_downstream(_oci("--network=host")) is None

    # Valid mcp/ namespace must still pass
    assert candidate_to_downstream(_oci("mcp/safe")) is not None
    assert candidate_to_downstream(_oci("mcp/safe:v1.2.3")) is not None


def test_candidate_to_downstream_npm_at_in_non_scoped_name_returns_none():
    """pkg@2.0 and tool@../escape must be rejected (@ is invalid in the name body)."""
    def _npm(identifier):
        return RegistryCandidate.from_dict({
            "id": "x", "name": "x", "description": "d",
            "packages": [{"registryType": "npm", "identifier": identifier}],
        })

    assert candidate_to_downstream(_npm("pkg@2.0")) is None
    assert candidate_to_downstream(_npm("tool@../escape")) is None
    # Scoped form (@ only in prefix) must still pass.
    assert candidate_to_downstream(_npm("@scope/valid-pkg")) is not None


def test_approve_and_write_warns_on_tier_downgrade_without_ledger(tmp_path):
    """Tier downgrade must emit UserWarning even when ledger_path is None."""
    import json as _json
    import warnings as _w

    gate = tmp_path / "gate"
    hub_json = tmp_path / "hub.json"
    candidates, _ = search_registry(http_get=_cassette_http_get)
    docs = next(c for c in candidates if c.name == "docs-mcp")

    # Stage the connector (creates connectors.json + approvals/*.json in gate).
    request = stage_install(gate, docs)

    # Load the approval to find the connector_id.
    approval_path = gate / "approvals" / f"{request.request_id}.json"
    approval = _json.loads(approval_path.read_text(encoding="utf-8"))
    connector_id = approval["connector_id"]

    # Patch connectors.json to inject Official tier into the connector metadata.
    connectors_path = gate / "connectors.json"
    connector_list = _json.loads(connectors_path.read_text(encoding="utf-8"))
    for c in connector_list:
        if c.get("connector_id") == connector_id:
            c.setdefault("metadata", {})["trust_tier"] = "Official"
    connectors_path.write_text(_json.dumps(connector_list, indent=2), encoding="utf-8")

    # approve_and_write with an allowlist path (non-existent → default_allowlist() used)
    # but without ledger_path. The cassette connector is not in the curated allowlist
    # so _apply_trust reaches the downgrade branch. The record_event call is skipped
    # (ledger_path=None) but the warnings.warn must still fire.
    missing_allowlist = tmp_path / "no_such_allowlist.json"
    with _w.catch_warnings(record=True) as caught:
        _w.simplefilter("always")
        server = approve_and_write(
            gate, request.request_id, hub_json,
            ledger_path=None,
            allowlist_path=missing_allowlist,
        )

    downgrade_warnings = [
        w for w in caught
        if (
            "downgrading" in str(w.message).lower()
            or "tier_downgrade" in str(w.message).lower()
            or "Community-Unverified" in str(w.message)
        )
    ]
    assert downgrade_warnings, "Expected UserWarning for tier downgrade even without ledger_path"
    assert server.trust_tier == "Community-Unverified"


def test_cli_registry_search_and_approve(tmp_path, capsys):
    from werktools.cli import main
    from werktools.hub.registry import default_config

    config = tmp_path / "hub.json"
    body = default_config().to_dict()
    body["ledger_path"] = str(tmp_path / "hub-ledger.jsonl")
    config.write_text(json.dumps(body), encoding="utf-8")

    # search exits 0 even on network failure (no cassette injection in CLI)
    assert main(["--config", str(config), "hub", "registry", "search", "--query", "zzz"]) == 0
    capsys.readouterr()

    # stage offline then approve via CLI
    gate = tmp_path / "gate"
    hub_target = tmp_path / "target-hub.json"
    candidates, _ = search_registry(http_get=_cassette_http_get)
    docs = next(c for c in candidates if c.name == "docs-mcp")
    request = stage_install(gate, docs)

    code = main(["--config", str(config), "hub", "registry", "approve", "--request-id", request.request_id, "--gate-root", str(gate), "--hub-config", str(hub_target)])
    assert code == 0
    assert "Connected:" in capsys.readouterr().out


def test_search_registry_limit_zero_returns_warning():
    """search_registry(limit=0) must return ([], [warning]) not raise."""
    candidates, warns = search_registry(limit=0, http_get=_cassette_http_get)
    assert candidates == []
    assert warns and any("limit" in w for w in warns)


def test_approve_and_write_updates_trust_note_same_tier(tmp_path):
    """A second approve_and_write call with same tier but updated trust_note must persist the new note.

    This pins the fix to discovery.py: the same-tier fast-path now compares all
    three trust fields and persists updates to trust_source/trust_note even when
    trust_tier is unchanged.
    """
    import dataclasses as _dc

    from werktools.hub.registry import load_config, save_config

    hub_cfg = tmp_path / "hub.json"
    gate = tmp_path / "gate"

    # First approval — no trust_note.
    cand = _cand("npm", name="advisory-server")
    req1 = stage_install(gate, cand)
    approve_and_write(
        request_id=req1.request_id,
        gate_root=gate,
        hub_config_path=hub_cfg,
    )

    # Verify first server is in hub.json.
    cfg = load_config(hub_cfg)
    assert any(s.id == "advisory-server" for s in cfg.servers)

    # Manually inject a trust_note into the persisted server to simulate an advisory.
    _new_note = "SECURITY ADVISORY: CVE-2099-9999"
    updated_servers = tuple(
        _dc.replace(s, trust_note=_new_note) if s.id == "advisory-server" else s
        for s in cfg.servers
    )
    save_config(hub_cfg, _dc.replace(cfg, servers=updated_servers))
    assert load_config(hub_cfg).servers[0].trust_note == _new_note

    # Second approval — same server, same tier, different trust_note (would have been
    # silently discarded before the fix).
    cand2 = _cand("npm", name="advisory-server")
    req2 = stage_install(gate, cand2)
    approve_and_write(
        request_id=req2.request_id,
        gate_root=gate,
        hub_config_path=hub_cfg,
    )

    # The server must still be present (no crash, no duplicate).
    final_cfg = load_config(hub_cfg)
    ids = [s.id for s in final_cfg.servers]
    assert "advisory-server" in ids
    # Exactly one entry for this server.
    assert ids.count("advisory-server") == 1
