"""Hub event helpers built on the core append-only ledger."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from werktools.ledger import append, tail
from werktools.redaction import redact_payload
from werktools.tools.audit import verify_chain

__all__ = ["record_event", "recent_events", "recent_events_verified", "redact_payload"]


def record_event(
    path: str | Path,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> dict:
    """Append a typed Hub event to a JSONL ledger."""
    body = {"type": event_type, **redact_payload(payload or {})}
    return append(Path(path), body)


def recent_events(path: str | Path, limit: int = 20) -> list[dict]:
    """Return the most recent Hub ledger records.

    Note: this returns records WITHOUT verifying the hash chain (corrupt lines
    are silently dropped by the reader). Callers presenting events as evidence
    must use ``recent_events_verified`` so a forged chain cannot be shown
    silently (MF12).
    """
    if limit <= 0:
        return []
    return tail(Path(path))[-limit:]


def recent_events_verified(
    path: str | Path, limit: int = 20
) -> tuple[list[dict], bool, int]:
    """Return recent records plus a verified marker for the WHOLE chain.

    Verification runs over the entire file (tampering anywhere breaks the
    chain), independent of the tail window. Fail-closed: any verification
    failure returns chain_verified=False and only an integer error count is
    exposed (never payloads/secrets). On an absent/empty ledger the chain is
    vacuously valid (zero errors).

    Note: the ledger has no rotation/compaction and will grow unboundedly over
    the lifetime of the hub process. Callers should not call this on every
    request at high throughput.
    """
    p = Path(path)
    if not p.exists():
        return [], True, 0
    # NOTE: two separate file reads occur here. recent_events reads the tail
    # of the ledger file; verify_chain reads the entire file independently.
    # chain_verified describes the chain state at the time of the second read
    # (verify_chain), not necessarily the same state as the events tail slice.
    # A unified single-read implementation would unify these; for now the two
    # reads are documented explicitly rather than claimed to be a single pass.
    events = recent_events(path, limit)
    result = verify_chain(p)
    return events, result.ok, len(result.errors)
