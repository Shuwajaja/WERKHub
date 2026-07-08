"""Trust taxonomy (metadata only): Official / Security-Scanned / Community-Unverified.

This is metadata that travels with cards/servers/connectors; it does NOT
change any enforcement decision in P1. Default is the lowest tier
(Community-Unverified) and any unknown value fails closed to that default.
"""

import pytest

from werktools.catalog import (
    DEFAULT_TRUST_TIER,
    TRUST_TIERS,
    CatalogCard,
    load_cards,
    normalize_trust_tier,
)
from werktools.hub.contracts import DownstreamServer, ToolCard
from werktools.tools.integration_gate import add_connector, connector_trust_tier
from werktools.tools.skills_discover import parse_skill_md


def test_trust_vocabulary_and_default():
    assert TRUST_TIERS == ("Official", "Security-Scanned", "Community-Unverified")
    assert DEFAULT_TRUST_TIER == "Community-Unverified"


@pytest.mark.parametrize(
    "value,expected",
    [
        ("Official", "Official"),
        ("Security-Scanned", "Security-Scanned"),
        ("Community-Unverified", "Community-Unverified"),
        ("bogus", "Community-Unverified"),
        (None, "Community-Unverified"),
        ("", "Community-Unverified"),
    ],
)
def test_normalize_trust_tier_fails_closed(value, expected):
    assert normalize_trust_tier(value) == expected


# ── ToolCard ──────────────────────────────────────────────────────────────────


def test_tool_card_defaults_to_community_unverified():
    card = ToolCard.from_dict({"id": "x", "name": "x"})
    assert card.trust_tier == "Community-Unverified"
    assert card.to_dict()["trust_tier"] == "Community-Unverified"
    assert card.to_dict()["trust_source"] == ""


def test_tool_card_trust_round_trips():
    card = ToolCard.from_dict(
        {
            "id": "x",
            "name": "x",
            "trust_tier": "Official",
            "trust_source": "docker-mcp-catalog",
            "trust_note": "docker-built",
        }
    )
    assert card.trust_tier == "Official"
    body = card.to_dict()
    assert body["trust_source"] == "docker-mcp-catalog"
    assert body["trust_note"] == "docker-built"


def test_tool_card_unknown_trust_falls_closed():
    assert ToolCard.from_dict({"id": "x", "trust_tier": "nonsense"}).trust_tier == "Community-Unverified"


def test_trust_fields_are_truncated_across_all_types():
    src = "a" * 100  # > 64
    note = "b" * 300  # > 200
    tc = ToolCard.from_dict({"id": "x", "trust_source": src, "trust_note": note})
    assert len(tc.trust_source) == 64 and len(tc.trust_note) == 200
    ds = DownstreamServer.from_dict({"id": "s", "trust_source": src, "trust_note": note})
    assert len(ds.trust_source) == 64 and len(ds.trust_note) == 200
    cc = CatalogCard.from_dict({"card_id": "c", "trust_source": src, "trust_note": note})
    assert len(cc.trust_source) == 64 and len(cc.trust_note) == 200


# ── DownstreamServer ──────────────────────────────────────────────────────────


def test_downstream_defaults_to_community_unverified():
    server = DownstreamServer.from_dict({"id": "s"})
    assert server.trust_tier == "Community-Unverified"
    body = server.to_dict()
    assert body["trust_tier"] == "Community-Unverified"
    assert body["trust_source"] == ""
    assert body["trust_note"] == ""


def test_downstream_trust_round_trips_and_fails_closed():
    server = DownstreamServer.from_dict(
        {"id": "s", "trust_tier": "Security-Scanned", "trust_source": "anthropic-connectors", "trust_note": "n"}
    )
    assert server.to_dict()["trust_tier"] == "Security-Scanned"
    assert server.to_dict()["trust_source"] == "anthropic-connectors"
    assert DownstreamServer.from_dict({"id": "s", "trust_tier": "weird"}).trust_tier == "Community-Unverified"


# ── CatalogCard ───────────────────────────────────────────────────────────────


def test_catalog_card_default_and_round_trip():
    assert CatalogCard.from_dict({"card_id": "c"}).trust_tier == "Community-Unverified"
    card = CatalogCard.from_dict({"card_id": "c", "trust_tier": "Official", "trust_source": "x"})
    body = card.to_dict()
    assert body["trust_tier"] == "Official"
    assert body["trust_source"] == "x"


def test_catalog_markdown_card_reads_trust_header(tmp_path):
    md = tmp_path / "thing.md"
    md.write_text("# Thing\nTrust_tier: Official\nTags: a,b\n\nbody text\n", encoding="utf-8")
    cards = load_cards(tmp_path, "skill")
    assert cards[0].trust_tier == "Official"


# ── SKILL.md frontmatter ──────────────────────────────────────────────────────


def test_skill_md_frontmatter_trust_is_parsed(tmp_path):
    p = tmp_path / "s.md"
    p.write_text(
        "---\n"
        "name: My Skill\n"
        "description: does useful things\n"
        "trust_tier: Security-Scanned\n"
        "trust_source: docker-mcp-catalog\n"
        "trust_note: cosign+sbom\n"
        "---\n"
        "body\n",
        encoding="utf-8",
    )
    card = parse_skill_md(p)
    assert card.trust_tier == "Security-Scanned"
    assert card.trust_source == "docker-mcp-catalog"
    assert card.trust_note == "cosign+sbom"


def test_skill_md_without_trust_defaults_to_community_unverified(tmp_path):
    p = tmp_path / "s.md"
    p.write_text("---\nname: Plain\ndescription: no trust field\n---\nbody\n", encoding="utf-8")
    assert parse_skill_md(p).trust_tier == "Community-Unverified"


# ── Integration gate connector ────────────────────────────────────────────────


def test_connector_trust_tier_default_and_from_metadata(tmp_path):
    scopes = ({"name": "read", "access": "read", "description": ""},)
    plain = add_connector(tmp_path, "conn", scopes=scopes)
    assert connector_trust_tier(plain) == "Community-Unverified"

    vetted = add_connector(tmp_path, "conn2", scopes=scopes, metadata={"trust_tier": "Official"})
    assert connector_trust_tier(vetted) == "Official"

    junk = add_connector(tmp_path, "conn3", scopes=scopes, metadata={"trust_tier": "lol"})
    assert connector_trust_tier(junk) == "Community-Unverified"
