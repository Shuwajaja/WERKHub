"""Read-only downstream MCP relay for the werk-hub server.

Discovers tools on configured downstream stdio MCP servers and normalizes
them into ToolCards. The keyword classifier only TIGHTENS risk and a
``destructiveHint`` only tightens it; a downstream-controlled
``readOnlyHint`` may, in the ABSENCE of mutating vocabulary, yield a read
CLASSIFICATION, but that classification alone never grants a token-free
forward. The single auto-forward (no-approval) path lives in
``server.py`` and fires only for tools the OPERATOR config-pinned as read;
a merely self-declared read-only downstream tool requires an approval
token. Requires the optional ``werktools[server]`` extra (FastMCP).
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

try:
    from fastmcp import Client
except ImportError as exc:  # pragma: no cover - exercised via werktools.server
    raise ImportError(
        "werktools.hub.relay requires FastMCP. Install it with: pip install werktools[server]"
    ) from exc

from werktools.classify import classify_tool

from .contracts import DownstreamServer, ToolCard

_SEVERITY = {"read": 0, "external": 1, "write": 2, "secret": 3, "destructive": 4}
_ANNOTATION_KEYS = ("readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint")

# Discovery and forwarding must never hang the hub on a stalled downstream.
DISCOVERY_TIMEOUT = 10.0
CALL_TIMEOUT = 60.0

# Mutating verbs the keyword classifier does not cover. A downstream cannot
# downgrade these to read merely by setting readOnlyHint=True: classification
# may only TIGHTEN. Conjugations are matched with a trailing-form regex.
_MUTATING_VERBS = (
    "clear",
    "flush",
    "compact",
    "archive",
    "prune",
    "reset",
    "purge",
    "wipe",
    "zap",
    "trim",
    "expire",
    "invalidate",
    "sweep",
    "drop",
    "revoke",
    "rotate",
    "migrate",
    "rebuild",
    "rollback",
    "restore",
    "evict",
)
_MUTATING_RE = re.compile(
    r"\b(?:" + "|".join(_MUTATING_VERBS) + r")(?:s|d|ed|es|ing)?\b",
    re.IGNORECASE,
)


def transport_for(server: DownstreamServer) -> dict[str, Any]:
    """Return a FastMCP client config for a downstream server.

    stdio servers spawn a subprocess; http/sse servers connect to server.url.
    Consumed only by server.py's relay path.
    """
    if server.transport == "stdio":
        spec: dict[str, Any] = {"command": server.command, "args": list(server.args)}
        if server.env:
            spec["env"] = dict(server.env)
        if server.cwd:
            spec["cwd"] = server.cwd
        return {"mcpServers": {server.id: spec}}
    if not server.url:
        raise ValueError(f"downstream server {server.id!r} uses {server.transport!r} but has no url")
    remote: dict[str, Any] = {"url": server.url}
    if server.headers:
        remote["headers"] = dict(server.headers)
    return {"mcpServers": {server.id: remote}}


# Diagnostic stderr probe: the FastMCP Client raises a generic "Failed to
# initialize server session" that HIDES the downstream's own stderr (e.g. a
# Windows "Zugriff verweigert"/access-denied or a missing-dependency trace).
# This best-effort helper spawns the stdio downstream directly, nudges it with a
# minimal initialize, and returns the secret-masked tail of its stderr so the
# operator can see WHY discovery failed without a manual probe. Used ONLY on the
# discovery error path, never the happy path.
_DIAG_TIMEOUT = 3.0
_DIAG_MAX_LINES = 12
# Hard ceiling on captured stderr before any line processing, so a downstream
# that floods stderr cannot balloon hub memory (capture reads it all first).
_DIAG_MAX_BYTES = 16_384


def collect_startup_stderr(
    server: DownstreamServer,
    *,
    timeout: float = _DIAG_TIMEOUT,
    max_lines: int = _DIAG_MAX_LINES,
) -> str:
    """Return the secret-masked tail of a stdio downstream's startup stderr.

    Returns "" for non-stdio servers, servers without a command, or a clean
    process that emitted nothing; a marker string when the probe timed out with
    no stderr; the spawn error for an un-spawnable command. Never raises —
    diagnostics must not mask the original discovery error.

    NOTE: this is a real (third) spawn of an operator-configured command, on the
    error path only. For a single-instance MCP that holds an exclusive lock, the
    probe's own access-denied IS the useful signal (it confirms the lock theory),
    so surfacing it is intended, not a defect. Reads stderr in BYTES and decodes
    utf-8/replace so a non-locale-encoded downstream cannot raise here (a
    text=True pipe would UnicodeDecodeError on cp1252 hosts and break the
    never-raises contract). Env mirrors a real client spawn (full environment +
    server.env overrides) for diagnostic fidelity; the byte cap + secret-mask
    bound any leakage if a downstream echoes secrets to stderr.
    """
    import json
    import os
    import subprocess

    from werktools.redaction import mask_secret_text

    if server.transport != "stdio" or not server.command:
        return ""
    spawn_env = {**os.environ, **server.env} if server.env else None
    init_line = (
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "werk-hub-probe", "version": "0"},
                },
            }
        )
        + "\n"
    ).encode("utf-8")
    effective_timeout = max(0.1, timeout)
    timed_out = False
    raw_err = b""
    try:
        completed = subprocess.run(  # noqa: S603 - operator-configured command
            [server.command, *server.args],
            input=init_line,
            capture_output=True,
            text=False,
            cwd=server.cwd or None,
            env=spawn_env,
            timeout=effective_timeout,
        )
        raw_err = completed.stderr or b""
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        raw_err = exc.stderr if isinstance(exc.stderr, bytes) else b""
    except OSError as exc:
        return mask_secret_text(f"spawn failed: {type(exc).__name__}: {exc}")
    # Keep the TAIL on overflow (not the head): the final selector below takes
    # the LAST max_lines, because a failure's conclusion lands at the end of
    # stderr (live: codebase-memory-mcp's "Zugriff verweigert" was the last
    # line). Head-capping would defeat that last-lines selection.
    if len(raw_err) > _DIAG_MAX_BYTES:
        raw_err = raw_err[-_DIAG_MAX_BYTES:]
    stderr_text = raw_err.decode("utf-8", "replace")
    lines = [line for line in stderr_text.splitlines() if line.strip()]
    tail = lines[-max_lines:] if max_lines > 0 else []
    if tail:
        return mask_secret_text("\n".join(tail))
    # Honest-degrade: a process that hung silently leaves no stderr; say so
    # rather than returning "" (which reads as "nothing to report").
    if timed_out:
        return f"(probe timed out after {effective_timeout:g}s with no stderr output)"
    return ""


def _tighten(base: str, candidate: str) -> str:
    return candidate if _SEVERITY.get(candidate, 0) > _SEVERITY.get(base, 0) else base


def _risk_for(name: str, description: str, schema: dict[str, Any], annotations: dict[str, Any]) -> str:
    # Conservative baseline: assume write. readOnlyHint is treated as a hint
    # for CLASSIFICATION only (it can yield a read tag when no mutating
    # vocabulary is present), never a forward grant: server.py auto-forwards a
    # relayed tool token-free only when the OPERATOR config-pinned it as read,
    # so a downstream self-declaring read-only cannot reach the no-approval
    # path on its own.
    read_only = annotations.get("readOnlyHint") is True
    blob = f"{name} {description}"
    looks_mutating = bool(_MUTATING_RE.search(blob))
    risk = "read" if (read_only and not looks_mutating) else "write"
    if annotations.get("destructiveHint"):
        risk = _tighten(risk, "destructive")
    if annotations.get("openWorldHint") and not read_only:
        risk = _tighten(risk, "external")
    signals = classify_tool({"name": name, "description": description, "inputSchema": schema}).get(
        "signals", []
    )
    if "secret/credential" in signals:
        risk = _tighten(risk, "secret")
    if "shell/exec" in signals or "injection-phrasing" in signals:
        risk = _tighten(risk, "destructive")
    if "fs-write/delete/destructive" in signals:
        risk = _tighten(risk, "write")
    if "network/fetch/url" in signals:
        risk = _tighten(risk, "external")
    return risk


async def _list_tools(target: Any) -> list[Any]:
    async with Client(target, init_timeout=DISCOVERY_TIMEOUT) as client:
        try:
            return list(await asyncio.wait_for(client.list_tools(), timeout=DISCOVERY_TIMEOUT))
        except (asyncio.TimeoutError, TimeoutError) as exc:
            raise TimeoutError(
                f"downstream discovery timed out after {DISCOVERY_TIMEOUT:g}s"
            ) from exc


def discover_tools(server_id: str, target: Any) -> tuple[ToolCard, ...]:
    """List downstream tools and normalize them into hub ToolCards."""
    cards: list[ToolCard] = []
    for tool in asyncio.run(_list_tools(target)):
        name = str(tool.name)
        description = str(tool.description or "")
        schema = dict(getattr(tool, "inputSchema", None) or {})
        raw_annotations = getattr(tool, "annotations", None)
        annotations = {
            key: getattr(raw_annotations, key, None) for key in _ANNOTATION_KEYS
        } if raw_annotations is not None else {}
        risk = _risk_for(name, description, schema, annotations)
        cards.append(
            ToolCard(
                id=f"{server_id}.{name}",
                name=name,
                description=description,
                server_id=server_id,
                input_schema=schema,
                tags=(risk,),
                risk=risk,
                read_only=risk == "read",
                destructive=risk == "destructive",
                source_annotations={key: value for key, value in annotations.items() if value is not None},
            )
        )
    return tuple(cards)


async def _call_tool(target: Any, tool_name: str, args: dict[str, Any]) -> Any:
    async with Client(target, init_timeout=DISCOVERY_TIMEOUT) as client:
        try:
            result = await asyncio.wait_for(client.call_tool(tool_name, args), timeout=CALL_TIMEOUT)
        except (asyncio.TimeoutError, TimeoutError) as exc:
            # str(TimeoutError()) is "", which would surface as an empty
            # error message + empty ledger reason. Raise an honest message.
            raise TimeoutError(
                f"downstream call to {tool_name!r} timed out after {CALL_TIMEOUT:g}s"
            ) from exc
        data = getattr(result, "data", None)
        if data is not None:
            return data
        content = getattr(result, "content", []) or []
        texts = [getattr(item, "text", None) for item in content]
        return [text for text in texts if text is not None]


def call_downstream(target: Any, tool_name: str, args: dict[str, Any]) -> Any:
    """Forward one call to a downstream MCP server and return its payload."""
    return asyncio.run(_call_tool(target, tool_name, args))


# NOTE: there is intentionally no pooled forwarding path here. `serve` spawns a
# fresh downstream subprocess per call via `call_downstream` above; the warm
# pool (hub/pool.py) is NOT wired into the relay and `_target_pid` would always
# be None for in-process and command-spec targets, so a pooled path could only
# fake a PID it can never reap. Real downstream lifecycle (PID tracking + idle/
# orphan reaping) lives in hub/lifecycle.py.
