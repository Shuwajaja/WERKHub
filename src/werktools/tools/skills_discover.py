"""Skills discovery: federate marketplace.json + ingest SKILL.md frontmatter.

Pulls skill metadata from remote .claude-plugin/marketplace.json manifests,
local SKILL.md files (YAML frontmatter), and an optional local SkillKit. All
results become CatalogCard objects — nothing is fetched-and-run, nothing
executes; source URLs are stored as provenance only (the no-runtime
boundary holds).

Discovery is read-only ENRICHMENT, so failures are GRACEFUL (ok=False +
warning + exit 0), unlike gate decisions and writes which fail closed.
Stdlib only; pyyaml is used if present (the [yaml] extra) else a minimal
flat-scalar parser. urllib is only touched when fetch_remote is True.
"""

from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..catalog import CatalogCard, _parse_markdown_card, normalize_trust_tier

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore[assignment]

MARKETPLACE_URLS = {
    "anthropic": "https://raw.githubusercontent.com/anthropics/claude-plugins/main/.claude-plugin/marketplace.json",
    "superpowers": "https://raw.githubusercontent.com/obra/superpowers/main/.claude-plugin/marketplace.json",
}
SKILLKIT_DEFAULT_URL = "http://localhost:3737"
_RISK_OK = ("read", "write", "destructive", "external", "secret", "unknown")


def _slug(name: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9-]+", "-", name.lower())).strip("-") or "skill"


def _stdlib_parse_yaml_block(lines: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            i += 1
            continue
        if line.startswith((" ", "\t")):
            raise ValueError("nested YAML is not supported by the stdlib parser")
        if ":" not in line:
            raise ValueError(f"invalid frontmatter line: {line!r}")
        key, _, rest = line.partition(":")
        key = key.strip()
        rest = rest.strip()
        if rest.startswith("[") and rest.endswith("]"):
            out[key] = [v.strip().strip("'\"") for v in rest[1:-1].split(",") if v.strip()]
        elif rest == "":
            items: list[str] = []
            j = i + 1
            while j < len(lines) and lines[j].lstrip().startswith("- "):
                items.append(lines[j].lstrip()[2:].strip().strip("'\""))
                j += 1
            if items:
                out[key] = items
                i = j
                continue
            out[key] = ""
        else:
            out[key] = rest.strip("'\"")
        i += 1
    return out


def parse_yaml_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Return (frontmatter, body). ({}, text) when there is no opening fence."""
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    if lines[0].strip() != "---":
        return {}, text
    close = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            close = idx
            break
    if close is None:
        raise ValueError("unterminated YAML frontmatter (missing closing ---)")
    block = lines[1:close]
    body = "\n".join(lines[close + 1:]).strip()
    if yaml is not None:
        parsed = yaml.safe_load("\n".join(block)) or {}
        if not isinstance(parsed, dict):
            raise ValueError("frontmatter must be a mapping")
        return parsed, body
    return _stdlib_parse_yaml_block(block), body


def _as_tuple(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return tuple(v.strip() for v in value.split(",") if v.strip())
    if isinstance(value, (list, tuple)):
        return tuple(str(v) for v in value)
    return ()


def parse_skill_md(path: str | Path) -> CatalogCard:
    """Parse a SKILL.md into a CatalogCard (frontmatter or legacy fallback)."""
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    front, body = parse_yaml_frontmatter(text)
    if not front:
        return _parse_markdown_card(p, "skill")
    name = front.get("name")
    description = front.get("description")
    if not name or not description:
        raise ValueError(f"{p.name}: SKILL.md frontmatter requires name and description")
    summary = str(description)
    if body:
        summary = f"{summary} {body}".strip()
    risk = str(front.get("risk", "unknown")).lower()
    profiles = _as_tuple(front.get("profiles")) or ("*",)
    return CatalogCard(
        card_id=_slug(str(name)),
        kind="skill",
        title=str(name),
        summary=summary[:1000],
        tags=_as_tuple(front.get("tags")),
        profiles=profiles,
        source=str(p.resolve()),
        risk=risk if risk in _RISK_OK else "unknown",
        requires_approval=bool(front.get("requires_approval", False)),
        metadata={},
        created_at="",
        trust_tier=normalize_trust_tier(front.get("trust_tier")),
        trust_source=str(front.get("trust_source", ""))[:64],
        trust_note=str(front.get("trust_note", ""))[:200],
    )


def load_skill_md_files(directory: str | Path) -> tuple[list[CatalogCard], list[tuple[str, str]]]:
    """Parse every *.md in a dir; return (cards, [(filename, error)])."""
    root = Path(directory)
    cards: list[CatalogCard] = []
    errors: list[tuple[str, str]] = []
    if not root.exists():
        return cards, errors
    for path in sorted(root.glob("*.md")):
        try:
            cards.append(parse_skill_md(path))
        except (ValueError, OSError) as exc:
            errors.append((path.name, str(exc)))
    return cards, errors


@dataclass(frozen=True)
class FederationResult:
    marketplace_id: str
    url: str
    cards: tuple[CatalogCard, ...]
    ok: bool
    error: str = ""


def _real_fetch(url: str, timeout: float) -> Any:
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - https manifests
        return json.loads(resp.read().decode("utf-8"))


def _marketplace_card(entry: dict[str, Any], marketplace_id: str) -> CatalogCard | None:
    name = entry.get("name")
    description = entry.get("description")
    if not name or not description:
        return None
    risk = str(entry.get("risk", "unknown")).lower()
    return CatalogCard(
        card_id=_slug(str(name)),
        kind="skill",
        title=str(name),
        summary=str(description),
        tags=_as_tuple(entry.get("tags")),
        profiles=_as_tuple(entry.get("profiles")) or ("*",),
        source=marketplace_id,
        risk=risk if risk in _RISK_OK else "unknown",
        requires_approval=bool(entry.get("requires_approval", False)),
        metadata={"skill_md_url": str(entry.get("source", ""))},
        created_at="",
        trust_tier=normalize_trust_tier(entry.get("trust_tier")),
        trust_source=str(entry.get("trust_source", ""))[:64],
        trust_note=str(entry.get("trust_note", ""))[:200],
    )


def fetch_marketplace(
    marketplace_id: str,
    url: str,
    *,
    _fetch: Callable[..., Any] | None = None,
    timeout: float = 10,
) -> FederationResult:
    """Fetch one marketplace.json into cards. Never raises."""
    fetcher = _fetch or _real_fetch
    try:
        body = fetcher(url, timeout)
    except Exception as exc:
        return FederationResult(marketplace_id, url, (), False, str(exc))
    entries = body.get("skills", []) if isinstance(body, dict) else (body if isinstance(body, list) else [])
    cards: list[CatalogCard] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        card = _marketplace_card(entry, marketplace_id)
        if card is not None:
            cards.append(card)
    return FederationResult(marketplace_id, url, tuple(cards), True)


def federate_marketplaces(
    extra_urls: dict[str, str] | None = None,
    *,
    _fetch: Callable[..., Any] | None = None,
    timeout: float = 10,
) -> list[FederationResult]:
    urls = {**MARKETPLACE_URLS, **(extra_urls or {})}
    return [fetch_marketplace(mid, url, _fetch=_fetch, timeout=timeout) for mid, url in sorted(urls.items())]


@dataclass(frozen=True)
class SkillKitResult:
    url: str
    cards: tuple[CatalogCard, ...]
    ok: bool
    error: str = ""


def query_skillkit(
    query: str,
    *,
    base_url: str = SKILLKIT_DEFAULT_URL,
    limit: int = 10,
    _fetch: Callable[..., Any] | None = None,
    timeout: float = 5,
) -> SkillKitResult:
    fetcher = _fetch or _real_fetch
    url = f"{base_url}/search?q={query}&limit={limit}"
    try:
        body = fetcher(url, timeout)
    except Exception as exc:
        return SkillKitResult(url, (), False, str(exc))
    cards: list[CatalogCard] = []
    for entry in body if isinstance(body, list) else []:
        if not isinstance(entry, dict):
            continue
        card = _marketplace_card(entry, "skillkit")
        if card is not None:
            cards.append(card)
    return SkillKitResult(url, tuple(cards), True)


@dataclass(frozen=True)
class DiscoverResult:
    cards: tuple[CatalogCard, ...]
    federation_results: tuple[FederationResult, ...]
    skillkit_result: Any
    local_errors: tuple[tuple[str, str], ...]
    total_fetched: int
    total_deduped: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "cards": [c.to_dict() for c in self.cards],
            "total_fetched": self.total_fetched,
            "total_deduped": self.total_deduped,
            "local_errors": [list(e) for e in self.local_errors],
            "marketplaces_ok": [f.marketplace_id for f in self.federation_results if f.ok],
        }


def discover_skills(
    local_dir: str | Path | None = None,
    *,
    marketplace_urls: dict[str, str] | None = None,
    skillkit_url: str | None = None,
    skillkit_query: str = "",
    fetch_remote: bool = True,
    _fetch: Callable[..., Any] | None = None,
    timeout: float = 10,
) -> DiscoverResult:
    """Discover skills from local + (optionally) remote sources. Never raises."""
    local_cards: list[CatalogCard] = []
    local_errors: list[tuple[str, str]] = []
    if local_dir is not None:
        local_cards, local_errors = load_skill_md_files(local_dir)

    federation: list[FederationResult] = []
    skillkit: SkillKitResult | None = None
    if fetch_remote:
        federation = federate_marketplaces(marketplace_urls, _fetch=_fetch, timeout=timeout)
        if skillkit_url is not None or skillkit_query:
            skillkit = query_skillkit(
                skillkit_query, base_url=skillkit_url or SKILLKIT_DEFAULT_URL, _fetch=_fetch, timeout=timeout
            )

    market_cards = [c for f in federation for c in f.cards]
    skillkit_cards = list(skillkit.cards) if skillkit else []
    total_fetched = len(local_cards) + len(market_cards) + len(skillkit_cards)

    seen: dict[str, CatalogCard] = {}
    for card in [*local_cards, *market_cards, *skillkit_cards]:  # priority order
        seen.setdefault(card.card_id, card)
    deduped = tuple(seen[k] for k in sorted(seen))

    return DiscoverResult(
        cards=deduped,
        federation_results=tuple(federation),
        skillkit_result=skillkit,
        local_errors=tuple(local_errors),
        total_fetched=total_fetched,
        total_deduped=len(deduped),
    )
