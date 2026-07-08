import json

from werktools.catalog import (
    CatalogCard,
    export_cards,
    load_cards,
    match_cards,
    visible_cards,
)


def _card(card_id="c1", profiles=("*",), tags=("review",), title="Review Helper", summary="Reviews policy modules."):
    return CatalogCard(
        card_id=card_id,
        kind="skill",
        title=title,
        summary=summary,
        tags=tuple(tags),
        profiles=tuple(profiles),
        source="memory",
        risk="read",
        requires_approval=False,
        metadata={},
        created_at="2026-06-10T00:00:00Z",
    )


def test_card_round_trip():
    card = _card()

    assert CatalogCard.from_dict(card.to_dict()) == card


def test_load_cards_reads_json_and_markdown(tmp_path):
    (tmp_path / "alpha.json").write_text(json.dumps(_card(card_id="alpha").to_dict()), encoding="utf-8")
    (tmp_path / "beta.md").write_text(
        "# Beta Skill\n\nTags: docs, style\nProfiles: claude-reviewer\nRisk: read\n\nWrites better docs.\n",
        encoding="utf-8",
    )

    cards = load_cards(tmp_path, kind="skill")

    by_id = {card.card_id: card for card in cards}
    assert set(by_id) == {"alpha", "beta"}
    assert by_id["beta"].title == "Beta Skill"
    assert by_id["beta"].tags == ("docs", "style")
    assert by_id["beta"].profiles == ("claude-reviewer",)
    assert by_id["beta"].summary == "Writes better docs."
    assert by_id["beta"].source.endswith("beta.md")


def test_load_cards_skips_corrupt_files(tmp_path):
    (tmp_path / "ok.json").write_text(json.dumps(_card(card_id="ok").to_dict()), encoding="utf-8")
    (tmp_path / "broken.json").write_text("{corrupt", encoding="utf-8")
    (tmp_path / "junk.json").write_text(json.dumps({"nope": True}), encoding="utf-8")

    cards = load_cards(tmp_path, kind="skill")

    assert [card.card_id for card in cards] == ["ok"]


def test_markdown_second_kv_block_is_body_not_headers(tmp_path):
    (tmp_path / "tricky.md").write_text(
        "# Tricky\n\nProfiles: admin\nRisk: read\n\nProfiles: *\nRisk: destructive\n\nBody.\n",
        encoding="utf-8",
    )

    card = load_cards(tmp_path, kind="skill")[0]

    assert card.profiles == ("admin",)
    assert card.risk == "read"


def test_markdown_missing_risk_defaults_to_unknown(tmp_path):
    (tmp_path / "norisk.md").write_text("# No Risk Header\n\nJust a body.\n", encoding="utf-8")

    card = load_cards(tmp_path, kind="skill")[0]

    assert card.risk == "unknown"


def test_explicit_empty_profiles_means_visible_to_nobody():
    card = CatalogCard.from_dict({"card_id": "locked", "profiles": []})

    assert card.profiles == ()
    assert visible_cards([card], "default") == []
    assert visible_cards([card], "admin") == []


def test_visible_cards_filters_by_profile():
    cards = [
        _card(card_id="public", profiles=("*",)),
        _card(card_id="private", profiles=("admin",)),
    ]

    assert [card.card_id for card in visible_cards(cards, "default")] == ["public"]
    assert {card.card_id for card in visible_cards(cards, "admin")} == {"public", "private"}


def test_match_cards_ranks_by_token_overlap():
    cards = [
        _card(card_id="review", title="Review Policy", summary="Adversarial review of policy modules.", tags=("review",)),
        _card(card_id="docs", title="Docs Writer", summary="Writes documentation.", tags=("docs",)),
    ]

    matched = match_cards(cards, "review the policy module", limit=2)

    assert matched[0].card_id == "review"


def test_match_cards_is_deterministic_for_ties():
    cards = [
        _card(card_id="b", title="Same Thing"),
        _card(card_id="a", title="Same Thing"),
    ]

    matched = match_cards(cards, "same thing", limit=2)

    assert [card.card_id for card in matched] == ["a", "b"]


def test_load_cards_corrupt_json_emits_warning(tmp_path):
    # load_cards must emit a UserWarning that includes the bad filename when a
    # JSON card file contains corrupt JSON (honest-degrade contract).
    (tmp_path / "ok.json").write_text(
        json.dumps(
            {
                "card_id": "ok",
                "kind": "skill",
                "title": "T",
                "summary": "s",
                "tags": [],
                "profiles": ["*"],
                "source": "",
                "risk": "read",
                "requires_approval": False,
                "metadata": {},
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "bad.json").write_text("{corrupt", encoding="utf-8")

    import warnings as _warnings
    with _warnings.catch_warnings(record=True) as caught:
        _warnings.simplefilter("always")
        cards = load_cards(tmp_path, kind="skill")

    warning_messages = [str(w.message) for w in caught]
    assert any("bad.json" in m for m in warning_messages), (
        f"expected a warning mentioning bad.json; got: {warning_messages}"
    )
    assert [c.card_id for c in cards] == ["ok"]


def test_export_cards_writes_deterministic_json(tmp_path):
    out = tmp_path / "export" / "cards.json"
    cards = [_card(card_id="z"), _card(card_id="a")]

    exported = export_cards(cards, out)

    body = json.loads(out.read_text(encoding="utf-8"))
    assert [item["card_id"] for item in body] == ["a", "z"]
    assert exported == body
