"""Live werk-hub MCP server: the bridge tools over the static hub.

This is the single host-facing MCP entry from SPEC_MCP_WERK_HUB.md. Agents
discover via tool_search -> tool_describe -> tool_call; every call passes
the ADR-001 fail-closed gate (hub.policy.enforce) and is ledgered. The
module requires the optional ``werktools[server]`` extra (FastMCP).
"""

from __future__ import annotations

import warnings
from pathlib import Path
from typing import Any, Callable

from werktools.envelope import err, ok
from werktools.redaction import mask_secret_text
from werktools.server import make_server, register

from . import relay
from .approvals import consume_token, hash_call_args, list_records, load_record, request_approval
from .contracts import HubConfig, ToolCard
from .diagnose import classify_discovery_failure
from .ledger import recent_events_verified, record_event
from .policy import enforce, explain
from .registry import get_profile, get_tool, visible_tools
from .status import hub_status as status_snapshot

SERVER_NAME = "werk-hub"

Handler = Callable[[dict[str, Any]], Any]

_SEARCH_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string"},
        "limit": {"type": "integer", "minimum": 1, "maximum": 200},
    },
    "required": [],
}
_DESCRIBE_SCHEMA = {
    "type": "object",
    "properties": {"tool_id": {"type": "string"}},
    "required": ["tool_id"],
}
_CALL_SCHEMA = {
    "type": "object",
    "properties": {
        "tool_id": {"type": "string"},
        "args": {"type": "object"},
        "_approval_request_id": {"type": "string"},
        "_approval_token": {"type": "string"},
    },
    "required": ["tool_id"],
}
_EMPTY_SCHEMA = {"type": "object", "properties": {}, "required": []}
_LEDGER_SCHEMA = {
    "type": "object",
    "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 200}},
    "required": [],
}
_APPROVAL_SCHEMA = {
    "type": "object",
    "properties": {"request_id": {"type": "string"}},
    "required": [],
}
_WORKER_SCHEMA = {
    "type": "object",
    "properties": {"worker": {"type": "string"}},
    "required": ["worker"],
}
_WORKER_CALL_SCHEMA = {
    "type": "object",
    "properties": {
        "worker": {"type": "string"},
        "model": {"type": "string"},
        "prompt": {"type": "string"},
        "_approval_request_id": {"type": "string"},
        "_approval_token": {"type": "string"},
    },
    "required": ["worker", "model", "prompt"],
}

_WORKER_CARDS = [
    {"id": "model_worker_list", "name": "model_worker_list", "risk": "read", "read_only": True, "tags": ["worker", "read"]},
    {"id": "model_worker_budget_check", "name": "model_worker_budget_check", "risk": "read", "read_only": True, "tags": ["worker", "read"]},
    {"id": "model_worker_call", "name": "model_worker_call", "risk": "external", "read_only": False, "requires_approval": True, "tags": ["worker", "external"]},
    {"id": "model_worker_report", "name": "model_worker_report", "risk": "read", "read_only": True, "tags": ["worker", "read"]},
]


def _load_workers(workers_manifest_path: str | Path | None):
    if workers_manifest_path is None:
        return []
    from .workers import load_workers

    return load_workers(workers_manifest_path)


def _with_worker_cards(config: HubConfig) -> HubConfig:
    known = {tool.id for tool in config.tools}
    merged = config.to_dict()
    merged["tools"] = [tool.to_dict() for tool in config.tools] + [c for c in _WORKER_CARDS if c["id"] not in known]
    return HubConfig.from_dict(merged)


def build_hub_server(
    config: HubConfig,
    profile_id: str | None = None,
    handlers: dict[str, Handler] | None = None,
    ledger_path: str | Path | None = None,
    relay_targets: dict[str, Any] | None = None,
    approvals_dir: str | Path | None = None,
    pool: Any = None,
    status_port: int | None = None,
    workers_manifest_path: str | Path | None = None,
    registry_http_get: Any = None,
):
    """Build the werk-hub FastMCP server with the profile pinned at launch.

    Downstream servers are discovered once at build time and only for the
    active profile's allowed_servers; discovered tools merge into the
    config as ToolCards so the normal policy gate applies to them.
    """
    profile = get_profile(config, profile_id)
    local_handlers: dict[str, Handler] = dict(handlers or {})
    ledger = Path(ledger_path or config.ledger_path)
    approvals = Path(approvals_dir) if approvals_dir is not None else ledger.parent / "hub-approvals"

    targets: dict[str, Any] = dict(relay_targets or {})
    if not targets:
        for downstream in config.servers:
            if downstream.enabled:
                targets[downstream.id] = relay.transport_for(downstream)
    allowed_server_ids = set(profile.allowed_servers)
    relayed_ids: set[str] = set()
    # Only operator-config-pinned read cards may auto-forward token-free. A tool
    # discovered with a downstream-controlled readOnlyHint is NOT trusted: it
    # requires an approval token like any non-read relayed tool.
    relayed_read_ids: set[str] = set()
    discovered: list[ToolCard] = []
    config_pinned_ids = {tool.id for tool in config.tools}
    known_ids = set(config_pinned_ids)
    for server_id in sorted(targets):
        if server_id not in allowed_server_ids:
            continue
        try:
            cards = relay.discover_tools(server_id, targets[server_id])
        except Exception as exc:
            exc_msg = mask_secret_text(f"{type(exc).__name__}: {exc}")
            payload: dict[str, Any] = {"server": server_id, "error": exc_msg}
            # FastMCP's error hides the downstream's own stderr. For a configured
            # stdio server, surface its startup stderr (e.g. an access-denied) so
            # the operator can see WHY without a manual probe.
            downstream_cfg = next((d for d in config.servers if d.id == server_id), None)
            diagnostic = ""
            if downstream_cfg is not None:
                diagnostic = relay.collect_startup_stderr(downstream_cfg)
                if diagnostic:
                    payload["stderr"] = diagnostic
            # Turn the raw failure into an actionable verdict (cause / supported /
            # remedy) so the operator knows e.g. "single-instance, can't run a 2nd
            # copy" without reading the stderr by hand. Always present (consistent
            # payload schema) even for an injected target with no stderr.
            payload.update(classify_discovery_failure(exc_msg, diagnostic))
            record_event(ledger, "tool.discovered", payload)
            warnings.warn(f"discover_tools({server_id}): {exc_msg}", stacklevel=2)
            continue
        record_event(ledger, "tool.discovered", {"server": server_id, "count": len(cards)})
        for card in cards:
            if card.id in config_pinned_ids:
                # Operator pinned this id: their card (risk/name/server_id) is
                # the trust anchor and becomes relayable. Only here can a read
                # card earn the token-free auto-forward.
                relayed_ids.add(card.id)
                if get_tool(config, card.id).risk == "read":
                    relayed_read_ids.add(card.id)
                continue
            if card.id in known_ids:
                continue
            discovered.append(card)
            known_ids.add(card.id)
            relayed_ids.add(card.id)
    if discovered:
        merged = config.to_dict()
        merged["tools"] = [tool.to_dict() for tool in config.tools] + [card.to_dict() for card in discovered]
        config = HubConfig.from_dict(merged)

    from werktools import __version__

    server = make_server(SERVER_NAME, version=__version__)

    def _tool_search(args: dict[str, Any]) -> dict[str, Any]:
        query = str(args.get("query", "") or "").lower()
        cards: list[dict[str, Any]] = []
        for tool in visible_tools(config, profile.id):
            haystack = f"{tool.id} {tool.name} {tool.description} {' '.join(tool.tags)}".lower()
            if query and query not in haystack:
                continue
            decision = explain(config, profile.id, tool.id)
            cards.append(
                {
                    "id": tool.id,
                    "description": tool.description,
                    "risk": tool.risk,
                    "decision": decision.decision,
                }
            )
        record_event(ledger, "tool.search", {"profile": profile.id, "query": query, "results": len(cards)})
        return ok("tool_search", data={"tools": cards})

    def _tool_describe(args: dict[str, Any]) -> dict[str, Any]:
        tool_id = str(args.get("tool_id", ""))
        visible_ids = {tool.id for tool in visible_tools(config, profile.id)}
        if tool_id not in visible_ids:
            record_event(ledger, "tool.describe", {"profile": profile.id, "tool": tool_id, "visible": False})
            return err("tool_describe", f"tool {tool_id!r} is not visible to profile {profile.id!r}")
        card = get_tool(config, tool_id)
        decision = explain(config, profile.id, tool_id)
        record_event(ledger, "tool.describe", {"profile": profile.id, "tool": tool_id, "visible": True})
        return ok("tool_describe", data={"tool": card.to_dict(), "decision": decision.to_dict()})

    def _execute_tool(tool_id: str, call_args: dict[str, Any], approved: bool) -> dict[str, Any]:
        handler = local_handlers.get(tool_id)
        if handler is not None:
            try:
                result = handler(call_args)
            except Exception as exc:
                reason = mask_secret_text(f"{type(exc).__name__}: {exc}")
                record_event(ledger, "tool.call.failed", {"profile": profile.id, "tool": tool_id, "reason": reason})
                return err("tool_call", f"{tool_id}: {reason}")
            record_event(ledger, "tool.call.completed", {"profile": profile.id, "tool": tool_id})
            return ok("tool_call", data={"tool": tool_id, "result": result})
        if tool_id in relayed_ids:
            card = get_tool(config, tool_id)
            # Token-free auto-forward is reserved for OPERATOR-pinned read cards.
            # A tool that is read only by its own (downstream-controlled)
            # annotation is not trusted and needs an approval token.
            trusted_read = card.risk == "read" and tool_id in relayed_read_ids
            if not trusted_read and not approved:
                record_event(
                    ledger,
                    "tool.call.denied",
                    {"profile": profile.id, "tool": tool_id, "reason": "relayed forward requires an operator-pinned read card or an approval token"},
                )
                return err(
                    "tool_call",
                    f"{tool_id}: relayed forwarding requires an operator-pinned read card or an approval token",
                )
            try:
                result = relay.call_downstream(targets[card.server_id], card.name, call_args)
            except Exception as exc:
                reason = mask_secret_text(f"{type(exc).__name__}: {exc}")
                record_event(ledger, "tool.call.failed", {"profile": profile.id, "tool": tool_id, "reason": reason})
                return err("tool_call", f"{tool_id}: {reason}")
            record_event(ledger, "tool.call.completed", {"profile": profile.id, "tool": tool_id})
            return ok("tool_call", data={"tool": tool_id, "result": result})
        record_event(
            ledger,
            "tool.call.failed",
            {"profile": profile.id, "tool": tool_id, "reason": "no handler configured"},
        )
        return err(
            "tool_call",
            f"tool {tool_id!r} is allowed but has no local handler and no downstream relay is configured",
        )

    def _tool_call(args: dict[str, Any]) -> dict[str, Any]:
        tool_id = str(args.get("tool_id", ""))
        raw_args = args.get("args")
        call_args: dict[str, Any] = raw_args if isinstance(raw_args, dict) else {}
        request_id = args.get("_approval_request_id")
        token = args.get("_approval_token")
        record_event(ledger, "tool.call.requested", {"profile": profile.id, "tool": tool_id})

        # Execute-after-approval: a one-use token authorizes a single run.
        # Peek the binding BEFORE consuming so a token routed to the wrong
        # tool/profile is rejected without being burned.
        if request_id and not token:
            record_event(
                ledger,
                "tool.call.denied",
                {"profile": profile.id, "tool": tool_id, "reason": "_approval_request_id supplied without _approval_token"},
            )
            return err("tool_call", f"{tool_id}: _approval_request_id supplied without _approval_token")
        if token and not request_id:
            record_event(
                ledger,
                "tool.call.denied",
                {"profile": profile.id, "tool": tool_id, "reason": "_approval_token supplied without _approval_request_id"},
            )
            return err("tool_call", f"{tool_id}: _approval_token supplied without _approval_request_id")
        if request_id and token:
            try:
                peek = load_record(approvals, str(request_id))
            except (KeyError, ValueError) as _load_exc:
                record_event(
                    ledger,
                    "tool.call.denied",
                    {"profile": profile.id, "tool": tool_id, "reason": f"unknown or invalid approval request: {_load_exc}"},
                )
                return err("tool_call", f"{tool_id}: unknown or invalid approval request {request_id}")
            if peek.profile_id != profile.id or peek.tool_id != tool_id:
                record_event(
                    ledger,
                    "tool.call.denied",
                    {"profile": profile.id, "tool": tool_id, "reason": "approval token bound to a different call"},
                )
                return err("tool_call", f"{tool_id}: approval token does not match this call")
            try:
                consume_token(
                    approvals,
                    ledger,
                    str(request_id),
                    str(token),
                    expected_args_hash=hash_call_args(call_args),
                )
            except ValueError as exc:
                record_event(
                    ledger,
                    "tool.call.denied",
                    {"profile": profile.id, "tool": tool_id, "reason": f"approval token: {exc}"},
                )
                return err("tool_call", f"{tool_id}: approval token rejected ({exc})")
            except OSError as exc:
                record_event(
                    ledger,
                    "tool.call.denied",
                    {"profile": profile.id, "tool": tool_id, "reason": f"approval store error: {exc}"},
                )
                return err("tool_call", f"{tool_id}: approval store error ({exc}); retry is safe")
            # Re-verify the live policy after the token is consumed: a token must
            # never bypass enforce(). Only allow / approval_required may proceed
            # (the human approved an approval_required tool); anything else now
            # denied fails closed even though a valid token was burned (MF3).
            recheck = enforce(config, profile.id, tool_id)
            if recheck.decision not in ("allow", "approval_required"):
                record_event(
                    ledger,
                    "tool.call.denied",
                    {"profile": profile.id, "tool": tool_id, "reason": f"post-consume policy: {recheck.reason}"},
                )
                return err("tool_call", f"{tool_id}: {recheck.reason}")
            return _execute_tool(tool_id, call_args, approved=True)

        decision = enforce(config, profile.id, tool_id)
        if decision.ok:
            # A relayed (downstream) non-read tool can never forward on a plain
            # allow: the relay path requires a consumed approval token. Under an
            # admin profile enforce() returns allow for write/external, which
            # previously dead-ended in _execute_tool. Mint an approval instead so
            # the human-in-the-loop gate actually fires (MF9).
            card = get_tool(config, tool_id) if tool_id in relayed_ids else None
            if card is not None and card.risk != "read":
                pending = request_approval(approvals, ledger, tool_id, profile.id, call_args)
                record_event(
                    ledger,
                    "tool.call.approval_required",
                    {"profile": profile.id, "tool": tool_id, "request_id": pending.request_id},
                )
                return err(
                    "tool_call",
                    f"{tool_id}: relayed write/destructive tools require an approval token; "
                    f"approve request {pending.request_id} then retry with the token",
                    data={"request_id": pending.request_id},
                )
            return _execute_tool(tool_id, call_args, approved=False)
        if decision.decision == "approval_required":
            pending = request_approval(approvals, ledger, tool_id, profile.id, call_args)
            record_event(
                ledger,
                "tool.call.approval_required",
                {"profile": profile.id, "tool": tool_id, "request_id": pending.request_id},
            )
            return err(
                "tool_call",
                f"{tool_id}: approval required; approve request {pending.request_id} then retry with the token",
                data={"request_id": pending.request_id},
            )
        record_event(ledger, "tool.call.denied", {"profile": profile.id, "tool": tool_id, "reason": decision.reason})
        return err("tool_call", f"{tool_id}: {decision.reason}")

    def _profile_info(args: dict[str, Any]) -> dict[str, Any]:
        return ok(
            "profile_info",
            data={
                "hub": config.name,
                "profile": profile.to_dict(),
                "visible_tools": len(visible_tools(config, profile.id)),
            },
        )

    def _ledger_recent(args: dict[str, Any]) -> dict[str, Any]:
        raw_limit = args.get("limit")
        if raw_limit is not None:
            try:
                limit_val = int(raw_limit)
            except (TypeError, ValueError):
                return err("ledger_recent", "limit must be an integer >= 1")
            if limit_val < 1:
                return err("ledger_recent", f"limit must be >= 1, got {limit_val}")
            limit = min(limit_val, 200)
        else:
            limit = 20
        events, chain_verified, chain_errors = recent_events_verified(ledger, limit=limit)
        return ok(
            "ledger_recent",
            data={"events": events, "chain_verified": chain_verified, "chain_errors": chain_errors},
        )

    def _approval_status(args: dict[str, Any]) -> dict[str, Any]:
        request_id = args.get("request_id")
        records = list_records(approvals)
        # Scope to the caller's own profile: approval_status must not leak
        # another profile's pending tool_ids / request_ids (cross-profile
        # metadata leak). The approvals store is shared across profiles, so
        # filter by the launch-pinned profile before projecting any rows.
        rows = [
            {
                "request_id": r.request_id,
                "tool_id": r.tool_id,
                "profile_id": r.profile_id,
                "status": r.status,
                "created_at": r.created_at,
            }
            for r in records
            if r.profile_id == profile.id
            and (request_id is None or r.request_id == str(request_id))
        ]
        pending = [row for row in rows if row["status"] == "pending"]
        return ok("approval_status", data={"pending": pending, "records": rows})

    def _hub_status_tool(args: dict[str, Any]) -> dict[str, Any]:
        return ok("hub_status", data=status_snapshot(config, profile.id, pool).to_dict())

    workers = _load_workers(workers_manifest_path)
    if workers:
        config = _with_worker_cards(config)

    register(server, "tool_search", "Search tools visible to the active profile.", _SEARCH_SCHEMA, _tool_search)
    register(server, "tool_describe", "Describe one visible tool and its policy decision.", _DESCRIBE_SCHEMA, _tool_describe)
    register(server, "tool_call", "Call an allowed tool through the policy gate.", _CALL_SCHEMA, _tool_call)
    register(server, "profile_info", "Show the active profile lens.", _EMPTY_SCHEMA, _profile_info)
    register(server, "ledger_recent", "Show recent hub ledger events.", _LEDGER_SCHEMA, _ledger_recent)
    register(server, "approval_status", "Show pending approval requests.", _APPROVAL_SCHEMA, _approval_status)
    def _registry_search(args: dict[str, Any]) -> dict[str, Any]:
        from .discovery import search_registry

        # registry_search makes an outbound HTTPS call to the Official MCP
        # Registry, so it is gated like an `external` tool (ADR-001 fail
        # closed): only balanced/admin permission profiles may reach out;
        # cautious/unknown profiles get a denial and NO network is touched.
        if profile.permission_profile not in ("balanced", "admin"):
            record_event(
                ledger,
                "tool.call.denied",
                {"profile": profile.id, "tool": "registry_search", "reason": "outbound registry search is denied for this profile"},
            )
            return err(
                "registry_search",
                f"registry_search: outbound registry search is not allowed for profile {profile.id!r}",
            )
        query = str(args.get("query", "") or "")
        raw_limit = args.get("limit")
        if raw_limit is not None:
            try:
                limit_val = int(raw_limit)
            except (TypeError, ValueError):
                return err("registry_search", "limit must be an integer >= 1")
            if limit_val < 1:
                return err("registry_search", f"limit must be >= 1, got {limit_val}")
            limit = min(limit_val, 200)
        else:
            limit = 20
        candidates, registry_warnings = search_registry(query, limit, http_get=registry_http_get)
        record_event(ledger, "registry.search", {"profile": profile.id, "query": query, "results": len(candidates)})
        return ok("registry_search", data={"candidates": [c.to_dict() for c in candidates], "warnings": registry_warnings})

    register(server, "hub_status", "Show downstream server fleet status.", _EMPTY_SCHEMA, _hub_status_tool)
    register(server, "registry_search", "Search the Official MCP Registry for installable servers.", _SEARCH_SCHEMA, _registry_search)

    if workers:
        from .workers import check_budget, dispatch_worker, get_worker

        def _denied(tool_id: str, reason: str, worker_id: str | None = None) -> dict[str, Any]:
            payload: dict[str, Any] = {"profile": profile.id, "tool": tool_id, "reason": reason}
            if worker_id is not None:
                payload["worker"] = worker_id
            record_event(ledger, "model_worker.call.denied", payload)
            return err(tool_id, f"{tool_id}: {reason}")

        def _model_worker_list(args: dict[str, Any]) -> dict[str, Any]:
            if not enforce(config, profile.id, "model_worker_list").ok:
                return _denied("model_worker_list", "not allowed for this profile")
            record_event(ledger, "model_worker.listed", {"profile": profile.id, "count": len(workers)})
            return ok("model_worker_list", data={"workers": [w.to_dict() for w in workers if w.enabled]})

        def _model_worker_budget_check(args: dict[str, Any]) -> dict[str, Any]:
            if not enforce(config, profile.id, "model_worker_budget_check").ok:
                return _denied("model_worker_budget_check", "not allowed for this profile")
            worker = get_worker(workers, str(args.get("worker", "")))
            if worker is None:
                return err("model_worker_budget_check", "unknown worker")
            status = check_budget(worker, ledger)
            record_event(ledger, "model_worker.budget_checked", {"worker": worker.id, "decision": status.decision})
            return ok("model_worker_budget_check", data=status.to_dict())

        def _model_worker_report(args: dict[str, Any]) -> dict[str, Any]:
            from .workers import report_workers

            if not enforce(config, profile.id, "model_worker_report").ok:
                return _denied("model_worker_report", "not allowed for this profile")
            return ok("model_worker_report", data={"workers": report_workers(workers, ledger)})

        def _model_worker_call(args: dict[str, Any]) -> dict[str, Any]:
            worker_id = str(args.get("worker", ""))
            model = str(args.get("model", ""))
            prompt = str(args.get("prompt", ""))
            request_id = args.get("_approval_request_id")
            token = args.get("_approval_token")
            worker = get_worker(workers, worker_id)
            if worker is None:
                return err("model_worker_call", "unknown worker")
            record_event(ledger, "model_worker.call.requested", {"profile": profile.id, "worker": worker_id})

            if request_id and not token:
                return _denied("model_worker_call", "model_worker_call: _approval_request_id supplied without _approval_token")
            if token and not request_id:
                record_event(ledger, "model_worker.call.denied", {"profile": profile.id, "worker": worker_id, "reason": "_approval_token supplied without _approval_request_id"})
                return err("model_worker_call", "model_worker_call: _approval_token supplied without _approval_request_id")
            if request_id and token:
                try:
                    peek = load_record(approvals, str(request_id))
                except (KeyError, ValueError) as _load_exc:
                    return _denied("model_worker_call", f"unknown or invalid approval request: {_load_exc}", worker_id=worker_id)
                if peek.tool_id != "model_worker_call" or peek.profile_id != profile.id:
                    return _denied("model_worker_call", "approval token bound to a different call")
                try:
                    consume_token(
                        approvals,
                        ledger,
                        str(request_id),
                        str(token),
                        expected_args_hash=hash_call_args({"worker": worker_id, "model": model, "prompt": prompt}),
                    )
                except ValueError as exc:
                    return _denied("model_worker_call", f"approval token: {exc}")
                except OSError as exc:
                    return _denied("model_worker_call", f"approval store error ({exc}); retry is safe", worker_id=worker_id)
                recheck = enforce(config, profile.id, "model_worker_call")
                if recheck.decision not in ("allow", "approval_required"):
                    return _denied("model_worker_call", f"post-consume policy: {recheck.reason}")
                record_event(ledger, "model_worker.call.approved", {"worker": worker_id, "request_id": str(request_id)})
                status = check_budget(worker, ledger)
                if status.decision != "allow":
                    return _denied("model_worker_call", f"budget {status.decision}: {status.reason}")
                try:
                    result = dispatch_worker(worker, model, prompt, worker.max_tokens, ledger)
                except (KeyError, ImportError, ValueError, IndexError) as exc:
                    reason = mask_secret_text(f"{type(exc).__name__}: {exc}")
                    record_event(ledger, "model_worker.call.failed", {"worker": worker_id, "reason": reason})
                    return err("model_worker_call", f"dispatch error: {reason}")
                if not result.ok:
                    return _denied("model_worker_call", result.reason)
                record_event(ledger, "model_worker.call.completed", {"worker": worker_id, "cost_usd": result.cost_usd})
                return ok("model_worker_call", data=result.to_dict())

            decision = enforce(config, profile.id, "model_worker_call")
            if decision.decision == "approval_required":
                pending = request_approval(
                    approvals, ledger, "model_worker_call", profile.id, {"worker": worker_id, "model": model, "prompt": prompt}
                )
                return err(
                    "model_worker_call",
                    f"approval required; approve {pending.request_id} then retry with the token",
                    data={"request_id": pending.request_id},
                )
            return _denied("model_worker_call", decision.reason)

        register(server, "model_worker_list", "List configured model workers.", _EMPTY_SCHEMA, _model_worker_list)
        register(server, "model_worker_budget_check", "Check a worker's budget.", _WORKER_SCHEMA, _model_worker_budget_check)
        register(server, "model_worker_call", "Call a model worker (governed, budgeted).", _WORKER_CALL_SCHEMA, _model_worker_call)
        register(server, "model_worker_report", "Report worker manifests + budgets.", _EMPTY_SCHEMA, _model_worker_report)

    server._pool = pool  # type: ignore[attr-defined]
    if status_port is not None:
        _start_status_server(status_port, config, profile.id, pool)
    return server


def _start_status_server(port: int, config: HubConfig, profile_id: str, pool: Any):
    """Start a 127.0.0.1-bound JSON status endpoint in a daemon thread.

    The only long-lived loop in the hub, and it lives solely inside the serve
    process (never imported by the stdlib core). GET /status -> HubStatus.
    """
    import ipaddress as _ipaddress
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    def _host_ok(raw: str) -> bool:
        """Return True only when the Host header resolves to a loopback address."""
        if not raw:
            return False
        if raw.startswith("["):
            host = raw.lstrip("[").split("]")[0]
        else:
            host = raw.rsplit(":", 1)[0]
        if host in ("localhost",):
            return True
        try:
            return _ipaddress.ip_address(host).is_loopback
        except ValueError:
            return False

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            if not _host_ok(self.headers.get("Host") or ""):
                self.send_response(403)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":"forbidden"}')
                return
            if self.path != "/status":
                self.send_response(404)
                self.end_headers()
                return
            try:
                body = json.dumps(status_snapshot(config, profile_id, pool).to_dict()).encode("utf-8")
            except Exception as exc:
                error_body = json.dumps({"error": f"{type(exc).__name__}: {exc}"}).encode("utf-8")
                self.send_response(500)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(error_body)))
                self.end_headers()
                self.wfile.write(error_body)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):  # noqa: N802
            self.send_response(405)
            self.end_headers()

        def log_message(self, *args):  # silence default stderr logging
            return

    httpd = HTTPServer(("127.0.0.1", port), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd
