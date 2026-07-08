"""Local append-only trace helpers for WERK Trace."""

from __future__ import annotations

import hashlib
import json
import uuid
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..ledger import GENESIS
from ..redaction import redact_payload

__all__ = [
    "TraceEvent",
    "TraceVerification",
    "append_event",
    "export_trace",
    "read_events",
    "recent_events",
    "redact_payload",
    "verify_trace",
]


@dataclass(frozen=True)
class TraceEvent:
    """One local trace event."""

    event_id: str
    event_type: str
    actor: str
    source_id: str
    payload: dict[str, Any]
    created_at: str
    prev_hash: str
    hash: str

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "TraceEvent":
        payload = raw.get("payload", {})
        if not isinstance(payload, dict):
            payload = {"value": payload}
        return cls(
            event_id=str(raw["id"]),
            event_type=str(raw["event_type"]),
            actor=str(raw.get("actor", "")),
            source_id=str(raw.get("source_id", "")),
            payload=payload,
            created_at=str(raw.get("created_at", "")),
            prev_hash=str(raw.get("prev_hash", "")),
            hash=str(raw.get("hash", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.event_id,
            "event_type": self.event_type,
            "actor": self.actor,
            "source_id": self.source_id,
            "payload": self.payload,
            "created_at": self.created_at,
            "prev_hash": self.prev_hash,
            "hash": self.hash,
        }


@dataclass(frozen=True)
class TraceVerification:
    """Result of local trace hash verification."""

    ok: bool
    event_count: int
    errors: tuple[str, ...]


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _event_id() -> str:
    return f"trace_{uuid.uuid4().hex[:12]}"


def _canonical_hash(body: dict[str, Any]) -> str:
    # Same canonical form as werktools.ledger so trace and audit chains verify
    # interchangeably: sorted keys, compact separators, ensure_ascii=False.
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _hash_event(event_body: dict[str, Any]) -> str:
    body = dict(event_body)
    body.pop("hash", None)
    return _canonical_hash(body)


def append_event(
    trace_path: str | Path,
    event_type: str,
    actor: str | None = None,
    payload: dict[str, Any] | None = None,
    source_id: str | None = None,
    created_at: str | None = None,
    hash_chain: bool = True,
) -> TraceEvent:
    """Append one structured local trace event to a JSONL file."""
    path = Path(trace_path)
    previous_events = read_events(path)
    previous_hash = ""
    if hash_chain:
        # Link to the most recent hashed event, not blindly to the last event:
        # an unhashed event in between must not silently break the chain.
        previous_hash = GENESIS
        for prior in reversed(previous_events):
            if prior.hash:
                previous_hash = prior.hash
                break
    event = TraceEvent(
        event_id=_event_id(),
        event_type=event_type,
        actor=actor or "",
        source_id=source_id or "",
        payload=redact_payload(payload or {}),
        created_at=created_at or _now_iso(),
        prev_hash=previous_hash,
        hash="",
    )
    body = event.to_dict()
    if hash_chain:
        body["hash"] = _hash_event(body)
        event = TraceEvent.from_dict(body)

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event.to_dict(), sort_keys=True) + "\n")
    return event


def read_events(trace_path: str | Path) -> list[TraceEvent]:
    """Read trace events from a local JSONL file."""
    path = Path(trace_path)
    if not path.exists():
        return []
    events: list[TraceEvent] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(raw, dict):
            continue
        try:
            events.append(TraceEvent.from_dict(raw))
        except (KeyError, TypeError) as exc:
            warnings.warn(f"read_events: skipping malformed record: {exc}", stacklevel=2)
            continue
    return events


def recent_events(trace_path: str | Path, limit: int = 20) -> list[TraceEvent]:
    """Return the newest local trace events."""
    if limit <= 0:
        return []
    return read_events(trace_path)[-limit:]


def verify_trace(trace_path: str | Path) -> TraceVerification:
    """Verify hash-chain integrity for local trace events.

    Unhashed events are tolerated only before the chain starts; once a hashed
    event exists, every later event must be hashed. An unhashed event that
    still carries a prev_hash is treated as a cleared-hash tampering signal.
    """
    errors: list[str] = []
    expected_prev = GENESIS
    chain_started = False
    events = read_events(trace_path)
    for index, event in enumerate(events, start=1):
        body = event.to_dict()
        actual_hash = body.get("hash", "")
        if not actual_hash:
            if chain_started:
                errors.append(f"event {index}: unhashed event after chained events")
            elif event.prev_hash:
                errors.append(f"event {index}: unhashed event carries prev_hash")
            continue
        if event.prev_hash != expected_prev:
            errors.append(f"event {index}: prev_hash mismatch")
        expected_hash = _hash_event(body)
        if actual_hash != expected_hash:
            errors.append(f"event {index}: hash mismatch")
        chain_started = True
        expected_prev = str(actual_hash)
    if events and not chain_started:
        # Fail closed: a fully-unhashed file is indistinguishable from one
        # whose hash and prev_hash fields were all cleared by tampering.
        errors.append("no hashed events: chain absent or fully cleared")
    return TraceVerification(ok=not errors, event_count=len(events), errors=tuple(errors))


def export_trace(
    trace_path: str | Path,
    out_path: str | Path,
    event_type: str | None = None,
    actor: str | None = None,
    source_id: str | None = None,
) -> int:
    """Export selected local trace events to a JSONL file."""
    events = [
        event
        for event in read_events(trace_path)
        if (event_type is None or event.event_type == event_type)
        and (actor is None or event.actor == actor)
        and (source_id is None or event.source_id == source_id)
    ]
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event.to_dict(), sort_keys=True) + "\n")
    return len(events)
