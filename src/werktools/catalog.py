"""Generic local catalog primitive for Wave-5 catalog tools.

A catalog is a directory of cards. Cards are plain JSON files or Markdown
files with a small `Key: value` header block. Everything here is local,
deterministic, and read-only: catalogs describe assets, they never run them.
"""

from __future__ import annotations

import json
import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_HEADER_PATTERN = re.compile(r"^(?P<key>[A-Za-z][A-Za-z _-]*):\s*(?P<value>.*)$")
_RISK_VALUES = ("read", "write", "destructive", "external", "secret", "unknown")

# Trust taxonomy (metadata only — never an enforcement decision in P1).
# Ordered most- to least-trusted; the default and every unknown value fail
# closed to the lowest tier.
TRUST_TIERS = ("Official", "Security-Scanned", "Community-Unverified")
DEFAULT_TRUST_TIER = "Community-Unverified"
_TRUST_SOURCE_MAX = 64
_TRUST_NOTE_MAX = 200


def normalize_trust_tier(value: Any) -> str:
    """Return a known trust tier, failing closed to Community-Unverified."""
    text = str(value).strip() if value is not None else ""
    return text if text in TRUST_TIERS else DEFAULT_TRUST_TIER


@dataclass(frozen=True)
class CatalogCard:
    """One described local asset (skill, connector, hook, ...)."""

    card_id: str
    kind: str
    title: str
    summary: str
    tags: tuple[str, ...]
    profiles: tuple[str, ...]
    source: str
    risk: str
    requires_approval: bool
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""
    trust_tier: str = DEFAULT_TRUST_TIER
    trust_source: str = ""
    trust_note: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "CatalogCard":
        metadata = raw.get("metadata", {})
        return cls(
            card_id=str(raw["card_id"]),
            kind=str(raw.get("kind", "card")),
            title=str(raw.get("title", raw["card_id"])),
            summary=str(raw.get("summary", "")),
            tags=tuple(str(item) for item in raw.get("tags", ())),
            # Missing profiles means public; an EXPLICIT empty list means
            # visible to nobody and must not silently widen to public.
            profiles=tuple(str(item) for item in raw.get("profiles", ("*",))),
            source=str(raw.get("source", "")),
            risk=str(raw.get("risk", "unknown")),
            requires_approval=bool(raw.get("requires_approval", False)),
            metadata=dict(metadata) if isinstance(metadata, dict) else {},
            created_at=str(raw.get("created_at", "")),
            trust_tier=normalize_trust_tier(raw.get("trust_tier")),
            trust_source=str(raw.get("trust_source", ""))[:_TRUST_SOURCE_MAX],
            trust_note=str(raw.get("trust_note", ""))[:_TRUST_NOTE_MAX],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "card_id": self.card_id,
            "kind": self.kind,
            "title": self.title,
            "summary": self.summary,
            "tags": list(self.tags),
            "profiles": list(self.profiles),
            "source": self.source,
            "risk": self.risk,
            "requires_approval": self.requires_approval,
            "metadata": self.metadata,
            "created_at": self.created_at,
            "trust_tier": self.trust_tier,
            "trust_source": self.trust_source,
            "trust_note": self.trust_note,
        }


def _csv(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _parse_markdown_card(path: Path, kind: str) -> CatalogCard:
    text = path.read_text(encoding="utf-8")
    title = path.stem
    headers: dict[str, str] = {}
    summary = ""
    body_lines: list[str] = []
    # Only the FIRST contiguous run of `Key: value` lines is the header
    # block; a later KV-looking block is body and must not override headers.
    state = "preamble"
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# ") and title == path.stem:
            title = stripped[2:].strip()
            continue
        if state == "preamble":
            if not stripped:
                continue
            matched = _HEADER_PATTERN.match(stripped)
            if matched:
                headers[matched.group("key").strip().lower()] = matched.group("value").strip()
                state = "headers"
                continue
            state = "body"
            body_lines.append(stripped)
            continue
        if state == "headers":
            if not stripped:
                state = "body"
                continue
            matched = _HEADER_PATTERN.match(stripped)
            if matched:
                headers[matched.group("key").strip().lower()] = matched.group("value").strip()
                continue
            state = "body"
            body_lines.append(stripped)
            continue
        if stripped:
            body_lines.append(stripped)
        elif body_lines:
            break
    if body_lines:
        summary = " ".join(body_lines)
    risk = headers.get("risk", "unknown").lower()
    return CatalogCard(
        card_id=path.stem,
        kind=kind,
        title=title,
        summary=summary,
        tags=_csv(headers.get("tags", "")),
        profiles=_csv(headers.get("profiles", "")) or ("*",),
        source=str(path.resolve()),
        risk=risk if risk in _RISK_VALUES else "unknown",
        requires_approval=headers.get("requires approval", headers.get("requires_approval", "")).lower()
        in {"true", "yes"},
        metadata={},
        created_at=headers.get("date", ""),
        trust_tier=normalize_trust_tier(headers.get("trust_tier", headers.get("trust"))),
        trust_source=headers.get("trust_source", "")[:_TRUST_SOURCE_MAX],
        trust_note=headers.get("trust_note", "")[:_TRUST_NOTE_MAX],
    )


def load_cards(directory: str | Path, kind: str) -> list[CatalogCard]:
    """Load JSON and Markdown cards from a local directory, skipping bad files."""
    root = Path(directory)
    if not root.exists():
        return []
    cards: list[CatalogCard] = []
    for path in sorted(root.glob("*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            warnings.warn(f"load_cards: skipping {path}: {exc}", stacklevel=2)
            continue
        if not isinstance(raw, dict):
            warnings.warn(f"load_cards: skipping {path}: root value is not a dict", stacklevel=2)
            continue
        try:
            card = CatalogCard.from_dict(raw)
        except (KeyError, TypeError) as exc:
            warnings.warn(f"load_cards: skipping {path}: {exc}", stacklevel=2)
            continue
        cards.append(
            CatalogCard.from_dict({**card.to_dict(), "kind": kind, "source": card.source or str(path.resolve())})
        )
    for path in sorted(root.glob("*.md")):
        try:
            cards.append(_parse_markdown_card(path, kind))
        except (OSError, UnicodeDecodeError) as exc:
            warnings.warn(f"load_cards: skipping {path}: {exc}", stacklevel=2)
            continue
    return sorted(cards, key=lambda card: card.card_id)


def visible_cards(cards: list[CatalogCard], profile: str) -> list[CatalogCard]:
    """Filter cards by profile visibility (`*` means public)."""
    return [card for card in cards if "*" in card.profiles or profile in card.profiles]


def _tokens(text: str) -> set[str]:
    return {token for token in re.findall(r"[a-z0-9]+", text.lower()) if len(token) > 2}


def match_cards(cards: list[CatalogCard], query: str, limit: int = 5) -> list[CatalogCard]:
    """Rank cards by deterministic token overlap with the query."""
    needle = _tokens(query)
    if not needle:
        return []
    scored: list[tuple[int, str, CatalogCard]] = []
    for card in cards:
        haystack = _tokens(f"{card.title} {card.summary} {' '.join(card.tags)}")
        score = len(needle & haystack)
        if score:
            scored.append((score, card.card_id, card))
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [card for _, _, card in scored[: max(0, limit)]]


def export_cards(cards: list[CatalogCard], out_path: str | Path) -> list[dict[str, Any]]:
    """Write cards as deterministic JSON and return the exported payload."""
    payload = [card.to_dict() for card in sorted(cards, key=lambda card: card.card_id)]
    target = Path(out_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload
