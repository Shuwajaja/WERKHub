"""Local capability catalog helpers derived from Hub tool cards."""

from __future__ import annotations

import json
from pathlib import Path

from werktools.classify import classify_tool

from .contracts import HubConfig
from .registry import get_tool


def capability_cards(config: HubConfig) -> list[dict]:
    """Return deterministic plain-dict capability cards."""
    return sorted((tool.to_dict() for tool in config.tools), key=lambda item: item["id"])


def show_capability(config: HubConfig, capability_id: str) -> dict:
    """Return one capability card by id."""
    return get_tool(config, capability_id).to_dict()


def classify_capability(manifest: dict) -> dict:
    """Classify a local manifest with the offline advisory classifier."""
    return classify_tool(manifest)


def export_capabilities(config: HubConfig, path: str | Path) -> list[dict]:
    """Write capability cards as deterministic JSON."""
    cards = capability_cards(config)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(cards, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return cards
