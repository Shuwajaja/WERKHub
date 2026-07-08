import json
from pathlib import Path

import pytest

from werktools.tools.skills_discover import (
    DiscoverResult,
    discover_skills,
    federate_marketplaces,
    fetch_marketplace,
    load_skill_md_files,
    parse_skill_md,
    parse_yaml_frontmatter,
    query_skillkit,
)

FIX = Path(__file__).parent / "fixtures"


def test_frontmatter_minimal():
    front, body = parse_yaml_frontmatter("---\nname: X\ndescription: d\n---\nbody")
    assert front["name"] == "X"
    assert body == "body"


def test_frontmatter_none_when_no_fence():
    front, body = parse_yaml_frontmatter("# heading\ntext")
    assert front == {}


def test_frontmatter_unterminated_raises():
    with pytest.raises(ValueError):
        parse_yaml_frontmatter("---\nname: X\nno closing fence")


def test_parse_skill_md_frontmatter():
    card = parse_skill_md(FIX / "skill_yaml_frontmatter.md")
    assert card.card_id == "review-policy"
    assert card.risk == "read"
    assert "supplements" in card.summary


def test_parse_skill_md_legacy_fallback():
    card = parse_skill_md(FIX / "skill_yaml_no_frontmatter.md")
    assert card.title == "Legacy Skill"
    assert "docs" in card.tags


def test_parse_skill_md_missing_required_raises(tmp_path):
    bad = tmp_path / "bad.md"
    bad.write_text("---\nname: only name\n---\nbody", encoding="utf-8")
    with pytest.raises(ValueError):
        parse_skill_md(bad)


def test_load_skill_md_files_mixed(tmp_path):
    (tmp_path / "good.md").write_text("---\nname: Good\ndescription: g\n---\nb", encoding="utf-8")
    (tmp_path / "bad.md").write_text("---\nname: Bad\n---\nb", encoding="utf-8")
    cards, errors = load_skill_md_files(tmp_path)
    assert [c.card_id for c in cards] == ["good"]
    assert errors and errors[0][0] == "bad.md"


def _market_fetch(url, timeout):
    return json.loads((FIX / "marketplace_anthropic.json").read_text(encoding="utf-8"))


def test_fetch_marketplace_skips_missing_required():
    result = fetch_marketplace("anthropic", "u", _fetch=_market_fetch)
    assert result.ok is True
    ids = {c.card_id for c in result.cards}
    assert ids == {"review-policy", "doc-writer"}  # empty-name entry skipped


def test_fetch_marketplace_network_failure_no_raise():
    def boom(url, timeout):
        raise OSError("refused")

    result = fetch_marketplace("x", "u", _fetch=boom)
    assert result.ok is False
    assert "refused" in result.error


def test_federate_multiple_one_fail(monkeypatch):
    def selective(url, timeout):
        if "anthropic" in url:
            return {"skills": [{"name": "A", "description": "a"}]}
        raise OSError("down")

    results = federate_marketplaces(_fetch=selective)
    assert any(r.ok for r in results)
    assert any(not r.ok for r in results)


def test_query_skillkit_success_and_refused():
    ok = query_skillkit("x", _fetch=lambda u, t: [{"name": "K", "description": "k"}])
    assert ok.ok is True and ok.cards[0].card_id == "k"
    bad = query_skillkit("x", _fetch=lambda u, t: (_ for _ in ()).throw(OSError("refused")))
    assert bad.ok is False


def test_discover_local_only_no_network(tmp_path):
    (tmp_path / "s.md").write_text("---\nname: S\ndescription: d\n---\nb", encoding="utf-8")
    called = {"n": 0}

    def spy(url, timeout):
        called["n"] += 1
        return {}

    result = discover_skills(tmp_path, fetch_remote=False, _fetch=spy)
    assert isinstance(result, DiscoverResult)
    assert called["n"] == 0
    assert [c.card_id for c in result.cards] == ["s"]


def test_discover_dedup_local_beats_marketplace(tmp_path):
    # local "Review Policy" collides with the marketplace fixture entry
    (tmp_path / "review.md").write_text("---\nname: Review Policy\ndescription: local wins\n---\n", encoding="utf-8")

    result = discover_skills(tmp_path, marketplace_urls={"anthropic": "u"}, fetch_remote=True, _fetch=_market_fetch)
    winner = next(c for c in result.cards if c.card_id == "review-policy")
    assert winner.summary == "local wins"  # local source beat the marketplace
    assert result.total_deduped < result.total_fetched


def test_cli_discover_no_remote(tmp_path, capsys):
    from werktools.cli import main

    (tmp_path / "s.md").write_text("---\nname: S\ndescription: d\nprofiles: ['*']\n---\n", encoding="utf-8")
    code = main(["skills", "discover", "--dir", str(tmp_path), "--no-remote"])
    assert code == 0
    assert "s" in capsys.readouterr().out
