"""Local/private knowledge capsule helpers for WERK Vault."""

from __future__ import annotations

import hashlib
import json
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..redaction import mask_secret_text
from .trace import TraceEvent, append_event, recent_events

_SOURCES = "sources.json"
_INDEX = "index.jsonl"
_AUDIT = "audit.jsonl"
_TEXT_SUFFIXES = {".md", ".markdown", ".txt"}


@dataclass(frozen=True)
class VaultSource:
    """One registered local Vault source."""

    source_id: str
    label: str
    path: str
    classification: str
    owner: str
    profiles: tuple[str, ...]
    created_at: str

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "VaultSource":
        return cls(
            source_id=str(raw["source_id"]),
            label=str(raw["label"]),
            path=str(raw["path"]),
            classification=str(raw.get("classification", "internal")),
            owner=str(raw.get("owner", "")),
            profiles=tuple(str(item) for item in raw.get("profiles", ("default",))),
            created_at=str(raw.get("created_at", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "label": self.label,
            "path": self.path,
            "classification": self.classification,
            "owner": self.owner,
            "profiles": list(self.profiles),
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class VaultItem:
    """One source-backed indexed local snippet."""

    item_id: str
    source_id: str
    source_label: str
    path: str
    classification: str
    owner: str
    text: str
    snippet: str
    created_at: str

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "VaultItem":
        return cls(
            item_id=str(raw["item_id"]),
            source_id=str(raw["source_id"]),
            source_label=str(raw["source_label"]),
            path=str(raw["path"]),
            classification=str(raw.get("classification", "internal")),
            owner=str(raw.get("owner", "")),
            text=str(raw.get("text", "")),
            snippet=str(raw.get("snippet", "")),
            created_at=str(raw.get("created_at", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "source_id": self.source_id,
            "source_label": self.source_label,
            "path": self.path,
            "classification": self.classification,
            "owner": self.owner,
            "text": self.text,
            "snippet": self.snippet,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class AccessDecision:
    """Profile/source access explanation."""

    decision: str
    source_id: str
    profile: str
    reason: str


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _root(root: str | Path) -> Path:
    return Path(root)


def _sources_path(root: str | Path) -> Path:
    return _root(root) / _SOURCES


def _index_path(root: str | Path) -> Path:
    return _root(root) / _INDEX


def _audit_path(root: str | Path) -> Path:
    return _root(root) / _AUDIT


def init_vault(root: str | Path) -> Path:
    """Create an empty local Vault directory if needed."""
    vault = _root(root)
    vault.mkdir(parents=True, exist_ok=True)
    if not _sources_path(vault).exists():
        _sources_path(vault).write_text("[]\n", encoding="utf-8")
    if not _index_path(vault).exists():
        _index_path(vault).write_text("", encoding="utf-8")
    return vault


def _load_sources(root: str | Path) -> list[VaultSource]:
    init_vault(root)
    raw = json.loads(_sources_path(root).read_text(encoding="utf-8") or "[]")
    if not isinstance(raw, list):
        return []
    return [VaultSource.from_dict(item) for item in raw if isinstance(item, dict)]


def _write_sources(root: str | Path, values: list[VaultSource]) -> None:
    init_vault(root)
    _sources_path(root).write_text(
        json.dumps([source.to_dict() for source in values], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _load_items(root: str | Path) -> list[VaultItem]:
    init_vault(root)
    items: list[VaultItem] = []
    for line in _index_path(root).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            warnings.warn(
                f"vault._load_items: skipping corrupt line in {_index_path(root)}: {exc}",
                stacklevel=2,
            )
            continue
        if not isinstance(raw, dict):
            continue
        try:
            items.append(VaultItem.from_dict(raw))
        except (KeyError, TypeError) as exc:
            warnings.warn(
                f"vault._load_items: skipping corrupt line in {_index_path(root)}: {exc}",
                stacklevel=2,
            )
            continue
    return items


def _write_items(root: str | Path, values: list[VaultItem]) -> None:
    init_vault(root)
    with _index_path(root).open("w", encoding="utf-8") as handle:
        for item in values:
            handle.write(json.dumps(item.to_dict(), sort_keys=True) + "\n")


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def _normalize_profiles(profiles: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
    values = tuple(str(profile) for profile in profiles or ("default",) if str(profile))
    return values or ("default",)


def _is_visible(source: VaultSource, profile: str) -> bool:
    return "*" in source.profiles or profile in source.profiles


def _source_files(source_path: Path) -> list[Path]:
    if source_path.is_file():
        return [source_path] if source_path.suffix.lower() in _TEXT_SUFFIXES else []
    if not source_path.exists():
        raise FileNotFoundError(f"Vault source not found: {source_path}")
    return sorted(
        path
        for path in source_path.rglob("*")
        if path.is_file() and not path.is_symlink() and path.suffix.lower() in _TEXT_SUFFIXES
    )


def _snippet(text: str, limit: int = 240) -> str:
    return " ".join(text.split())[:limit]


def _index_source(source: VaultSource) -> list[VaultItem]:
    items: list[VaultItem] = []
    for path in _source_files(Path(source.path)):
        try:
            raw_text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError) as exc:
            warnings.warn(f"vault._index_source: skipping {path}: {exc}", stacklevel=2)
            continue
        text = mask_secret_text(raw_text)
        item_id = _stable_id("vault_item", source.source_id, str(path.resolve()), text)
        items.append(
            VaultItem(
                item_id=item_id,
                source_id=source.source_id,
                source_label=source.label,
                path=str(path.resolve()),
                classification=source.classification,
                owner=source.owner,
                text=text,
                snippet=_snippet(text),
                created_at=source.created_at,
            )
        )
    return items


def add_source(
    root: str | Path,
    source_path: str | Path,
    label: str,
    classification: str = "internal",
    owner: str = "",
    profiles: tuple[str, ...] | list[str] | None = None,
) -> tuple[VaultSource, list[VaultItem]]:
    """Register and index one local file or folder source."""
    init_vault(root)
    source_root = Path(source_path).resolve()
    source = VaultSource(
        source_id=_stable_id("vault_source", str(source_root), label),
        label=label,
        path=str(source_root),
        classification=classification,
        owner=owner,
        profiles=_normalize_profiles(profiles),
        created_at=_now_iso(),
    )
    known_sources = [item for item in _load_sources(root) if item.source_id != source.source_id]
    known_sources.append(source)
    _write_sources(root, sorted(known_sources, key=lambda item: item.label))

    indexed = _index_source(source)
    existing_items = [item for item in _load_items(root) if item.source_id != source.source_id]
    _write_items(root, existing_items + indexed)
    return source, indexed


def sources(root: str | Path, profile: str | None = None) -> list[VaultSource]:
    """List registered sources, optionally filtered by profile visibility."""
    values = _load_sources(root)
    if profile is None:
        return values
    return [source for source in values if _is_visible(source, profile)]


def _source_by_id(root: str | Path) -> dict[str, VaultSource]:
    return {source.source_id: source for source in _load_sources(root)}


def _audit(root: str | Path, event_type: str, profile: str, payload: dict[str, Any]) -> None:
    append_event(_audit_path(root), event_type, actor=profile, payload=payload)


def search(root: str | Path, query: str, profile: str = "default", limit: int = 10) -> list[VaultItem]:
    """Search visible local Vault items by simple case-insensitive substring."""
    source_map = _source_by_id(root)
    needle = query.lower()
    results: list[VaultItem] = []
    denied_sources: set[str] = set()
    for item in _load_items(root):
        source = source_map.get(item.source_id)
        if source is None:
            continue
        haystack = f"{item.source_label}\n{item.path}\n{item.text}".lower()
        if needle not in haystack:
            continue
        if not _is_visible(source, profile):
            denied_sources.add(source.label)
            continue
        if len(results) < limit:
            results.append(item)
    _audit(
        root,
        "vault.search",
        profile,
        {
            "query": mask_secret_text(query),
            "results": len(results),
            "denied_sources": sorted(denied_sources),
        },
    )
    return results


def show_item(
    root: str | Path,
    item_id: str,
    profile: str = "default",
    reveal_secrets: bool = False,
) -> VaultItem:
    """Return one source-backed item if visible to the profile."""
    source_map = _source_by_id(root)
    for item in _load_items(root):
        if item.item_id != item_id:
            continue
        source = source_map.get(item.source_id)
        if source is None or not _is_visible(source, profile):
            _audit(root, "vault.show.denied", profile, {"item_id": item_id, "source_id": item.source_id})
            raise PermissionError(f"item {item_id!r} is not visible to profile {profile!r}")
        if reveal_secrets:
            # The index is data, not authority: a reveal must stay inside the
            # registered source root even if index.jsonl was tampered with.
            item_path = Path(item.path).resolve()
            source_root = Path(source.path).resolve()
            if item_path != source_root and not item_path.is_relative_to(source_root):
                _audit(
                    root,
                    "vault.show.denied",
                    profile,
                    {"item_id": item_id, "source_id": item.source_id, "reason": "path outside source root"},
                )
                raise PermissionError(f"item {item_id!r} points outside its registered source root")
            text = item_path.read_text(encoding="utf-8")
        else:
            text = item.text
        result = VaultItem(
            item_id=item.item_id,
            source_id=item.source_id,
            source_label=item.source_label,
            path=item.path,
            classification=item.classification,
            owner=item.owner,
            text=text,
            snippet=_snippet(text),
            created_at=item.created_at,
        )
        _audit(
            root,
            "vault.show",
            profile,
            {"item_id": item.item_id, "source_id": item.source_id, "revealed": reveal_secrets},
        )
        return result
    raise KeyError(f"Vault item not found: {item_id}")


def explain_access(root: str | Path, source_id: str, profile: str = "default") -> AccessDecision:
    """Explain profile visibility for one source."""
    source = _source_by_id(root).get(source_id)
    if source is None:
        return AccessDecision("deny", source_id, profile, "unknown source fails closed")
    if _is_visible(source, profile):
        return AccessDecision("allow", source_id, profile, f"source is visible to profile {profile!r}")
    return AccessDecision("deny", source_id, profile, f"source is not visible to profile {profile!r}")


def audit_recent(root: str | Path, limit: int = 20) -> list[TraceEvent]:
    """Return recent Vault audit events."""
    return recent_events(_audit_path(root), limit=limit)
