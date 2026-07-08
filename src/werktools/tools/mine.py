"""Local knowledge-card extraction for WERK Mine."""

from __future__ import annotations

import hashlib
import json
import re
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_URL_RE = re.compile(r"https?://[^\s)>\]]+")
_PREFIX_RE = re.compile(r"^(?P<label>Pattern|Warning|Risk|Avoid):\s*(?P<body>.+)$", re.IGNORECASE)


@dataclass(frozen=True)
class MineCard:
    """One local WERK Mine knowledge card."""

    id: str
    title: str
    topic: str
    summary: str
    source: str
    source_status: str
    links: tuple[str, ...]
    patterns: tuple[str, ...]
    warnings: tuple[str, ...]
    tags: tuple[str, ...]
    created_at: str

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "MineCard":
        return cls(
            id=str(raw["id"]),
            title=str(raw["title"]),
            topic=str(raw.get("topic", "")),
            summary=str(raw.get("summary", "")),
            source=str(raw.get("source", "")),
            source_status=str(raw.get("source_status", "provided_unverified")),
            links=tuple(str(item) for item in raw.get("links", ())),
            patterns=tuple(str(item) for item in raw.get("patterns", ())),
            warnings=tuple(str(item) for item in raw.get("warnings", ())),
            tags=tuple(str(item) for item in raw.get("tags", ())),
            created_at=str(raw.get("created_at", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "topic": self.topic,
            "summary": self.summary,
            "source": self.source,
            "source_status": self.source_status,
            "links": list(self.links),
            "patterns": list(self.patterns),
            "warnings": list(self.warnings),
            "tags": list(self.tags),
            "created_at": self.created_at,
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stable_id(source: str, title: str) -> str:
    digest = hashlib.sha256(f"{source}\n{title}".encode("utf-8")).hexdigest()[:12]
    return f"mine_{digest}"


def _clean_url(url: str) -> str:
    return url.rstrip(".,")


def extract_links(text: str) -> tuple[str, ...]:
    """Extract HTTP links from provided text."""
    return tuple(dict.fromkeys(_clean_url(match.group(0)) for match in _URL_RE.finditer(text)))


def _prefixed(text: str, labels: set[str]) -> tuple[str, ...]:
    values: list[str] = []
    for line in text.splitlines():
        match = _PREFIX_RE.match(line.strip())
        if match and match.group("label").lower() in labels:
            values.append(match.group("body").strip())
    return tuple(values)


def extract_patterns(text: str) -> tuple[str, ...]:
    """Extract explicit Pattern lines."""
    return _prefixed(text, {"pattern"})


def extract_warnings(text: str) -> tuple[str, ...]:
    """Extract explicit warning/risk/avoidance lines."""
    return _prefixed(text, {"warning", "risk", "avoid"})


def _title_from_text(source: str, text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or Path(source).stem
        if stripped:
            return stripped[:80]
    return Path(source).stem or "untitled"


def _summary(text: str) -> str:
    lines = [
        line.strip().lstrip("#").strip()
        for line in text.splitlines()
        if line.strip() and not _PREFIX_RE.match(line.strip()) and not _URL_RE.search(line)
    ]
    return " ".join(lines)[:240]


def _tags(topic: str, patterns: tuple[str, ...], warnings: tuple[str, ...]) -> tuple[str, ...]:
    tags = {"mine"}
    if topic:
        tags.add(topic)
    if patterns:
        tags.add("patterns")
    if warnings:
        tags.add("warnings")
    return tuple(sorted(tags))


def create_card(
    source: str,
    text: str,
    topic: str | None = None,
    title: str | None = None,
    created_at: str | None = None,
) -> MineCard:
    """Create one local knowledge card from provided text."""
    card_title = title or _title_from_text(source, text)
    card_topic = topic or "general"
    patterns = extract_patterns(text)
    warnings = extract_warnings(text)
    return MineCard(
        id=_stable_id(source, card_title),
        title=card_title,
        topic=card_topic,
        summary=_summary(text),
        source=source,
        source_status="provided_unverified",
        links=extract_links(text),
        patterns=patterns,
        warnings=warnings,
        tags=_tags(card_topic, patterns, warnings),
        created_at=created_at or _now_iso(),
    )


def write_card(card: MineCard, out_dir: str | Path) -> Path:
    """Write a MineCard as a local JSON file."""
    target_dir = Path(out_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{card.id}.json"
    path.write_text(json.dumps(card.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def load_cards(cards_dir: str | Path) -> list[MineCard]:
    """Load local Mine cards from a directory."""
    root = Path(cards_dir)
    cards: list[MineCard] = []
    if not root.exists():
        return cards
    for path in sorted(root.glob("*.json")):
        if path.name == "index.json":
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            warnings.warn(f"mine.load_cards: skipping {path}: {exc}", stacklevel=2)
            continue
        if not isinstance(raw, dict):
            continue
        try:
            cards.append(MineCard.from_dict(raw))
        except (KeyError, TypeError) as exc:
            warnings.warn(f"mine.load_cards: skipping {path}: {exc}", stacklevel=2)
            continue
    return cards


def write_index(cards_dir: str | Path) -> Path:
    """Write a small JSON index for local cards."""
    root = Path(cards_dir)
    cards = load_cards(root)
    index = [
        {
            "id": card.id,
            "title": card.title,
            "topic": card.topic,
            "source": card.source,
            "tags": list(card.tags),
        }
        for card in cards
    ]
    path = root / "index.json"
    path.write_text(json.dumps(index, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def query_cards(cards: list[MineCard], query: str) -> list[MineCard]:
    """Search local cards by simple case-insensitive substring matching."""
    needle = query.lower()
    matches: list[MineCard] = []
    for card in cards:
        haystack = " ".join(
            [
                card.title,
                card.topic,
                card.summary,
                " ".join(card.tags),
                " ".join(card.patterns),
                " ".join(card.warnings),
            ]
        ).lower()
        if needle in haystack:
            matches.append(card)
    return matches


def write_report(cards: list[MineCard], out: str | Path, topic: str | None = None) -> Path:
    """Write a readable Markdown report from local cards."""
    selected = [card for card in cards if topic is None or card.topic == topic]
    target = Path(out)
    target.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# WERK Mine Report", "", f"Cards: {len(selected)}", ""]
    for card in selected:
        lines.extend(
            [
                f"## {card.title}",
                "",
                f"- id: `{card.id}`",
                f"- topic: `{card.topic}`",
                f"- source: `{card.source}`",
                f"- source_status: `{card.source_status}`",
                f"- links: {len(card.links)}",
                f"- warnings: {len(card.warnings)}",
                "",
                card.summary,
                "",
            ]
        )
    target.write_text("\n".join(lines), encoding="utf-8")
    return target
