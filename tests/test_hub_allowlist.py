"""Tier-1 allowlist + install gate (deny-by-default, fail-closed).

The allowlist is metadata that DECIDES a trust label, never auto-runs anything.
A candidate on the curated Tier-1 set is promoted (Official / Security-Scanned);
anything else stays Community-Unverified and carries an [UNVETTED] note. The
registry stays discovery-only — nothing untrusted reaches hub.json silently.
"""

import json
from pathlib import Path

import pytest

from werktools.hub.allowlist import (
    AllowlistEntry,
    Tier1Allowlist,
    default_allowlist,
    is_tier1,
    load_tier1_allowlist,
    tier1_trust_fields,
)
from werktools.hub.contracts import RegistryCandidate
from werktools.hub.discovery import approve_and_write, stage_install
from werktools.hub.ledger import recent_events
from werktools.tools.integration_gate import connector_trust_tier, connectors

ALLOWLIST_SCHEMA = "werk-tier1-allowlist-v1"


def _docker_entry(server_id="mcp-stripe", digest=None):
    return AllowlistEntry(
        server_id=server_id,
        display_name="Stripe",
        source="docker-mcp-catalog",
        promotion_reason="docker-built",
        category="cloud",
        image_ref="mcp/stripe",
        image_digest=digest,
        pinned_commit="abc1234",
        pinned_date="2026-06-19",
    )


def _official_entry(server_id="ghcr-io-github-github-mcp-server"):
    return AllowlistEntry(
        server_id=server_id,
        display_name="GitHub Official",
        source="official-vendor",
        promotion_reason="official",
        category="dev",
        image_ref="ghcr.io/github/github-mcp-server",
        pinned_commit="abc1234",
        pinned_date="2026-06-19",
    )


def _write_allowlist(path: Path, *entries: AllowlistEntry) -> Path:
    payload = Tier1Allowlist(schema=ALLOWLIST_SCHEMA, pinned_at="2026-06-19T00:00:00Z", entries=tuple(entries))
    path.write_text(json.dumps(payload.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    return path


def _oci_candidate(cand_id="mcp-stripe", image="mcp/stripe"):
    return RegistryCandidate.from_dict(
        {"id": cand_id, "name": cand_id, "description": "d", "packages": [{"registryType": "oci", "identifier": image}]}
    )


# ── embedded seed ─────────────────────────────────────────────────────────────


def test_default_allowlist_is_the_curated_seed():
    allowlist = default_allowlist()
    assert allowlist.schema == ALLOWLIST_SCHEMA
    assert len(allowlist.entries) >= 60  # 70 Tier-1 entries seeded from the doc
    # spot-check a docker-built and an official entry are present
    assert is_tier1(allowlist, "mcp-stripe") is not None
    assert is_tier1(allowlist, "ghcr-io-github-github-mcp-server") is not None
    assert is_tier1(allowlist, "totally-made-up-server") is None


def test_is_tier1_is_case_insensitive():
    # RegistryCandidate.from_dict preserves case ("Shopify/dev-mcp" -> "Shopify-dev-mcp")
    # while the seed slug is lowercased; the lookup must still match.
    allowlist = default_allowlist()
    assert is_tier1(allowlist, "Shopify-dev-mcp") is not None
    assert is_tier1(allowlist, "shopify-dev-mcp") is not None
    assert is_tier1(allowlist, "Snowflake-Labs-mcp") is not None
    assert is_tier1(allowlist, "PagerDuty-pagerduty-mcp-server") is not None


def test_load_rejects_malformed_digest(tmp_path):
    bad = AllowlistEntry(
        server_id="x", display_name="X", source="docker-mcp-catalog",
        promotion_reason="docker-built", image_digest="sha256:not-hex",
    )
    # constructing directly is fine; the guard is at load/from_dict boundary
    path = _write_allowlist(tmp_path / "al.json", bad)
    with pytest.raises(ValueError):
        load_tier1_allowlist(path)


def test_seed_has_no_tier2_candidates():
    allowlist = default_allowlist()
    # the two Tier-2 candidates (Twilio alpha, archived Postgres ref) must NOT
    # be promoted into the Tier-1 set
    ids = {e.server_id for e in allowlist.entries}
    assert not any("twilio" in i for i in ids)
    assert not any("postgres" in i and "neon" not in i for i in ids)


def test_seed_entries_are_well_formed():
    for entry in default_allowlist().entries:
        assert entry.server_id
        assert entry.source in ("docker-mcp-catalog", "anthropic-connectors", "official-vendor")
        assert entry.promotion_reason in ("docker-built", "anthropic-connector", "official")


# ── trust mapping ─────────────────────────────────────────────────────────────


def test_tier1_trust_fields_docker_is_security_scanned():
    fields = tier1_trust_fields(_docker_entry())
    assert fields["trust_tier"] == "Security-Scanned"
    assert fields["trust_source"] == "docker-mcp-catalog"
    assert "docker-built" in fields["trust_note"]


def test_tier1_trust_fields_official_is_official():
    fields = tier1_trust_fields(_official_entry())
    assert fields["trust_tier"] == "Official"
    assert fields["trust_source"] == "official-vendor"


# ── load + validate ───────────────────────────────────────────────────────────


def test_load_round_trips(tmp_path):
    path = _write_allowlist(tmp_path / "al.json", _docker_entry(), _official_entry())
    loaded = load_tier1_allowlist(path)
    assert loaded.schema == ALLOWLIST_SCHEMA
    assert is_tier1(loaded, "mcp-stripe") is not None
    assert is_tier1(loaded, "ghcr-io-github-github-mcp-server") is not None


def test_load_bad_schema_raises(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"schema": "wrong", "pinned_at": "x", "entries": []}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_tier1_allowlist(path)


def test_load_non_object_raises(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError):
        load_tier1_allowlist(path)


# ── gate: stage_install ───────────────────────────────────────────────────────


def test_stage_install_without_allowlist_is_unchanged(tmp_path):
    gate = tmp_path / "gate"
    cand = _oci_candidate()
    request = stage_install(gate, cand)
    assert request.status == "pending"
    conn = next(c for c in connectors(gate) if c.connector_id == cand.id)
    # no allowlist passed -> trust stays at the safe default
    assert connector_trust_tier(conn) == "Community-Unverified"


def test_stage_install_tier1_match_promotes_connector_trust(tmp_path):
    gate = tmp_path / "gate"
    allow = _write_allowlist(tmp_path / "al.json", _docker_entry("mcp-stripe"))
    cand = _oci_candidate("mcp-stripe")
    stage_install(gate, cand, allowlist_path=allow)
    conn = next(c for c in connectors(gate) if c.connector_id == "mcp-stripe")
    assert connector_trust_tier(conn) == "Security-Scanned"


def test_stage_install_unlisted_stays_community_unverified(tmp_path):
    gate = tmp_path / "gate"
    allow = _write_allowlist(tmp_path / "al.json", _docker_entry("mcp-stripe"))
    cand = _oci_candidate("some-random-server", "mcp/random")
    stage_install(gate, cand, allowlist_path=allow)
    conn = next(c for c in connectors(gate) if c.connector_id == "some-random-server")
    assert connector_trust_tier(conn) == "Community-Unverified"


# ── gate: approve_and_write ───────────────────────────────────────────────────


def test_approve_tier1_sets_trust_on_server(tmp_path):
    gate = tmp_path / "gate"
    hub_json = tmp_path / "hub.json"
    allow = _write_allowlist(tmp_path / "al.json", _docker_entry("mcp-stripe"))
    cand = _oci_candidate("mcp-stripe")
    request = stage_install(gate, cand, allowlist_path=allow)
    server = approve_and_write(gate, request.request_id, hub_json, allowlist_path=allow)
    assert server.trust_tier == "Security-Scanned"
    assert "[UNVETTED]" not in server.trust_note
    body = json.loads(hub_json.read_text(encoding="utf-8"))
    written = next(s for s in body["servers"] if s["id"] == "mcp-stripe")
    assert written["trust_tier"] == "Security-Scanned"


def test_approve_unlisted_is_unvetted(tmp_path):
    gate = tmp_path / "gate"
    hub_json = tmp_path / "hub.json"
    allow = _write_allowlist(tmp_path / "al.json", _docker_entry("mcp-stripe"))
    cand = _oci_candidate("unknown-server", "mcp/unknown")
    request = stage_install(gate, cand, allowlist_path=allow)
    server = approve_and_write(gate, request.request_id, hub_json, allowlist_path=allow)
    assert server.trust_tier == "Community-Unverified"
    assert "[UNVETTED]" in server.trust_note


def test_approve_oci_tier1_with_digest_pins_image(tmp_path):
    gate = tmp_path / "gate"
    hub_json = tmp_path / "hub.json"
    digest = "sha256:" + "a" * 64
    allow = _write_allowlist(tmp_path / "al.json", _docker_entry("mcp-stripe", digest=digest))
    cand = _oci_candidate("mcp-stripe", "mcp/stripe")
    request = stage_install(gate, cand, allowlist_path=allow)
    server = approve_and_write(gate, request.request_id, hub_json, allowlist_path=allow)
    assert any(a == f"mcp/stripe@{digest}" for a in server.args)
    assert "mcp/stripe" == server.args[-1].split("@")[0]


# ── ledger events ─────────────────────────────────────────────────────────────


def test_invalid_allowlist_emits_error_event_and_fails_closed(tmp_path):
    gate = tmp_path / "gate"
    hub_json = tmp_path / "hub.json"
    ledger = tmp_path / "ledger.jsonl"
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"schema": "WRONG", "entries": []}), encoding="utf-8")
    cand = _oci_candidate("mcp-stripe")
    request = stage_install(gate, cand, allowlist_path=bad, hub_ledger_path=ledger)
    server = approve_and_write(gate, request.request_id, hub_json, allowlist_path=bad, ledger_path=ledger)
    # fail closed: an invalid override does not promote anything
    assert server.trust_tier == "Community-Unverified"
    types = [e["payload"]["type"] for e in recent_events(ledger, limit=20)]
    assert "registry.allowlist.error" in types


def test_tier_downgrade_event_when_allowlist_changes_between_stage_and_approve(tmp_path):
    gate = tmp_path / "gate"
    hub_json = tmp_path / "hub.json"
    ledger = tmp_path / "ledger.jsonl"
    cand = _oci_candidate("mcp-stripe")
    # staged while on the allowlist
    staged_allow = _write_allowlist(tmp_path / "stage.json", _docker_entry("mcp-stripe"))
    request = stage_install(gate, cand, allowlist_path=staged_allow, hub_ledger_path=ledger)
    # approved against an allowlist that no longer lists it
    empty_allow = _write_allowlist(tmp_path / "approve.json")
    server = approve_and_write(gate, request.request_id, hub_json, allowlist_path=empty_allow, ledger_path=ledger)
    assert server.trust_tier == "Community-Unverified"
    types = [e["payload"]["type"] for e in recent_events(ledger, limit=20)]
    assert "registry.allowlist.tier_downgrade" in types


def test_tier_downgrade_event_payload_is_specific(tmp_path):
    gate = tmp_path / "gate"
    hub_json = tmp_path / "hub.json"
    ledger = tmp_path / "ledger.jsonl"
    staged = _write_allowlist(tmp_path / "s.json", _docker_entry("mcp-stripe"))
    request = stage_install(gate, _oci_candidate("mcp-stripe"), allowlist_path=staged, hub_ledger_path=ledger)
    empty = _write_allowlist(tmp_path / "e.json")
    approve_and_write(gate, request.request_id, hub_json, allowlist_path=empty, ledger_path=ledger)
    payloads = [
        e["payload"]
        for e in recent_events(ledger, limit=20)
        if e["payload"].get("type") == "registry.allowlist.tier_downgrade"
    ]
    assert payloads
    assert payloads[0]["server_id"] == "mcp-stripe"
    assert payloads[0]["staged_tier"] == "Security-Scanned"


def test_stage_install_corrupt_allowlist_stays_community_and_logs_error(tmp_path):
    gate = tmp_path / "gate"
    ledger = tmp_path / "ledger.jsonl"
    bad = tmp_path / "bad.json"
    bad.write_text('{"schema": "WRONG", "entries": []}', encoding="utf-8")
    stage_install(gate, _oci_candidate("mcp-stripe"), allowlist_path=bad, hub_ledger_path=ledger)
    conn = next(c for c in connectors(gate) if c.connector_id == "mcp-stripe")
    assert connector_trust_tier(conn) == "Community-Unverified"
    types = [e["payload"]["type"] for e in recent_events(ledger, limit=20)]
    assert "registry.allowlist.error" in types


def test_approve_twice_returns_persisted_trust(tmp_path):
    gate = tmp_path / "gate"
    hub_json = tmp_path / "hub.json"
    allow = _write_allowlist(tmp_path / "al.json", _docker_entry("mcp-stripe"))
    r1 = stage_install(gate, _oci_candidate("mcp-stripe"), allowlist_path=allow)
    s1 = approve_and_write(gate, r1.request_id, hub_json, allowlist_path=allow)
    r2 = stage_install(gate, _oci_candidate("mcp-stripe"), allowlist_path=allow)
    s2 = approve_and_write(gate, r2.request_id, hub_json, allowlist_path=allow)
    # already-connected path must return the persisted record, not a re-derived one
    assert s2.trust_tier == s1.trust_tier == "Security-Scanned"


def test_load_rejects_oversized_file(tmp_path, monkeypatch):
    from werktools.hub import allowlist as al

    monkeypatch.setattr(al, "_MAX_ALLOWLIST_BYTES", 10)
    path = _write_allowlist(tmp_path / "al.json", _docker_entry())
    with pytest.raises(ValueError):
        load_tier1_allowlist(path)


def test_load_rejects_unknown_promotion_reason(tmp_path):
    bad = AllowlistEntry(
        server_id="x", display_name="X", source="docker-mcp-catalog", promotion_reason="bogus-reason",
    )
    path = _write_allowlist(tmp_path / "al.json", bad)
    with pytest.raises(ValueError):
        load_tier1_allowlist(path)


def test_tier1_trust_fields_fails_closed_on_unknown_reason():
    bad = AllowlistEntry(
        server_id="x", display_name="X", source="docker-mcp-catalog", promotion_reason="bogus-reason",
    )
    assert tier1_trust_fields(bad)["trust_tier"] == "Community-Unverified"


def test_pin_digest_variants():
    from werktools.hub.allowlist import pin_digest

    d = "sha256:" + "a" * 64
    assert pin_digest("mcp/stripe", d) == f"mcp/stripe@{d}"
    assert pin_digest("mcp/stripe:v1.2", d) == f"mcp/stripe@{d}"  # tag stripped
    assert pin_digest("mcp/stripe@sha256:" + "0" * 64, d) == f"mcp/stripe@{d}"  # old digest replaced
    assert pin_digest("localhost:5000/repo:tag", d) == f"localhost:5000/repo@{d}"  # port preserved


def test_default_allowlist_all_entries_have_nonempty_server_id():
    """All seed entries must have a non-empty server_id (guards _build_seed guard)."""
    from werktools.hub.allowlist import default_allowlist

    al = default_allowlist()
    empty = [e for e in al.entries if not e.server_id]
    assert not empty, f"seed entries with empty server_id: {empty}"
