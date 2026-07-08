"""Catalog-only Integration Gate: connector manifests, scope review, approvals.

This is the policy/manifest layer from SPEC_MCP_WERK_INTEGRATION_GATE.md.
It records connectors, scopes, risk, and approval requests. It does NOT do
OAuth, hold tokens, or call any external system; per ADR-001 the
``approval_required`` verdict is explanation-only in v0.
"""

from __future__ import annotations

import json
import uuid
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..catalog import normalize_trust_tier
from ..redaction import is_secret_key
from .trace import TraceEvent, append_event, recent_events

_CONNECTORS = "connectors.json"
_APPROVALS = "approvals"
_AUDIT = "audit.jsonl"
_ACCESS_VALUES = ("read", "write", "destructive")


@dataclass(frozen=True)
class ConnectorScope:
    """One declared scope of a connector."""

    name: str
    access: str
    description: str

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ConnectorScope":
        access = str(raw.get("access", "unknown")).lower()
        return cls(
            name=str(raw["name"]),
            access=access if access in _ACCESS_VALUES else "unknown",
            description=str(raw.get("description", "")),
        )

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "access": self.access, "description": self.description}


@dataclass(frozen=True)
class Connector:
    """One configured integration, described but never executed."""

    connector_id: str
    label: str
    provider: str
    scopes: tuple[ConnectorScope, ...]
    profiles: tuple[str, ...]
    docs_url: str
    metadata: dict[str, Any]
    created_at: str

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Connector":
        metadata = raw.get("metadata", {})
        return cls(
            connector_id=str(raw["connector_id"]),
            label=str(raw.get("label", raw["connector_id"])),
            provider=str(raw.get("provider", "")),
            scopes=tuple(
                ConnectorScope.from_dict(item) for item in raw.get("scopes", ()) if isinstance(item, dict)
            ),
            profiles=tuple(str(item) for item in raw.get("profiles", ("default",))) or ("default",),
            docs_url=str(raw.get("docs_url", "")),
            metadata=dict(metadata) if isinstance(metadata, dict) else {},
            created_at=str(raw.get("created_at", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "connector_id": self.connector_id,
            "label": self.label,
            "provider": self.provider,
            "scopes": [scope.to_dict() for scope in self.scopes],
            "profiles": list(self.profiles),
            "docs_url": self.docs_url,
            "metadata": self.metadata,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class ScopeDecision:
    """Catalog-only policy explanation for one connector scope."""

    decision: str
    connector_id: str
    scope: str
    profile: str
    reason: str


@dataclass(frozen=True)
class AccessRequest:
    """One recorded approval request; never auto-granted."""

    request_id: str
    connector_id: str
    profile: str
    scopes: tuple[str, ...]
    status: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "request_id": self.request_id,
            "connector_id": self.connector_id,
            "profile": self.profile,
            "scopes": list(self.scopes),
            "status": self.status,
            "created_at": self.created_at,
        }


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _connectors_path(root: str | Path) -> Path:
    return Path(root) / _CONNECTORS


def _audit_path(root: str | Path) -> Path:
    return Path(root) / _AUDIT


def _audit(root: str | Path, event_type: str, profile: str, payload: dict[str, Any]) -> TraceEvent:
    return append_event(_audit_path(root), event_type, actor=profile, payload=payload)


def _load_connectors(root: str | Path) -> list[Connector]:
    path = _connectors_path(root)
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8") or "[]")
    except json.JSONDecodeError as exc:
        warnings.warn(f"_load_connectors: corrupt {path}: {exc}; treating as empty", stacklevel=2)
        return []
    if not isinstance(raw, list):
        return []
    values: list[Connector] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            values.append(Connector.from_dict(item))
        except (KeyError, TypeError) as exc:
            warnings.warn(f"_load_connectors: skipping malformed entry {item!r}: {exc}", stacklevel=2)
            continue
    return values


def _write_connectors(root: str | Path, values: list[Connector]) -> None:
    path = _connectors_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps([item.to_dict() for item in values], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _reject_secret_fields(value: Any) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if is_secret_key(str(key)):
                raise ValueError(
                    f"connector metadata must not carry secret-like field {key!r}; "
                    "the Integration Gate catalog never holds credentials"
                )
            _reject_secret_fields(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_secret_fields(item)


def add_connector(
    root: str | Path,
    connector_id: str,
    label: str | None = None,
    provider: str = "",
    scopes: tuple[dict[str, Any], ...] | list[dict[str, Any]] | None = None,
    profiles: tuple[str, ...] | list[str] | None = None,
    docs_url: str = "",
    metadata: dict[str, Any] | None = None,
) -> Connector:
    """Register one connector manifest in the local catalog."""
    scope_values = tuple(ConnectorScope.from_dict(item) for item in scopes or ())
    if not scope_values:
        raise ValueError("connectors require at least one declared scope")
    if any(not scope.name for scope in scope_values):
        raise ValueError("every connector scope requires a non-empty name")
    body = dict(metadata or {})
    _reject_secret_fields(body)
    connector = Connector(
        connector_id=connector_id,
        label=label or connector_id,
        provider=provider,
        scopes=scope_values,
        profiles=tuple(str(item) for item in profiles or ("default",)) or ("default",),
        docs_url=docs_url,
        metadata=body,
        created_at=_now_iso(),
    )
    known = [item for item in _load_connectors(root) if item.connector_id != connector.connector_id]
    known.append(connector)
    _write_connectors(root, sorted(known, key=lambda item: item.connector_id))
    return connector


def connector_trust_tier(connector: Connector) -> str:
    """Read the connector's trust tier from metadata, failing closed.

    Trust is metadata only (P1) — it never changes a gate decision here. An
    absent or unknown value normalizes to Community-Unverified.
    """
    return normalize_trust_tier(connector.metadata.get("trust_tier"))


def _is_visible(connector: Connector, profile: str) -> bool:
    return "*" in connector.profiles or profile in connector.profiles


def connectors(root: str | Path, profile: str | None = None) -> list[Connector]:
    """List configured connectors, optionally filtered by profile visibility."""
    values = _load_connectors(root)
    if profile is None:
        return values
    return [item for item in values if _is_visible(item, profile)]


def show_connector(root: str | Path, connector_id: str, profile: str = "default") -> Connector:
    """Return one connector with all scopes visible before any approval."""
    for connector in _load_connectors(root):
        if connector.connector_id != connector_id:
            continue
        if not _is_visible(connector, profile):
            _audit(root, "integration.show.denied", profile, {"connector_id": connector_id})
            raise PermissionError(f"connector {connector_id!r} is not visible to profile {profile!r}")
        return connector
    raise KeyError(f"unknown connector: {connector_id}")


def explain_policy(root: str | Path, connector_id: str, profile: str = "default", scope: str = "") -> ScopeDecision:
    """Explain (catalog-only) whether a profile may use a connector scope."""
    connector = next(
        (item for item in _load_connectors(root) if item.connector_id == connector_id),
        None,
    )
    # Hidden and unknown connectors share one reason so an unprivileged
    # profile cannot enumerate which hidden connector ids exist.
    if connector is None or not _is_visible(connector, profile):
        return ScopeDecision(
            "deny", connector_id, scope, profile, "unknown or not-visible connector fails closed"
        )
    matched = next((item for item in connector.scopes if item.name == scope), None)
    if matched is None:
        return ScopeDecision("deny", connector_id, scope, profile, "unknown scope fails closed")
    if matched.access == "read":
        return ScopeDecision("allow", connector_id, scope, profile, "read scope is allowed")
    return ScopeDecision(
        "approval_required",
        connector_id,
        scope,
        profile,
        f"{matched.access} scope requires human approval (explanation-only in v0, see ADR-001)",
    )


def request_access(
    root: str | Path,
    connector_id: str,
    profile: str = "default",
    scopes: tuple[str, ...] | list[str] = (),
) -> AccessRequest:
    """Record a pending approval request for connector scopes. Never grants."""
    connector = show_connector(root, connector_id, profile=profile)
    known_scopes = {item.name for item in connector.scopes}
    requested = tuple(str(item) for item in scopes)
    unknown = [item for item in requested if item not in known_scopes]
    if not requested or unknown:
        raise ValueError(f"request must name known scopes; unknown: {unknown or 'none requested'}")
    request = AccessRequest(
        request_id=f"req_{uuid.uuid4().hex[:12]}",
        connector_id=connector_id,
        profile=profile,
        scopes=requested,
        status="pending",
        created_at=_now_iso(),
    )
    approvals = Path(root) / _APPROVALS
    approvals.mkdir(parents=True, exist_ok=True)
    (approvals / f"{request.request_id}.json").write_text(
        json.dumps(request.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _audit(
        root,
        "integration.access.requested",
        profile,
        {"connector_id": connector_id, "request_id": request.request_id, "scopes": list(requested)},
    )
    return request


def audit_recent(root: str | Path, limit: int = 20) -> list[TraceEvent]:
    """Return recent Integration Gate audit events."""
    return recent_events(_audit_path(root), limit=limit)
