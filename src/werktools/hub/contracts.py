"""JSON-friendly contracts for the static WERK Hub foundation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from werktools.catalog import DEFAULT_TRUST_TIER, normalize_trust_tier

_TRUST_SOURCE_MAX = 64
_TRUST_NOTE_MAX = 200
_CWD_MAX = 1024


def _normalize_cwd(raw_cwd: Any) -> str | None:
    """Normalize a downstream working directory at the config boundary.

    Empty / whitespace-only -> None (inherit the hub's cwd, never spawn into a
    bogus blank directory). Length-capped like the other string fields. A
    relative path is kept as-is and resolves against the hub process's cwd when
    spawned — an honest, surfaced failure if it is wrong, not a silent one.
    """
    if raw_cwd is None:
        return None
    return str(raw_cwd).strip()[:_CWD_MAX] or None

RISK_CLASSES = ("read", "write", "destructive", "external", "secret", "unknown")
DECISIONS = ("allow", "deny", "approval_required", "hidden")
EVENT_NAMES = (
    "config.loaded",
    "tool.discovered",
    "tool.search",
    "tool.describe",
    "tool.call.requested",
    "tool.call.denied",
    "tool.call.approval_required",
    "tool.call.completed",
    "tool.call.failed",
    "approval.requested",
    "approval.resolved",
    "approval.token_consumed",
    "process.spawned",
    "process.reaped",
    "process.idle",
    "process.killed",
    "lifecycle.pool_created",
    "lifecycle.pool_hit",
    "lifecycle.pool_killed",
    "lifecycle.pid_unknown",
    "model_worker.listed",
    "model_worker.budget_checked",
    "model_worker.call.requested",
    "model_worker.call.approved",
    "model_worker.call.denied",
    "model_worker.call.completed",
    "model_worker.call.failed",
    "config.rendered",
    "config.connector.toggled",
    "config.connector.removed",
    "config.connector.added",
    "rules.exported",
    "registry.search",
    "registry.install.staged",
    "registry.install.approved",
    "skill.discover",
    "process.kill.requested",
    "process.kill.completed",
    "process.kill.failed",
    "policy.explained",
    "runtime.probed",
    "registry.allowlist.error",
    "registry.allowlist.tier_downgrade",
)


def _str_tuple(value: Any, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    if value is None:
        return default
    if isinstance(value, str):
        return (value,)
    return tuple(str(item) for item in value)


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _risk(value: Any) -> str:
    text = str(value) if value is not None else "unknown"
    if text in RISK_CLASSES:
        return text
    import warnings as _warnings
    _warnings.warn(
        f"_risk: unknown risk value {text!r} normalised to 'unknown'",
        stacklevel=3,
    )
    return "unknown"


def _decision(value: Any) -> str:
    text = str(value) if value is not None else "deny"
    return text if text in DECISIONS else "deny"


@dataclass(frozen=True)
class ToolCard:
    """Local truth for one agent-visible tool or capability."""

    id: str
    name: str
    description: str = ""
    server_id: str = "local"
    input_schema: dict[str, Any] = field(default_factory=dict)
    tags: tuple[str, ...] = ()
    risk: str = "unknown"
    read_only: bool = True
    destructive: bool = False
    requires_approval: bool = False
    source_annotations: dict[str, Any] = field(default_factory=dict)
    trust_tier: str = DEFAULT_TRUST_TIER
    trust_source: str = ""
    trust_note: str = ""
    local_notes: str | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "ToolCard":
        tool_id = str(raw["id"])
        risk = _risk(raw.get("risk"))
        return cls(
            id=tool_id,
            name=str(raw.get("name", tool_id)),
            description=str(raw.get("description", "")),
            server_id=str(raw.get("server_id", "local")),
            input_schema=_dict(raw.get("input_schema")),
            tags=_str_tuple(raw.get("tags")),
            risk=risk,
            read_only=bool(raw.get("read_only", risk == "read")),
            destructive=bool(raw.get("destructive", risk == "destructive")),
            requires_approval=bool(raw.get("requires_approval", False)),
            source_annotations=_dict(raw.get("source_annotations")),
            trust_tier=normalize_trust_tier(raw.get("trust_tier")),
            trust_source=str(raw.get("trust_source", ""))[:_TRUST_SOURCE_MAX],
            trust_note=str(raw.get("trust_note", ""))[:_TRUST_NOTE_MAX],
            local_notes=(
                str(raw["local_notes"]) if raw.get("local_notes") is not None else None
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "server_id": self.server_id,
            "input_schema": self.input_schema,
            "tags": list(self.tags),
            "risk": self.risk,
            "read_only": self.read_only,
            "destructive": self.destructive,
            "requires_approval": self.requires_approval,
            "source_annotations": self.source_annotations,
            "trust_tier": self.trust_tier,
            "trust_source": self.trust_source,
            "trust_note": self.trust_note,
        }
        if self.local_notes is not None:
            body["local_notes"] = self.local_notes
        return body


@dataclass(frozen=True)
class HubProfile:
    """Local policy lens for one agent or human profile."""

    id: str
    label: str | None = None
    purpose: str = ""
    permission_profile: str = "cautious"
    visible_tags: tuple[str, ...] = ("read",)
    hidden_tools: tuple[str, ...] = ()
    allowed_tools: tuple[str, ...] = ()
    allowed_servers: tuple[str, ...] = ()
    approval_mode: str = "manual"
    ledger_level: str = "standard"

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "HubProfile":
        profile_id = str(raw["id"])
        return cls(
            id=profile_id,
            label=str(raw["label"]) if raw.get("label") is not None else None,
            purpose=str(raw.get("purpose", "")),
            permission_profile=str(raw.get("permission_profile", "cautious")),
            visible_tags=_str_tuple(raw.get("visible_tags"), default=("read",)),
            hidden_tools=_str_tuple(raw.get("hidden_tools")),
            allowed_tools=_str_tuple(raw.get("allowed_tools")),
            allowed_servers=_str_tuple(raw.get("allowed_servers")),
            approval_mode=str(raw.get("approval_mode", "manual")),
            ledger_level=str(raw.get("ledger_level", "standard")),
        )

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "id": self.id,
            "purpose": self.purpose,
            "permission_profile": self.permission_profile,
            "visible_tags": list(self.visible_tags),
            "hidden_tools": list(self.hidden_tools),
            "allowed_tools": list(self.allowed_tools),
            "allowed_servers": list(self.allowed_servers),
            "approval_mode": self.approval_mode,
            "ledger_level": self.ledger_level,
        }
        if self.label is not None:
            body["label"] = self.label
        return body


TRANSPORTS = ("stdio", "http", "sse", "ws")


@dataclass(frozen=True)
class DownstreamServer:
    """One configured downstream MCP server behind the hub.

    stdio servers use command/args; http/sse/ws servers use url + headers.
    The dataclass holds dict fields (headers/env) so instances are
    unhashable — hashing raises TypeError — matching the ToolCard precedent.
    """

    id: str
    command: str = ""
    args: tuple[str, ...] = ()
    enabled: bool = True
    transport: str = "stdio"
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    trust_tier: str = DEFAULT_TRUST_TIER
    trust_source: str = ""
    trust_note: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "DownstreamServer":
        transport = str(raw.get("transport", "stdio"))
        if transport not in TRANSPORTS:
            transport = "stdio"
        env = {str(k): str(v) for k, v in (raw.get("env") or {}).items()}
        from werktools.redaction import is_secret_key

        for key in env:
            if is_secret_key(key):
                raise ValueError(
                    f"downstream server env must not carry secret-like key {key!r}; "
                    "pass secrets through the hub process environment instead"
                )
        url = raw.get("url")
        if transport in ("http", "sse", "ws") and not (url or "").strip():
            raise ValueError(
                f"downstream server {raw['id']!r} uses transport {transport!r} but has no url"
            )
        return cls(
            id=str(raw["id"]),
            command=str(raw.get("command", "")),
            args=_str_tuple(raw.get("args")),
            enabled=bool(raw.get("enabled", True)),
            transport=transport,
            url=str(url) if url is not None else None,
            headers={str(k): str(v) for k, v in (raw.get("headers") or {}).items()},
            env=env,
            cwd=_normalize_cwd(raw.get("cwd")),
            trust_tier=normalize_trust_tier(raw.get("trust_tier")),
            trust_source=str(raw.get("trust_source", ""))[:_TRUST_SOURCE_MAX],
            trust_note=str(raw.get("trust_note", ""))[:_TRUST_NOTE_MAX],
        )

    def to_dict(self, redact: bool = False) -> dict[str, Any]:
        """Serialize the server. ``redact=True`` masks http/sse auth headers,
        env values, and secret-looking command args for trace/inspection
        surfaces; the default (``redact=False``) keeps the real secrets
        because a host config physically needs them to connect.
        """
        from werktools.redaction import REDACTED, is_secret_key, mask_secret_text

        args = [mask_secret_text(a) for a in self.args] if redact else list(self.args)
        body: dict[str, Any] = {
            "id": self.id,
            "command": mask_secret_text(self.command) if redact else self.command,
            "args": args,
            "enabled": self.enabled,
            "transport": self.transport,
            "trust_tier": self.trust_tier,
            "trust_source": self.trust_source,
            "trust_note": self.trust_note,
        }
        if self.url is not None:
            body["url"] = self.url
        if self.cwd is not None:
            body["cwd"] = mask_secret_text(self.cwd) if redact else self.cwd
        if self.headers:
            body["headers"] = (
                {k: (REDACTED if is_secret_key(k) else v) for k, v in self.headers.items()}
                if redact
                else dict(self.headers)
            )
        if self.env:
            body["env"] = ({k: REDACTED for k in self.env} if redact else dict(self.env))
        return body


@dataclass(frozen=True)
class RegistryPackage:
    """One installable package from an MCP registry server entry."""

    name: str
    registry_type: str = ""
    version: str = ""

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "RegistryPackage":
        return cls(
            name=str(raw.get("identifier", raw.get("name", ""))),
            registry_type=str(raw.get("registryType", raw.get("registry_type", ""))),
            version=str(raw.get("version", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "registry_type": self.registry_type, "version": self.version}


@dataclass(frozen=True)
class RegistryCandidate:
    """One server entry returned from an MCP registry search."""

    id: str
    name: str
    description: str
    source_url: str
    packages: tuple[RegistryPackage, ...]
    registry_version: str
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "RegistryCandidate":
        import re

        name = str(raw.get("name", ""))
        raw_id = str(raw.get("id", name))
        sanitized = re.sub(r"[^a-zA-Z0-9_-]", "-", raw_id)[:64] or "server"
        packages = tuple(
            RegistryPackage.from_dict(p) for p in raw.get("packages", []) if isinstance(p, dict)
        )
        raw_meta = raw.get("metadata", {})
        metadata = (
            {
                k: (str(v)[:256] if isinstance(v, str) else v)
                for k, v in raw_meta.items()
                if not isinstance(v, (dict, list))
            }
            if isinstance(raw_meta, dict)
            else {}
        )
        return cls(
            id=sanitized,
            # Cap untrusted external fields at safe lengths (SF: untrusted registry data).
            name=name[:128],
            description=str(raw.get("description", ""))[:512],
            source_url=(
                str(raw.get("repository", {}).get("url", ""))
                if isinstance(raw.get("repository"), dict)
                else str(raw.get("source_url", ""))
            )[:256],
            packages=packages,
            registry_version=str(raw.get("version", ""))[:32],
            metadata=metadata,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "source_url": self.source_url,
            "packages": [p.to_dict() for p in self.packages],
            "registry_version": self.registry_version,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class HubConfig:
    """Static WERK Hub config loaded from a local JSON file."""

    name: str = "werk-hub"
    default_profile: str = "codex-builder"
    ledger_path: str = ".werktools/hub-ledger.jsonl"
    # Optional Tier-1 allowlist override file (resolved like ledger_path,
    # relative to CWD). Absent file -> the embedded curated seed is used.
    tier1_allowlist_path: str = ".werktools/tier1_allowlist.json"
    profiles: tuple[HubProfile, ...] = ()
    tools: tuple[ToolCard, ...] = ()
    servers: tuple[DownstreamServer, ...] = ()

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "HubConfig":
        profiles = tuple(
            HubProfile.from_dict(item)
            for item in raw.get("profiles", ())
            if isinstance(item, dict)
        )
        tools = tuple(
            ToolCard.from_dict(item)
            for item in raw.get("tools", ())
            if isinstance(item, dict)
        )
        servers = tuple(
            DownstreamServer.from_dict(item)
            for item in raw.get("servers", ())
            if isinstance(item, dict)
        )
        return cls(
            name=str(raw.get("name", "werk-hub")),
            default_profile=str(raw.get("default_profile", "codex-builder")),
            ledger_path=str(raw.get("ledger_path", ".werktools/hub-ledger.jsonl")),
            tier1_allowlist_path=str(
                raw.get("tier1_allowlist_path", ".werktools/tier1_allowlist.json")
            ),
            profiles=profiles,
            tools=tools,
            servers=servers,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "default_profile": self.default_profile,
            "ledger_path": self.ledger_path,
            "tier1_allowlist_path": self.tier1_allowlist_path,
            "profiles": [profile.to_dict() for profile in self.profiles],
            "tools": [tool.to_dict() for tool in self.tools],
            "servers": [server.to_dict() for server in self.servers],
        }


@dataclass(frozen=True)
class PolicyDecision:
    """Explainable local decision for a profile/tool pair."""

    decision: str
    tool_id: str
    profile_id: str
    reason: str
    risk: str = "unknown"
    requires_approval: bool = False
    visible: bool = True
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.decision == "allow"

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "decision": _decision(self.decision),
            "tool_id": self.tool_id,
            "profile_id": self.profile_id,
            "reason": self.reason,
            "risk": _risk(self.risk),
            "requires_approval": self.requires_approval,
            "visible": self.visible,
            "meta": self.meta,
        }
