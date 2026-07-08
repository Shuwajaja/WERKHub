import json

from werktools.cli import main
from werktools.hub.capabilities import (
    capability_cards,
    classify_capability,
    export_capabilities,
    show_capability,
)
from werktools.hub.registry import default_config


def test_capability_cards_returns_plain_dicts():
    cards = capability_cards(default_config())

    assert isinstance(cards, list)
    assert all(isinstance(card, dict) for card in cards)
    assert any(card["id"] == "docs.search" for card in cards)


def test_capability_cards_keep_unknown_risk_visible():
    cards = capability_cards(default_config())

    mystery = next(card for card in cards if card["id"] == "mystery.tool")

    assert mystery["risk"] == "unknown"


def test_show_capability_returns_one_card():
    card = show_capability(default_config(), "docs.search")

    assert card["id"] == "docs.search"
    assert card["risk"] == "read"


def test_classify_capability_uses_offline_classifier():
    result = classify_capability(
        {
            "name": "run_shell",
            "description": "Execute a shell command",
            "inputSchema": {},
        }
    )

    assert result["risk"] == "critical"
    assert "shell/exec" in result["signals"]


def test_export_capabilities_writes_deterministic_json(tmp_path):
    path = tmp_path / "capabilities.json"

    cards = export_capabilities(default_config(), path)

    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded == cards
    assert loaded[0]["id"] == "docs.search"


def test_capability_list_cli(tmp_path, capsys):
    config = tmp_path / "hub.json"
    main(["--config", str(config), "hub", "init"])

    code = main(["--config", str(config), "capability", "list"])

    out = capsys.readouterr().out
    assert code == 0
    assert "docs.search" in out
    assert "mystery.tool" in out


def test_capability_show_cli(tmp_path, capsys):
    config = tmp_path / "hub.json"
    main(["--config", str(config), "hub", "init"])

    code = main(["--config", str(config), "capability", "show", "docs.search"])

    out = capsys.readouterr().out
    assert code == 0
    assert '"id": "docs.search"' in out


def test_capability_classify_cli(tmp_path, capsys):
    config = tmp_path / "hub.json"
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"name": "run_shell", "description": "Execute shell command"}),
        encoding="utf-8",
    )

    code = main(["--config", str(config), "capability", "classify", str(manifest)])

    out = capsys.readouterr().out
    assert code == 0
    assert '"risk": "critical"' in out


def test_capability_export_cli(tmp_path):
    config = tmp_path / "hub.json"
    out_path = tmp_path / "capabilities.json"
    main(["--config", str(config), "hub", "init"])

    code = main(["--config", str(config), "capability", "export", "--out", str(out_path)])

    assert code == 0
    exported = json.loads(out_path.read_text(encoding="utf-8"))
    assert any(card["id"] == "docs.search" for card in exported)
