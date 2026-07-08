import json

from werktools.cli import main
from werktools.tools.mine import (
    create_card,
    extract_links,
    extract_patterns,
    extract_warnings,
    load_cards,
    query_cards,
    write_card,
)


def test_load_cards_skips_corrupt_and_malformed_files(tmp_path):
    cards_dir = tmp_path / "mine"
    cards_dir.mkdir()
    card = create_card("notes.md", "# Title\n\nbody text", topic="governance")
    write_card(card, cards_dir)
    (cards_dir / "broken.json").write_text("{corrupt", encoding="utf-8")
    (cards_dir / "incomplete.json").write_text(json.dumps({"only": "junk"}), encoding="utf-8")

    cards = load_cards(cards_dir)

    assert len(cards) == 1


def test_extract_links_finds_http_links():
    links = extract_links("Use https://example.com/a and http://example.org/b.")

    assert links == ("https://example.com/a", "http://example.org/b")


def test_extract_patterns_finds_prefixed_lines():
    patterns = extract_patterns("Pattern: local-first cards\nOther line")

    assert patterns == ("local-first cards",)


def test_extract_warnings_finds_risk_warning_and_avoid_lines():
    warnings = extract_warnings("Warning: secrets\nRisk: stale docs\nAvoid: auto browsing")

    assert warnings == ("secrets", "stale docs", "auto browsing")


def test_create_card_contains_required_fields():
    card = create_card(
        "notes.md",
        "# Agent Governance\nPattern: approval gates\nWarning: external claim\nhttps://example.com",
        topic="governance",
        created_at="2026-06-10T00:00:00Z",
    )

    body = card.to_dict()
    assert set(body) == {
        "id",
        "title",
        "topic",
        "summary",
        "source",
        "source_status",
        "links",
        "patterns",
        "warnings",
        "tags",
        "created_at",
    }
    assert body["title"] == "Agent Governance"
    assert body["source_status"] == "provided_unverified"
    assert body["links"] == ["https://example.com"]


def test_write_card_and_load_cards_round_trip(tmp_path):
    card = create_card("notes.md", "Pattern: local-first", created_at="2026-06-10T00:00:00Z")

    path = write_card(card, tmp_path)

    assert path.exists()
    loaded = load_cards(tmp_path)
    assert len(loaded) == 1
    assert loaded[0].id == card.id


def test_load_cards_ignores_index_json(tmp_path):
    card = create_card("notes.md", "Pattern: local-first", created_at="2026-06-10T00:00:00Z")
    write_card(card, tmp_path)
    (tmp_path / "index.json").write_text(json.dumps([]), encoding="utf-8")

    assert len(load_cards(tmp_path)) == 1


def test_query_cards_returns_matching_cards():
    cards = [
        create_card("a.md", "# Agent Governance\n", topic="governance", created_at="2026-06-10T00:00:00Z"),
        create_card("b.md", "# Design Notes\n", topic="design", created_at="2026-06-10T00:00:00Z"),
    ]

    matches = query_cards(cards, "governance")

    assert [card.title for card in matches] == ["Agent Governance"]


def test_mine_extract_cli_writes_card(tmp_path, capsys):
    source = tmp_path / "notes.md"
    source.write_text("# Agent Governance\nPattern: approval gates\n", encoding="utf-8")
    out_dir = tmp_path / "mine"

    code = main(["mine", "extract", str(source), "--out", str(out_dir)])

    out = capsys.readouterr().out
    assert code == 0
    assert "mine_" in out
    assert len(list(out_dir.glob("mine_*.json"))) == 1


def test_mine_index_cli_writes_index(tmp_path):
    card = create_card("notes.md", "# Agent Governance", created_at="2026-06-10T00:00:00Z")
    write_card(card, tmp_path)

    code = main(["mine", "index", str(tmp_path)])

    assert code == 0
    assert (tmp_path / "index.json").exists()


def test_mine_query_cli_prints_matching_title(tmp_path, capsys):
    card = create_card("notes.md", "# Agent Governance", topic="governance", created_at="2026-06-10T00:00:00Z")
    write_card(card, tmp_path)

    code = main(["mine", "query", "governance", "--dir", str(tmp_path)])

    out = capsys.readouterr().out
    assert code == 0
    assert "Agent Governance" in out


def test_mine_report_cli_writes_report(tmp_path):
    card = create_card("notes.md", "# Agent Governance", topic="governance", created_at="2026-06-10T00:00:00Z")
    write_card(card, tmp_path)
    out_path = tmp_path / "report.md"

    code = main(["mine", "report", "--dir", str(tmp_path), "--out", str(out_path)])

    assert code == 0
    assert "WERK Mine Report" in out_path.read_text(encoding="utf-8")
