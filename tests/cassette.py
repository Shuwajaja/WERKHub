"""Record/replay cassette helper for offline deterministic tests.

A cassette is a JSON array of recorded calls. Tests replay recorded
responses so no network or live provider is ever touched. A miss during
replay raises RecordingRequired so a missing recording fails loudly instead
of silently calling out.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


class RecordingRequired(Exception):
    """Raised when a replay cassette has no entry for the requested call."""


@dataclass(frozen=True)
class CassetteEntry:
    """One recorded call: the kwargs that produced it and the response."""

    call_kwargs: dict[str, Any]
    response: Any
    recorded_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "call_kwargs": self.call_kwargs,
            "response": self.response,
            "recorded_at": self.recorded_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "CassetteEntry":
        return cls(
            call_kwargs=dict(raw.get("call_kwargs", {})),
            response=raw.get("response"),
            recorded_at=str(raw.get("recorded_at", "")),
        )


def load_cassette(path: str | Path) -> list[CassetteEntry]:
    """Load cassette entries from a local JSON array file."""
    source = Path(path)
    if not source.exists():
        return []
    raw = json.loads(source.read_text(encoding="utf-8") or "[]")
    if not isinstance(raw, list):
        return []
    return [CassetteEntry.from_dict(item) for item in raw if isinstance(item, dict)]


def save_cassette(path: str | Path, entries: list[CassetteEntry]) -> Path:
    """Write cassette entries as a deterministic JSON array."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps([entry.to_dict() for entry in entries], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return target


class CassetteReplayer:
    """Replay recorded responses in FIFO order; never calls live in replay."""

    def __init__(self, path: str | Path, live_fn: Callable[..., Any] | None = None) -> None:
        self._entries = load_cassette(path)
        self._cursor = 0
        self._live_fn = live_fn

    def call(self, **kwargs: Any) -> Any:
        if self._cursor < len(self._entries):
            entry = self._entries[self._cursor]
            self._cursor += 1
            return entry.response
        raise RecordingRequired(f"no cassette entry for call #{self._cursor} kwargs={kwargs!r}")
