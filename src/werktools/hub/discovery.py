"""Browse + connect: the Official MCP Registry → catalog → approve → connect.

`search_registry` queries the Official MCP Registry and never raises — any
network/JSON/shape error returns ([], [warning]). Install is a two-step
explicit human approval: `stage_install` records a pending connector
(governance catalog, connectors.json) and `approve_and_write` is the ADR-002
one-way bridge that, on approval, writes a DownstreamServer into hub.json.

Stdlib only (urllib at module scope, importable without FastMCP). No daemon.
"""

from __future__ import annotations

import json
import re
import urllib.request
import warnings
from pathlib import Path
from typing import Any, Callable

from werktools.catalog import normalize_trust_tier

from .allowlist import (
    ALLOWLIST_SCHEMA,
    Tier1Allowlist,
    default_allowlist,
    is_tier1,
    load_tier1_allowlist,
    pin_digest,
    tier1_trust_fields,
)
from .contracts import DownstreamServer, RegistryCandidate
from .ledger import record_event

REGISTRY_BASE = "https://registry.modelcontextprotocol.io/v0.1"
FETCH_TIMEOUT = 10.0
_UNVETTED_STAGE = "[UNVETTED] not on the Tier-1 allowlist; human approval required."
_UNVETTED_APPROVE = "[UNVETTED] registry-origin server; not on Tier-1 allowlist"
_EMPTY_ALLOWLIST = Tier1Allowlist(schema=ALLOWLIST_SCHEMA, pinned_at="", entries=())


def _resolve_allowlist(
    allowlist_path: str | Path | None,
    ledger_path: str | Path | None,
) -> Tier1Allowlist | None:
    """Resolve the Tier-1 allowlist for the install gate.

    - ``None`` path: the gate is disabled (backward-compatible default).
    - missing file: use the embedded curated baseline (absence is normal).
    - present + valid: use the operator's override.
    - present + invalid: emit ``registry.allowlist.error`` and FAIL CLOSED to an
      empty allowlist so a corrupt override can never promote anything.
    """
    if allowlist_path is None:
        return None
    path = Path(allowlist_path)
    if not path.exists():
        return default_allowlist()
    try:
        return load_tier1_allowlist(path)
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        warnings.warn(
            f"_resolve_allowlist: corrupt allowlist at {path!r}: {exc}; failing closed to empty allowlist",
            stacklevel=3,
        )
        if ledger_path is not None:
            record_event(
                ledger_path,
                "registry.allowlist.error",
                {"path": str(path), "error": str(exc)[:200]},
            )
        return _EMPTY_ALLOWLIST


def _real_http_get(url: str) -> dict[str, Any]:
    with urllib.request.urlopen(url, timeout=FETCH_TIMEOUT) as resp:  # noqa: S310 - https registry
        return json.loads(resp.read(4 * 1024 * 1024).decode("utf-8"))


def search_registry(
    query: str = "",
    limit: int = 20,
    http_get: Callable[[str], dict[str, Any]] | None = None,
) -> tuple[list[RegistryCandidate], list[str]]:
    """Return (candidates, warnings). Never raises."""
    if limit <= 0:
        return [], [f"search_registry: limit must be >= 1, got {limit}"]
    getter = http_get or _real_http_get
    registry_warnings: list[str] = []
    url = f"{REGISTRY_BASE}/servers"
    try:
        body = getter(url)
    except Exception as exc:  # network, timeout, decode — all non-fatal
        return [], [f"registry fetch failed: {exc}"]
    if not isinstance(body, dict) or "servers" not in body:
        return [], ["registry response missing 'servers'"]
    servers_raw = body.get("servers", [])
    if not isinstance(servers_raw, list):
        return [], [f"registry response 'servers' is not a list: {type(servers_raw).__name__}"]
    candidates: list[RegistryCandidate] = []
    needle = query.lower()
    for raw in servers_raw:
        if not isinstance(raw, dict):
            continue
        try:
            candidate = RegistryCandidate.from_dict(raw)
        except (KeyError, TypeError, ValueError) as exc:
            registry_warnings.append(f"skipped malformed entry: {exc}")
            continue
        if needle and needle not in f"{candidate.name} {candidate.description}".lower():
            continue
        candidates.append(candidate)
        if len(candidates) >= limit:
            break
    return candidates, registry_warnings


_NPM_NAME_RE = re.compile(r"^(@[a-zA-Z0-9][a-zA-Z0-9_-]*/)?[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
_PYPI_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
# OCI/docker: only the official Docker MCP namespace is auto-installable.
# Operators can always add arbitrary images manually to hub.json.
_OCI_NAME_RE = re.compile(r"^mcp/[a-zA-Z0-9_.-]+(?::[a-zA-Z0-9_.\-]+)?$")


def candidate_to_downstream(candidate: RegistryCandidate) -> DownstreamServer | None:
    """Map the first package to a runnable stdio DownstreamServer, or None."""
    if not candidate.packages:
        return None
    pkg = candidate.packages[0]
    rtype = pkg.registry_type.lower()
    command: str
    args: tuple[str, ...]
    if rtype in ("npm", "node"):
        if not _NPM_NAME_RE.match(pkg.name):
            return None
        command, args = "npx", ("-y", pkg.name)
    elif rtype in ("pypi", "python"):
        if not _PYPI_NAME_RE.match(pkg.name):
            return None
        command, args = "uvx", (pkg.name,)
    elif rtype in ("oci", "docker"):
        if not _OCI_NAME_RE.match(pkg.name):
            return None
        command, args = "docker", ("run", "--rm", "-i", pkg.name)
    else:
        return None
    return DownstreamServer(id=candidate.id, command=command, args=args)


def stage_install(
    gate_root: str | Path,
    candidate: RegistryCandidate,
    profile: str = "default",
    hub_ledger_path: str | Path | None = None,
    allowlist_path: str | Path | None = None,
):
    """Record a pending connector for an install (no hub.json mutation).

    When ``allowlist_path`` is given, the candidate's trust tier is decided
    against the Tier-1 allowlist (deny-by-default) and recorded in the connector
    metadata so ``approve_and_write`` can re-check it. The registry stays
    discovery-only — staging never mutates hub.json.
    """
    from ..tools.integration_gate import add_connector, request_access

    metadata: dict[str, Any] = {
        "packages_json": json.dumps([p.to_dict() for p in candidate.packages]),
        "name": candidate.name,
        "description": candidate.description,
    }
    allowlist = _resolve_allowlist(allowlist_path, hub_ledger_path)
    if allowlist is not None:
        entry = is_tier1(allowlist, candidate.id)
        if entry is not None:
            metadata.update(tier1_trust_fields(entry))
        else:
            metadata["trust_tier"] = "Community-Unverified"
            metadata["trust_source"] = "registry"
            metadata["trust_note"] = _UNVETTED_STAGE

    add_connector(
        gate_root,
        candidate.id,
        label=candidate.name,
        provider="mcp-registry",
        scopes=({"name": "install", "access": "read", "description": "install this MCP server"},),
        profiles=(profile,),
        docs_url=candidate.source_url,
        metadata=metadata,
    )
    request = request_access(gate_root, candidate.id, profile=profile, scopes=("install",))
    if hub_ledger_path is not None:
        record_event(
            hub_ledger_path,
            "registry.install.staged",
            {"request_id": request.request_id, "server_id": candidate.id},
        )
    return request


def _candidate_from_connector(connector) -> RegistryCandidate:
    try:
        packages = json.loads(connector.metadata.get("packages_json", "[]"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"connector {connector.connector_id!r} has corrupt packages_json: {exc}") from exc
    return RegistryCandidate.from_dict(
        {
            "id": connector.connector_id,
            "name": connector.metadata.get("name", connector.connector_id),
            "description": connector.metadata.get("description", ""),
            "packages": packages,
        }
    )


def _apply_trust(
    server: DownstreamServer,
    connector,
    allowlist_path: str | Path | None,
    ledger_path: str | Path | None,
) -> DownstreamServer:
    """Stamp trust metadata on the server from the Tier-1 allowlist.

    Re-checks membership at approve time. A server staged as Tier-1 that no
    longer matches is downgraded to Community-Unverified and a
    ``registry.allowlist.tier_downgrade`` event fires (guards stage→approve
    drift). Tier-1 OCI entries with a real digest get their image pinned.
    """
    import dataclasses

    allowlist = _resolve_allowlist(allowlist_path, ledger_path)
    if allowlist is None:
        # No gate configured: still honor whatever trust stage_install recorded
        # on the connector instead of silently resetting to the bare default.
        return dataclasses.replace(
            server,
            trust_tier=normalize_trust_tier(connector.metadata.get("trust_tier")),
            trust_source=str(connector.metadata.get("trust_source", "")),
            trust_note=str(connector.metadata.get("trust_note", "")),
        )
    entry = is_tier1(allowlist, connector.connector_id)
    if entry is not None:
        fields = tier1_trust_fields(entry)
        args = server.args
        if entry.image_digest:
            if server.command == "docker" and args:
                args = args[:-1] + (pin_digest(args[-1], entry.image_digest),)
            else:
                warnings.warn(
                    f"_apply_trust: {connector.connector_id!r} has image_digest but command is "
                    f"{server.command!r} (not 'docker'); digest pin was NOT applied to args",
                    stacklevel=3,
                )
        return dataclasses.replace(
            server,
            args=args,
            trust_tier=fields["trust_tier"],
            trust_source=fields["trust_source"],
            trust_note=fields["trust_note"],
        )
    staged_tier = str(connector.metadata.get("trust_tier", "Community-Unverified"))
    if staged_tier in ("Official", "Security-Scanned"):
        # Always warn (honest-degrade) so the operator sees the downgrade even
        # when no ledger_path is wired (e.g. in tests or offline mode).
        warnings.warn(
            f"_apply_trust: {connector.connector_id!r} staged as {staged_tier!r} "
            "but is no longer on the Tier-1 allowlist; downgrading to Community-Unverified",
            stacklevel=3,
        )
        if ledger_path is not None:
            record_event(
                ledger_path,
                "registry.allowlist.tier_downgrade",
                {"server_id": connector.connector_id, "staged_tier": staged_tier},
            )
    return dataclasses.replace(
        server,
        trust_tier="Community-Unverified",
        trust_source="registry",
        trust_note=_UNVETTED_APPROVE,
    )


def approve_and_write(
    gate_root: str | Path,
    request_id: str,
    hub_config_path: str | Path,
    ledger_path: str | Path | None = None,
    allowlist_path: str | Path | None = None,
) -> DownstreamServer:
    """Approve a staged install and write the server into hub.json (atomic).

    The hub.json write happens BEFORE the request is marked approved, so a
    failed write leaves the request pending and hub.json untouched. When
    ``allowlist_path`` is given, the server is tier-stamped (deny-by-default)
    before the write.
    """
    from pathlib import Path as _Path

    from ..tools.integration_gate import connectors as load_connectors
    from .registry import load_config, save_config

    if not re.fullmatch(r"req_[0-9a-f]{12}", request_id):
        raise ValueError("invalid request_id")
    approval_path = _Path(gate_root) / "approvals" / f"{request_id}.json"
    try:
        approval_path.resolve().relative_to((_Path(gate_root) / "approvals").resolve())
    except ValueError:
        raise ValueError("invalid request_id")
    if not approval_path.exists():
        raise ValueError(f"unknown install request: {request_id}")
    approval = json.loads(approval_path.read_text(encoding="utf-8"))
    if approval.get("status") != "pending":
        raise ValueError(f"request {request_id} is {approval.get('status')}, not pending")

    connector = next((c for c in load_connectors(gate_root) if c.connector_id == approval["connector_id"]), None)
    if connector is None:
        raise ValueError(f"connector {approval['connector_id']} not found")
    server = candidate_to_downstream(_candidate_from_connector(connector))
    if server is None:
        raise ValueError(f"connector {connector.connector_id} has no installable package")

    server = _apply_trust(server, connector, allowlist_path, ledger_path)

    config = load_config(hub_config_path) if _Path(hub_config_path).exists() else load_config({"name": "werk-hub"})
    if any(s.id == server.id for s in config.servers):
        # Already connected — approve the request but do not duplicate. If trust
        # was downgraded by _apply_trust, persist the downgraded fields so hub.json
        # reflects the current tier and the stale elevated trust is not retained.
        import dataclasses as _dc

        persisted = next(s for s in config.servers if s.id == server.id)
        if (persisted.trust_tier, persisted.trust_source, persisted.trust_note) != (
            server.trust_tier,
            server.trust_source,
            server.trust_note,
        ):
            # _apply_trust may have emitted a tier_downgrade event; persist all
            # three trust fields so security advisories in trust_note are not
            # silently dropped when the tier itself has not changed.
            updated_servers = tuple(
                _dc.replace(s, trust_tier=server.trust_tier, trust_source=server.trust_source, trust_note=server.trust_note)
                if s.id == server.id
                else s
                for s in config.servers
            )
            merged = _dc.replace(config, servers=updated_servers)
            server = next(s for s in merged.servers if s.id == server.id)
        else:
            merged = config
            server = persisted
    else:
        import dataclasses as _dc

        merged = _dc.replace(config, servers=config.servers + (server,))
    save_config(hub_config_path, merged)

    approval["status"] = "approved"
    tmp = approval_path.with_name(approval_path.name + ".tmp")
    tmp.write_text(json.dumps(approval, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    import os

    os.replace(tmp, approval_path)

    if ledger_path is not None:
        record_event(ledger_path, "registry.install.approved", {"request_id": request_id, "server_id": server.id})
    return server
