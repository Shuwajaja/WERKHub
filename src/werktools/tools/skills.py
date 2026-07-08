"""Local skill catalog helpers for the Skill Library.

Skills are knowledge assets (Markdown instructions, workflows, guides).
They are cataloged, matched, and exported - never executed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..catalog import CatalogCard, export_cards, load_cards, match_cards, visible_cards


def list_skills(directory: str | Path, profile: str | None = None) -> list[CatalogCard]:
    """List local skill cards; profile=None is the unfiltered operator view."""
    cards = load_cards(directory, kind="skill")
    if profile is None:
        return cards
    return visible_cards(cards, profile)


def show_skill(directory: str | Path, skill_id: str, profile: str = "default") -> CatalogCard:
    """Return one skill card if it is visible to the profile."""
    for card in list_skills(directory):
        if card.card_id != skill_id:
            continue
        if not visible_cards([card], profile):
            raise PermissionError(f"skill {skill_id!r} is not visible to profile {profile!r}")
        return card
    raise KeyError(f"unknown skill: {skill_id}")


def match_skills(
    directory: str | Path,
    task: str,
    profile: str = "default",
    limit: int = 5,
) -> list[CatalogCard]:
    """Match visible skills against a task description."""
    return match_cards(visible_cards(list_skills(directory), profile), task, limit=limit)


def export_skills(directory: str | Path, out_path: str | Path, profile: str = "default") -> list[dict[str, Any]]:
    """Explicitly export the skills visible to a profile as JSON."""
    return export_cards(visible_cards(list_skills(directory), profile), out_path)


def discover_and_list_skills(
    local_dir: str | Path | None = None,
    *,
    profile: str | None = None,
    fetch_remote: bool = True,
    marketplace_urls: dict[str, str] | None = None,
    skillkit_url: str | None = None,
    skillkit_query: str = "",
    _fetch: Any = None,
) -> list[CatalogCard]:
    """Discover skills from local + remote sources, profile-filtered."""
    from .skills_discover import discover_skills

    result = discover_skills(
        local_dir,
        marketplace_urls=marketplace_urls,
        skillkit_url=skillkit_url,
        skillkit_query=skillkit_query,
        fetch_remote=fetch_remote,
        _fetch=_fetch,
    )
    cards = list(result.cards)
    if profile is not None:
        cards = visible_cards(cards, profile)
    return cards
