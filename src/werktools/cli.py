"""Human CLI for static WERK Hub inspection."""

from __future__ import annotations

import argparse
import json
import os
import sys
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .hub.runtimes import RuntimeDescriptor, RuntimeProbe

from .hub.capabilities import (
    capability_cards,
    classify_capability,
    export_capabilities,
    show_capability,
)
from .hub.policy import explain
from .hub.registry import load_config, save_default_config, visible_tools
from .tools.audit import (
    export_bundle as audit_export_bundle,
)
from .tools.audit import (
    redact_jsonl as audit_redact_jsonl,
)
from .tools.audit import (
    verify_chain as audit_verify_chain,
)
from .tools.audit import (
    write_report as write_audit_report,
)
from .tools.bench import (
    BenchmarkSpec as BenchBenchmarkSpec,
)
from .tools.bench import (
    Variant as BenchVariant,
)
from .tools.bench import (
    build_matrix as bench_build_matrix,
)
from .tools.bench import (
    judge_quality as bench_judge_quality,
)
from .tools.bench import (
    load_results as bench_load_results,
)
from .tools.bench import (
    render_report as bench_render_report,
)
from .tools.bench import (
    run_matrix as bench_run_matrix,
)
from .tools.cost import (
    budget_check as cost_budget_check,
)
from .tools.cost import (
    record_cost,
    rollup_costs,
)
from .tools.cost import (
    write_report as write_cost_report,
)
from .tools.data_gate import (
    add_source as add_data_source,
)
from .tools.data_gate import (
    audit_recent as data_audit_recent,
)
from .tools.data_gate import (
    query_preview as data_query_preview,
)
from .tools.data_gate import (
    query_read as data_query_read,
)
from .tools.data_gate import (
    schema as data_schema,
)
from .tools.data_gate import (
    sources as data_sources,
)
from .tools.eval import (
    list_cassettes as eval_list_cassettes,
)
from .tools.eval import (
    run_cassette as eval_run_cassette,
)
from .tools.eval import (
    write_report as write_eval_report,
)
from .tools.integration_gate import (
    add_connector,
    show_connector,
)
from .tools.integration_gate import (
    audit_recent as integration_audit_recent,
)
from .tools.integration_gate import (
    connectors as integration_connectors,
)
from .tools.integration_gate import (
    explain_policy as integration_explain_policy,
)
from .tools.integration_gate import (
    request_access as integration_request_access,
)
from .tools.mine import (
    create_card,
    load_cards,
    query_cards,
    write_card,
    write_index,
)
from .tools.mine import (
    write_report as write_mine_report,
)
from .tools.skills import (
    export_skills,
    list_skills,
    match_skills,
    show_skill,
)
from .tools.swarm import (
    collect_reports as swarm_collect_reports,
)
from .tools.swarm import (
    load_plan as swarm_load_plan,
)
from .tools.swarm import (
    plan_from_goal,
)
from .tools.swarm import (
    render_packet as swarm_render_packet,
)
from .tools.swarm import (
    review_reports as swarm_review_reports,
)
from .tools.swarm import (
    write_plan as swarm_write_plan,
)
from .tools.trace import (
    append_event,
    export_trace,
    recent_events,
    verify_trace,
)
from .tools.truth import scan_repo
from .tools.truth import write_report as write_truth_report
from .tools.vault import (
    add_source as add_vault_source,
)
from .tools.vault import (
    audit_recent as vault_audit_recent,
)
from .tools.vault import (
    explain_access as vault_explain_access,
)
from .tools.vault import (
    init_vault,
)
from .tools.vault import (
    search as vault_search,
)
from .tools.vault import (
    show_item as vault_show_item,
)
from .tools.vault import (
    sources as vault_sources,
)


def _load_or_default(path: Path):
    if path.exists():
        return load_config(path)
    return save_default_config(path)


def _hub_init(config_path: Path, community: bool = False) -> int:
    if config_path.exists():
        print(f"Hub config already exists (left unchanged): {config_path}")
        return 0
    if community:
        from .hub.registry import save_community_default_config

        save_community_default_config(config_path)
        print(f"Community hub config initialized: {config_path}")
    else:
        save_default_config(config_path)
        print(f"Hub config initialized: {config_path}")
    return 0


def _hub_serve(config_path: Path, profile: str | None, status_port: int | None = None) -> int:
    config = _load_or_default(config_path)
    selected = profile or os.environ.get("WERKTOOLS_HUB_PROFILE") or None
    try:
        from .hub import server as hub_server
    except ImportError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    try:
        server = hub_server.build_hub_server(config, profile_id=selected, status_port=status_port)
    except KeyError as exc:
        print(f"Error: unknown hub profile {exc}", file=sys.stderr)
        return 1
    server.run()
    return 0


def _hub_dashboard(config_path: Path, host: str, port: int, open_browser: bool) -> int:
    from .hub.dashboard import run_dashboard

    config = _load_or_default(config_path)
    run_dashboard(
        config, config.ledger_path, host=host, port=port, open_browser=open_browser, config_path=config_path
    )
    return 0


def _hub_pool_status(config_path: Path, profile: str | None) -> int:
    import json as _json

    from .hub.status import hub_status

    config = _load_or_default(config_path)
    snapshot = hub_status(config, profile, pool=None)
    print(_json.dumps(snapshot.to_dict(), indent=2, sort_keys=True))
    return 0


def _hub_approvals_list(config_path: Path) -> int:
    from .hub.approvals import list_records

    config = _load_or_default(config_path)
    ledger_parent = Path(config.ledger_path).parent / "hub-approvals"
    for record in list_records(ledger_parent):
        print(f"{record.request_id}\t{record.status}\t{record.tool_id}\t{record.profile_id}")
    return 0


def _hub_approvals_approve(config_path: Path, request_id: str) -> int:
    from .hub.approvals import approve_request

    config = _load_or_default(config_path)
    approvals = Path(config.ledger_path).parent / "hub-approvals"
    try:
        record = approve_request(approvals, config.ledger_path, request_id)
    except (KeyError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Approved: {record.request_id}")
    print(f"Token: {record.token}")
    return 0


def _hub_approvals_deny(config_path: Path, request_id: str) -> int:
    from .hub.approvals import deny_request

    config = _load_or_default(config_path)
    approvals = Path(config.ledger_path).parent / "hub-approvals"
    try:
        record = deny_request(approvals, config.ledger_path, request_id)
    except (KeyError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Denied: {record.request_id}")
    return 0


def _hub_registry_search(query: str) -> int:
    from .hub.discovery import search_registry

    candidates, warnings = search_registry(query)
    for c in candidates:
        print(f"{c.id}\t{c.name}\t{c.description}")
    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)
    return 0


def _allowlist_path_for(config) -> Path:
    """Tier-1 allowlist override path from config (absent file -> embedded seed).

    Resolved like ``ledger_path`` (relative to CWD), so the default
    ``.werktools/tier1_allowlist.json`` sits beside the default hub.json.
    """
    return Path(config.tier1_allowlist_path)


def _hub_registry_install(config_path: Path, gate_root: str, query: str) -> int:
    from .hub.discovery import search_registry, stage_install
    from .tools.integration_gate import connector_trust_tier, connectors

    config = _load_or_default(config_path)
    candidates, _ = search_registry(query)
    if not candidates:
        print("Error: no matching registry server", file=sys.stderr)
        return 1
    request = stage_install(
        gate_root,
        candidates[0],
        hub_ledger_path=config.ledger_path,
        allowlist_path=_allowlist_path_for(config),
    )
    print(f"Staged install: {request.request_id} ({candidates[0].id})")
    conn = next((c for c in connectors(gate_root) if c.connector_id == candidates[0].id), None)
    if conn is not None and connector_trust_tier(conn) == "Community-Unverified":
        print(
            f"Warning: {candidates[0].id} is UNVETTED (not on the Tier-1 allowlist); "
            "human approval required before connect.",
            file=sys.stderr,
        )
    return 0


def _hub_registry_approve(config_path: Path, gate_root: str, request_id: str, hub_config: str) -> int:
    from .hub.discovery import approve_and_write

    config = _load_or_default(config_path)
    try:
        server = approve_and_write(
            gate_root,
            request_id,
            hub_config,
            ledger_path=config.ledger_path,
            allowlist_path=_allowlist_path_for(config),
        )
    except (ValueError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Connected: {server.id} ({server.command})")
    if server.trust_tier == "Community-Unverified":
        print(
            f"Warning: server {server.id} is UNVETTED (Community-Unverified). Proceed with caution.",
            file=sys.stderr,
        )
    return 0


def _registry_db_path(config_path: Path) -> Path:
    """Capability-registry read-model DB, beside the hub config."""
    return Path(config_path).parent / "registry.db"


def _hub_registry_build(config_path: Path, skills_dir: str | None = None) -> int:
    from .hub import registry_db
    from .hub.capability_select import load_skill_capabilities

    rows = list(registry_db.load_seed())
    if skills_dir:
        rows += load_skill_capabilities(skills_dir)
    db = _registry_db_path(config_path)
    n = registry_db.build_registry(db, rows)
    print(f"built {db}: {n} capabilities")
    return 0


def _hub_registry_list(config_path: Path, category: str | None, deluxe: bool) -> int:
    from .hub import registry_db

    caps = registry_db.query_capabilities(
        _registry_db_path(config_path), category=category, deluxe_only=deluxe, limit=500
    )
    if not caps:
        print("no capabilities (run 'hub registry build' first)", file=sys.stderr)
        return 0
    for c in caps:
        print(f"{c.id}\t{c.kind}\t{c.category}\t{c.trust_tier}\t{'deluxe' if c.deluxe_base else '-'}")
    return 0


def _hub_registry_select(config_path: Path, task: str, budget: int) -> int:
    from .hub import registry_db
    from .hub.capability_select import select_capabilities

    caps = registry_db.query_capabilities(_registry_db_path(config_path), limit=1000)
    if not caps:
        print("no capabilities (run 'hub registry build' first)", file=sys.stderr)
        return 0
    sel = select_capabilities(caps, task, budget=budget)
    print(f"# selected for: {task}")
    for d in sel.included:
        print(f"  + {d.capability.id}\t({d.reason})")
    print("# excluded (top reasons)")
    for d in sel.excluded[:10]:
        print(f"  - {d.capability.id}\t({d.reason})")
    return 0


def _hub_onboard(
    config_path: Path,
    apply: bool,
    host: str | None,
    home: str | None,
) -> int:
    """Discover host MCP configs and (optionally) adopt them into hub.json."""
    from .hub.onboarding import onboard

    home_path = Path(home) if home else None
    result = onboard(config_path, apply=apply, home=home_path, host_filter=host)

    # Print per-host counts
    if result.by_host:
        print("Discovered servers by host:")
        for h, count in sorted(result.by_host.items()):
            print(f"  {h}: {count}")
    else:
        print("No MCP servers discovered.")

    # Print the mapping table
    if result.connectors:
        print("\nWould adopt:" if not apply else "\nConnectors:")
        for c in result.connectors:
            tier = c.trust_tier
            print(f"  {c.id}\t{c.transport}\t{tier}")
    else:
        print("No connectors to adopt.")

    if apply:
        if result.added:
            print(f"\nAdded {len(result.added)} connector(s): {', '.join(result.added)}")
        else:
            print("\nNo new connectors added (all already present).")
        if result.skipped_hosts:
            print(f"Kept existing (collision): {', '.join(result.skipped_hosts)}")
    else:
        print(f"\n(dry-run) Pass --apply to write {len(result.connectors)} connector(s) to hub.json")

    return 0


def _hub_export_rules(config_path: Path, agents_md: str, skills_dir: str, out: str, profile: str | None, host: str) -> int:
    from .hub.export_rules import export_rules

    config = _load_or_default(config_path)
    try:
        manifest = export_rules(
            agents_md, skills_dir, out, profile or config.default_profile, host, ledger_path=config.ledger_path
        )
    except (ValueError, FileNotFoundError, ImportError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Export complete: {manifest.host} ({manifest.skill_count} skills) -> {out}")
    return 0


def _hub_render(config_path: Path, profile: str | None, host: str, out: str | None) -> int:
    from .hub.ledger import record_event
    from .hub.render import render

    config = _load_or_default(config_path)
    try:
        text = render(config, profile, host)
    except (ValueError, ImportError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(text, encoding="utf-8")
        print(f"Rendered {host} config -> {out}")
    else:
        print(text)
    record_event(config.ledger_path, "config.rendered", {"profile": profile or config.default_profile, "host": host})
    return 0


def _canon_check(repo: Path, strict: bool) -> int:
    from .tools.canon import check_canon

    report = check_canon(repo)
    for issue in report.issues:
        print(f"{issue.severity}\t{issue.kind}\t{issue.target}\t{issue.source}\t{issue.detail}")
    n_err = len(report.errors)
    n_warn = len(report.warnings)
    verdict = "OK" if report.ok and not (strict and n_warn) else "FAIL"
    print(f"Canon: {n_err} errors, {n_warn} warnings - {verdict}")
    if n_err or (strict and n_warn):
        return 1
    return 0


def _canon_gen_agents(repo: Path, out: str | None, dry_run: bool) -> int:
    from .tools.canon import gen_agents_md

    text = gen_agents_md(repo)
    if dry_run or not out:
        print(text)
        return 0
    Path(out).write_text(text, encoding="utf-8")
    print(f"Wrote {out}")
    return 0


def _canon_gen_spec(name: str, out_dir: str | None, kind: str, dry_run: bool) -> int:
    from .tools.canon import gen_spec_template

    files = gen_spec_template(name, kind=kind)
    if dry_run or not out_dir:
        for fname in files:
            print(fname)
        return 0
    target = Path(out_dir)
    target.mkdir(parents=True, exist_ok=True)
    for fname, content in files.items():
        (target / fname).write_text(content, encoding="utf-8")
    print(f"Wrote {len(files)} spec files -> {out_dir}")
    return 0


def _models_list(workers_path: Path) -> int:
    from .hub.workers import load_workers

    for worker in load_workers(workers_path):
        flag = "enabled" if worker.enabled else "disabled"
        print(f"{worker.id}\t{worker.provider}\t{flag}\t{','.join(worker.allowed_models)}")
    return 0


def _models_report(config_path: Path, workers_path: Path) -> int:
    import json as _json

    from .hub.workers import load_workers, report_workers

    config = _load_or_default(config_path)
    rows = report_workers(load_workers(workers_path), config.ledger_path)
    print(_json.dumps(rows, indent=2, sort_keys=True))
    return 0


def _models_call(config_path: Path, workers_path: Path, worker_id: str, model: str, prompt: str) -> int:
    from .hub.workers import check_budget, dispatch_worker, get_worker, load_workers

    config = _load_or_default(config_path)
    worker = get_worker(load_workers(workers_path), worker_id)
    if worker is None:
        print(f"Error: unknown worker {worker_id!r}", file=sys.stderr)
        return 1
    status = check_budget(worker, config.ledger_path)
    if status.decision != "allow":
        print(f"Error: budget {status.decision}: {status.reason}", file=sys.stderr)
        return 1
    try:
        result = dispatch_worker(worker, model, prompt, worker.max_tokens, config.ledger_path)
    except (KeyError, ImportError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    if not result.ok:
        print(f"Error: {result.reason}", file=sys.stderr)
        return 1
    print(result.summary)
    return 0


def _hub_reap(config_path: Path, sidecar: str | None, ttl: float) -> int:
    import time as _time

    from .hub.lifecycle import orphan_sweep, reap

    config = _load_or_default(config_path)
    ledger_path = Path(config.ledger_path)
    sidecar_path = Path(sidecar) if sidecar else ledger_path.parent / "hub-procs.json"
    if ttl > 0:
        reaped = reap(_time.time(), ttl, sidecar_path, ledger_path)
    else:
        reaped = orphan_sweep(sidecar_path, ledger_path)
    for row in reaped:
        print(f"{row['server_id']}\tpid={row['pid']}\t{row['reason']}\t{row['kill_status']}")
    print(f"total reaped: {len(reaped)}")
    return 0


def _runtime_row(probe: "RuntimeProbe", descriptor: "RuntimeDescriptor") -> dict[str, Any]:
    """Merge a RuntimeProbe with its descriptor for the --json consumer.

    Delegates to the shared runtime_row helper so CLI and dashboard stay in
    sync — both import the same function rather than duplicating the logic.
    """
    from .hub.runtime_row import runtime_row

    return runtime_row(probe, descriptor)


def _hub_doctor(
    config_path: Path,
    *,
    as_json: bool = False,
    probe_versions: bool = False,
    hosts: tuple[str, ...] = (),
    detected_only: bool = False,
) -> int:
    from .hub.invariants import run_all
    from .hub.ledger import record_event
    from .hub.runtimes import get_descriptor, probe_all

    results = run_all()
    total = sum(len(v) for v in results.values())

    config = None
    config_error = ""
    if config_path.exists():
        try:
            config = load_config(config_path)
        except (ValueError, OSError) as exc:
            config_error = str(exc)
            total += 1
    config_ok = config is not None or not config_path.exists()

    # Host detection is informational and never changes the exit code. The
    # default path is subprocess-free; only --probe-versions opts into it.
    report = probe_all(probe_versions=probe_versions)

    # Emit the ledger event when we can resolve a ledger path (config present).
    if config is not None:
        try:
            record_event(
                config.ledger_path,
                "runtime.probed",
                {"detected": list(report.detected_hosts()), "probe_versions": probe_versions},
            )
        except OSError as exc:
            warnings.warn(f"runtime probe: ledger write failed: {exc}", stacklevel=2)  # honest-degrade: probe stays non-fatal

    # Apply display filters (do not affect the event payload, which is full).
    probes = report.probes
    if hosts:
        known_ids = {p.host_id for p in report.probes}
        unknown = [h for h in hosts if h not in known_ids]
        if unknown:
            print(f"doctor: unknown host(s): {', '.join(unknown)}", file=sys.stderr)
        wanted = set(hosts)
        probes = tuple(p for p in probes if p.host_id in wanted)
    if detected_only:
        probes = tuple(p for p in probes if p.detected)

    if as_json:
        runtimes = report.to_dict()
        # keep the summary consistent with any applied --host/--detected-only filter
        runtimes["probes"] = [_runtime_row(p, get_descriptor(p.host_id)) for p in probes]
        runtimes["total"] = len(probes)
        runtimes["detected"] = [p.host_id for p in probes if p.detected]
        payload = {
            "invariants": results,
            "config_ok": config_ok,
            "config_initialized": config_path.exists(),
            "config_error": config_error,
            "runtimes": runtimes,
            "total_violations": total,
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 1 if total else 0

    for check, violations in results.items():
        if violations:
            print(f"FAIL {check}")
            for v in violations:
                print(f"  - {v}")
        else:
            print(f"ok   {check}")
    if config is not None:
        print(
            f"config {config_path}: profiles={len(config.profiles)} "
            f"tools={len(config.tools)} servers={len(config.servers)}"
        )
    elif config_path.exists():
        print(f"config {config_path}: unreadable ({config_error})")
    else:
        print(f"config {config_path}: not initialized (run hub init)")

    # Runtimes panel — text + monochrome monogram placeholders, NO vendor logos.
    print("\nRuntimes")
    for probe in probes:
        descriptor = get_descriptor(probe.host_id)
        mark = "detected" if probe.detected else "missing "
        where = probe.binary_path or probe.gui_path_found or probe.config_path_found or "-"
        risk = " [at-risk]" if descriptor.at_risk else ""
        signals = []
        if probe.token_env_present:
            signals.append("token-env")
        if probe.token_file_present:
            signals.append("token-file")
        signal = (" " + ",".join(signals)) if signals else ""
        version = f" v={probe.version_str}" if probe.version_str else ""
        print(f"  [{descriptor.monogram:<2}] {mark} {descriptor.display_name:<18}{risk}  {where}{signal}{version}")

    print(f"\n{total} violation(s)" if total else "\nall invariants hold")
    return 1 if total else 0


def _hub_status(config_path: Path) -> int:
    config = _load_or_default(config_path)
    print(f"Hub: {config.name}")
    print(f"Default profile: {config.default_profile}")
    print(f"Profiles: {len(config.profiles)}")
    print(f"Tools: {len(config.tools)}")
    print(f"Ledger: {config.ledger_path}")
    return 0


def _hub_tools(config_path: Path, profile_id: str | None) -> int:
    config = _load_or_default(config_path)
    selected_profile = profile_id or config.default_profile
    for tool in visible_tools(config, selected_profile):
        decision = explain(config, selected_profile, tool.id)
        print(f"{tool.id}\t{tool.risk}\t{decision.decision}")
    return 0


def _hub_policy_explain(config_path: Path, tool_id: str, profile_id: str | None) -> int:
    config = _load_or_default(config_path)
    selected_profile = profile_id or config.default_profile
    decision = explain(config, selected_profile, tool_id)
    print(f"Tool: {decision.tool_id}")
    print(f"Profile: {decision.profile_id}")
    print(f"Decision: {decision.decision}")
    print(f"Risk: {decision.risk}")
    print(f"Reason: {decision.reason}")
    return 0


def _capability_list(config_path: Path) -> int:
    config = _load_or_default(config_path)
    for card in capability_cards(config):
        print(f"{card['id']}\t{card['risk']}")
    return 0


def _capability_show(config_path: Path, capability_id: str) -> int:
    config = _load_or_default(config_path)
    print(json.dumps(show_capability(config, capability_id), indent=2, sort_keys=True))
    return 0


def _capability_classify(manifest_path: Path) -> int:
    raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Expected JSON object at top level, got {type(raw)}")
    print(json.dumps(classify_capability(raw), indent=2, sort_keys=True))
    return 0


def _capability_export(config_path: Path, out_path: Path) -> int:
    config = _load_or_default(config_path)
    cards = export_capabilities(config, out_path)
    print(f"Exported {len(cards)} capability cards: {out_path}")
    return 0


def _truth_scan(repo: Path) -> int:
    facts = scan_repo(repo)
    print(f"Repo: {facts.root}")
    print(f"Project: {facts.project_name or 'unknown'}")
    print(f"Markdown: {len(facts.markdown_files)}")
    print(f"Python: {len(facts.python_files)}")
    print(f"Tests: {len(facts.test_files)}")
    print(f"Console scripts: {len(facts.console_scripts)}")
    return 0


def _truth_report(repo: Path, out: Path) -> int:
    result = write_truth_report(repo, out)
    print(f"Truth report written: {out}")
    print(f"Checks: {len(result['checks'])}")
    return 0


def _mine_extract(source: Path, out_dir: Path, topic: str | None) -> int:
    text = source.read_text(encoding="utf-8")
    card = create_card(str(source), text, topic=topic)
    path = write_card(card, out_dir)
    print(f"Mine card written: {path}")
    print(f"Card: {card.id}")
    print("Source status: provided_unverified")
    return 0


def _mine_index(cards_dir: Path) -> int:
    path = write_index(cards_dir)
    print(f"Mine index written: {path}")
    return 0


def _mine_query(cards_dir: Path, query: str) -> int:
    matches = query_cards(load_cards(cards_dir), query)
    for card in matches:
        print(f"{card.id}\t{card.topic}\t{card.title}")
    return 0


def _mine_report(cards_dir: Path, out: Path, topic: str | None) -> int:
    path = write_mine_report(load_cards(cards_dir), out, topic=topic)
    print(f"Mine report written: {path}")
    return 0


def _trace_payload(raw: str | None) -> dict[str, Any]:
    if raw is None:
        return {}
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("Trace payload must be a JSON object")
    return payload


def _trace_append(
    path: Path,
    event_type: str,
    actor: str | None,
    payload: str | None,
    source_id: str | None,
    hash_chain: bool,
) -> int:
    event = append_event(
        path,
        event_type,
        actor=actor,
        payload=_trace_payload(payload),
        source_id=source_id,
        hash_chain=hash_chain,
    )
    print(f"Trace event appended: {event.event_id}")
    print(f"File: {path}")
    return 0


def _trace_recent(path: Path, limit: int) -> int:
    for event in recent_events(path, limit=limit):
        print(f"{event.created_at}\t{event.event_type}\t{event.event_id}")
    return 0


def _trace_verify(path: Path) -> int:
    result = verify_trace(path)
    print(f"OK: {result.ok}")
    print(f"Events: {result.event_count}")
    for error in result.errors:
        print(f"Error: {error}")
    return 0 if result.ok else 1


def _trace_export(
    path: Path,
    out: Path,
    event_type: str | None,
    actor: str | None,
    source_id: str | None,
) -> int:
    count = export_trace(path, out, event_type=event_type, actor=actor, source_id=source_id)
    print(f"Trace export written: {out}")
    print(f"Events: {count}")
    return 0


def _vault_init(root: Path) -> int:
    path = init_vault(root)
    print(f"Vault initialized: {path}")
    return 0


def _vault_add_source(
    root: Path,
    source_path: Path,
    label: str,
    classification: str,
    owner: str,
    profiles: list[str] | None,
) -> int:
    source, items = add_vault_source(
        root,
        source_path,
        label=label,
        classification=classification,
        owner=owner,
        profiles=tuple(profiles) if profiles else None,
    )
    print(f"Vault source added: {source.source_id}")
    print(f"Label: {source.label}")
    print(f"Indexed: {len(items)}")
    return 0


def _vault_sources(root: Path, profile: str | None) -> int:
    for source in vault_sources(root, profile=profile):
        print(f"{source.source_id}\t{source.classification}\t{source.label}\t{source.path}")
    return 0


def _vault_search(root: Path, query: str, profile: str, limit: int) -> int:
    for item in vault_search(root, query, profile=profile, limit=limit):
        print(f"{item.item_id}\t{item.source_label}\t{item.path}\t{item.snippet}")
    return 0


def _vault_show(root: Path, item_id: str, profile: str, reveal_secrets: bool) -> int:
    item = vault_show_item(root, item_id, profile=profile, reveal_secrets=reveal_secrets)
    print(f"Item: {item.item_id}")
    print(f"Source: {item.source_label}")
    print(f"Path: {item.path}")
    print("")
    print(item.text)
    return 0


def _vault_explain_access(root: Path, source_id: str, profile: str) -> int:
    decision = vault_explain_access(root, source_id, profile=profile)
    print(f"Decision: {decision.decision}")
    print(f"Source: {decision.source_id}")
    print(f"Profile: {decision.profile}")
    print(f"Reason: {decision.reason}")
    return 0


def _vault_audit_tail(root: Path, limit: int) -> int:
    for event in vault_audit_recent(root, limit=limit):
        print(f"{event.created_at}\t{event.event_type}\t{event.event_id}")
    return 0


def _data_add_source(
    root: Path,
    sqlite_path: Path,
    source_id: str,
    label: str | None,
    tables: list[str],
    masks: list[str] | None,
    profiles: list[str] | None,
    limit: int,
) -> int:
    source = add_data_source(
        root,
        source_id,
        sqlite_path,
        label=label,
        tables=tuple(tables),
        masked_columns=tuple(masks or ()),
        profiles=tuple(profiles) if profiles else None,
        row_limit=limit,
    )
    print(f"Data source added: {source.source_id}")
    print(f"Tables: {len(source.tables)}")
    print(f"Masked columns: {len(source.masked_columns)}")
    return 0


def _data_sources(root: Path, profile: str | None) -> int:
    for source in data_sources(root, profile=profile):
        print(f"{source.source_id}\t{source.label}\t{source.sqlite_path}")
    return 0


def _data_schema(root: Path, source_id: str, profile: str) -> int:
    info = data_schema(root, source_id, profile=profile)
    print(f"Source: {info.source_id}")
    for table, columns in info.tables.items():
        print(f"{table}\t{', '.join(columns)}")
    return 0


def _data_preview(root: Path, source_id: str, intent: str, profile: str) -> int:
    preview = data_query_preview(root, source_id, intent, profile=profile)
    print(f"Preview: {preview.preview_id}")
    print(f"Source: {preview.source_id}")
    print(f"Table: {preview.table}")
    print(f"SQL: {preview.sql}")
    return 0


def _data_read(root: Path, preview_id: str, profile: str) -> int:
    result = data_query_read(root, preview_id, profile=profile)
    print(
        json.dumps(
            {
                "preview_id": result.preview_id,
                "source_id": result.source_id,
                "rows_returned": result.rows_returned,
                "masked_columns": list(result.masked_columns),
                "audit_id": result.audit_id,
                "rows": list(result.rows),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


def _data_audit(root: Path, limit: int) -> int:
    for event in data_audit_recent(root, limit=limit):
        print(f"{event.created_at}\t{event.event_type}\t{event.event_id}")
    return 0


def _swarm_plan(goal_file: Path, out_dir: Path, repo: str, agents: int) -> int:
    plan = plan_from_goal(goal_file, repo=repo, agents=agents)
    path = swarm_write_plan(plan, out_dir)
    print(f"Swarm plan written: {path}")
    print(f"Packets: {len(plan.packets)}")
    return 0


def _swarm_packet(agent_id: str, plan_dir: Path) -> int:
    plan = swarm_load_plan(plan_dir)
    print(swarm_render_packet(plan, agent_id))
    return 0


def _swarm_collect(reports_dir: Path) -> int:
    summary = swarm_collect_reports(reports_dir)
    print(f"Reports: {summary.report_count}")
    for status in summary.statuses:
        print(f"Status: {status}")
    for evidence in summary.evidence:
        print(f"Evidence: {evidence}")
    return 0


def _swarm_review(reports_dir: Path) -> int:
    review = swarm_review_reports(reports_dir)
    print(f"OK: {review.ok}")
    for finding in review.findings:
        print(f"Finding: {finding}")
    return 0 if review.ok else 1


def _cost_record(
    path: Path,
    mission: str,
    task: str,
    tool: str,
    model: str,
    amount: str | None,
    currency: str,
) -> int:
    record_cost(path, mission=mission, task=task, tool=tool, model=model, amount=amount, currency=currency)
    print(f"Cost event recorded: {path}")
    return 0


def _cost_rollup(path: Path) -> int:
    rollup = rollup_costs(path)
    print(f"Total: {rollup.total}")
    print(f"Events: {rollup.event_count}")
    print(f"Unknown: {rollup.unknown_count}")
    return 0


def _cost_budget_check(path: Path, budget: str) -> int:
    decision = cost_budget_check(rollup_costs(path), budget)
    print(f"Decision: {decision.decision}")
    print(f"Total: {decision.total}")
    print(f"Budget: {decision.budget}")
    print(f"Reason: {decision.reason}")
    return 0 if decision.decision == "allow" else 1


def _cost_report(path: Path, out: Path, budget: str | None) -> int:
    report = write_cost_report(path, out, budget=budget)
    print(f"Cost report written: {report}")
    return 0


def _eval_list(root: Path) -> int:
    for cassette in eval_list_cassettes(root):
        print(cassette)
    return 0


def _eval_run(path: Path) -> int:
    result = eval_run_cassette(path)
    print(f"Cassette: {result.cassette_id}")
    print(f"Cases: {result.total}")
    print(f"Passed: {result.passed}")
    print(f"Failed: {result.failed}")
    for diff in result.diffs:
        print(f"Diff: {diff}")
    return 0 if result.failed == 0 else 1


def _eval_report(root: Path, out: Path) -> int:
    report = write_eval_report(root, out)
    print(f"Eval report written: {report}")
    return 0


def _audit_verify(path: Path) -> int:
    result = audit_verify_chain(path)
    print(f"OK: {result.ok}")
    print(f"Records: {result.record_count}")
    for error in result.errors:
        print(f"Error: {error}")
    return 0 if result.ok else 1


def _audit_redact(path: Path, out: Path) -> int:
    target = audit_redact_jsonl(path, out)
    print(f"Redacted JSONL written: {target}")
    return 0


def _audit_export(out: Path, traces: list[str] | None, evidence: list[str] | None) -> int:
    result = audit_export_bundle(
        out,
        traces=tuple(traces or ()),
        evidence=tuple(evidence or ()),
    )
    print(f"Audit bundle manifest: {result.manifest_path}")
    print(f"Exported: {len(result.exported_files)}")
    print(f"Missing: {len(result.missing_files)}")
    for missing in result.missing_files:
        print(f"Missing file: {missing}")
    return 0 if not result.missing_files else 1


def _audit_report(path: Path, out: Path) -> int:
    report = write_audit_report(path, out)
    print(f"Audit report written: {report}")
    return 0


def _skills_list(directory: Path, profile: str | None) -> int:
    for card in list_skills(directory, profile=profile):
        print(f"{card.card_id}\t{card.risk}\t{card.title}")
    return 0


def _skills_show(directory: Path, skill_id: str, profile: str) -> int:
    try:
        card = show_skill(directory, skill_id, profile=profile)
    except (PermissionError, KeyError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Skill: {card.card_id}")
    print(f"Title: {card.title}")
    print(f"Tags: {', '.join(card.tags)}")
    print(f"Risk: {card.risk}")
    print(f"Source: {card.source}")
    print(f"Summary: {card.summary}")
    return 0


def _skills_match(directory: Path, task: str, profile: str, limit: int) -> int:
    for card in match_skills(directory, task, profile=profile, limit=limit):
        print(f"{card.card_id}\t{card.title}")
    return 0


def _skills_export(directory: Path, out: Path, profile: str) -> int:
    exported = export_skills(directory, out, profile=profile)
    print(f"Skills exported: {len(exported)} -> {out}")
    return 0


def _skills_discover(directory: str | None, no_remote: bool, profile: str, out: str | None) -> int:
    from .tools.skills_discover import discover_skills

    result = discover_skills(directory, fetch_remote=not no_remote)
    for fr in result.federation_results:
        if not fr.ok:
            print(f"warn: marketplace {fr.marketplace_id} unavailable: {fr.error}", file=sys.stderr)
    if result.skillkit_result is not None and not result.skillkit_result.ok:
        print(f"warn: skillkit unavailable: {result.skillkit_result.error}", file=sys.stderr)
    from .catalog import export_cards, visible_cards

    cards = visible_cards(list(result.cards), profile)
    for card in cards:
        print(f"{card.card_id}\t{card.risk}\t{card.title}")
    if out:
        export_cards(cards, out)
    return 0


def _parse_scope_argument(value: str) -> dict[str, str]:
    name, _, rest = value.partition("=")
    access, _, description = rest.partition(":")
    access = access.strip().lower()
    if access not in {"read", "write", "destructive"}:
        raise ValueError(f"scope access must be read, write, or destructive, got {access!r} in {value!r}")
    return {"name": name.strip(), "access": access, "description": description.strip()}


def _integration_add(
    root: Path,
    connector_id: str,
    label: str | None,
    provider: str,
    scopes: list[str] | None,
    profiles: list[str] | None,
    docs_url: str,
) -> int:
    try:
        connector = add_connector(
            root,
            connector_id,
            label=label,
            provider=provider,
            scopes=[_parse_scope_argument(item) for item in scopes or []],
            profiles=tuple(profiles or ()) or None,
            docs_url=docs_url,
        )
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Connector registered: {connector.connector_id}")
    print(f"Scopes: {len(connector.scopes)}")
    return 0


def _integration_list(root: Path, profile: str) -> int:
    for connector in integration_connectors(root, profile=profile):
        accesses = ",".join(sorted({scope.access for scope in connector.scopes}))
        print(f"{connector.connector_id}\t{connector.provider}\t{accesses}")
    return 0


def _integration_show(root: Path, connector_id: str, profile: str) -> int:
    try:
        connector = show_connector(root, connector_id, profile=profile)
    except (PermissionError, KeyError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Connector: {connector.connector_id}")
    print(f"Label: {connector.label}")
    print(f"Provider: {connector.provider}")
    print(f"Docs: {connector.docs_url}")
    for scope in connector.scopes:
        print(f"Scope: {scope.name}\t{scope.access}\t{scope.description}")
    return 0


def _integration_explain(root: Path, connector_id: str, profile: str, scope: str) -> int:
    decision = integration_explain_policy(root, connector_id, profile=profile, scope=scope)
    print(f"Connector: {decision.connector_id}")
    print(f"Scope: {decision.scope}")
    print(f"Profile: {decision.profile}")
    print(f"Decision: {decision.decision}")
    print(f"Reason: {decision.reason}")
    return 0


def _integration_request_access(root: Path, connector_id: str, profile: str, scopes: list[str] | None) -> int:
    try:
        request = integration_request_access(root, connector_id, profile=profile, scopes=tuple(scopes or ()))
    except (PermissionError, KeyError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print(f"Request: {request.request_id}")
    print(f"Status: {request.status}")
    print(f"Scopes: {', '.join(request.scopes)}")
    return 0


def _integration_audit(root: Path, limit: int) -> int:
    for event in integration_audit_recent(root, limit=limit):
        print(f"{event.created_at}\t{event.event_type}\t{event.actor}")
    return 0


def _bench_report(from_path: Path, csv_out: Path | None) -> int:
    """Load recorded bench results, build matrix + Pareto frontier, print report."""
    results = bench_load_results(from_path)
    if not results:
        print(f"Error: no results loaded from {from_path}", file=sys.stderr)
        return 1
    matrix = bench_build_matrix(results)
    report = bench_render_report(matrix)
    print(report.markdown)
    if csv_out is not None:
        csv_out.parent.mkdir(parents=True, exist_ok=True)
        csv_out.write_bytes(report.csv_bytes)
        print(f"CSV written: {csv_out}")
    return 0


def _bench_run(spec_path: Path, variants_path: Path, cache_dir: Path | None, out_path: Path | None) -> int:
    """Run bench variants offline-safe (skips honestly when keys are absent).

    Offline by default: with no provider keys set, all solo variants are
    recorded as 'skipped' with a clear reason — never crashed, never invented.
    """
    import os

    if not spec_path.exists():
        print(f"Error: spec file not found: {spec_path}", file=sys.stderr)
        return 1
    if not variants_path.exists():
        print(f"Error: variants file not found: {variants_path}", file=sys.stderr)
        return 1

    try:
        spec_raw = json.loads(spec_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"Error: cannot read spec: {exc}", file=sys.stderr)
        return 1

    try:
        variants_raw = json.loads(variants_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"Error: cannot read variants: {exc}", file=sys.stderr)
        return 1

    if not isinstance(spec_raw, dict):
        print("Error: spec must be a JSON object", file=sys.stderr)
        return 1
    if not isinstance(variants_raw, list):
        print("Error: variants must be a JSON array", file=sys.stderr)
        return 1

    spec = BenchBenchmarkSpec.from_dict(spec_raw)
    variants = [BenchVariant.from_dict(v) for v in variants_raw if isinstance(v, dict)]

    results = bench_run_matrix(
        spec,
        variants,
        executor=None,
        cache_dir=cache_dir,
        environ=dict(os.environ),
    )

    serialized = json.dumps([r.to_dict() for r in results], indent=2, sort_keys=True)

    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(serialized, encoding="utf-8")
        print(f"Results written: {out_path} ({len(results)} variants)")
    else:
        print(serialized)

    skipped = [r for r in results if r.status == "skipped"]
    if skipped:
        for r in skipped:
            print(f"skipped: {r.variant.label!r} — {r.reason}", file=sys.stderr)

    return 0


def _bench_judge(from_path: Path, spec_path: Path, out_path: Path | None) -> int:
    """Run quality judgment pass over recorded bench results.

    Offline-safe: with no provider keys, results are written with judged=False.
    """
    import os

    if not from_path.exists():
        print(f"Error: results file not found: {from_path}", file=sys.stderr)
        return 1

    if not spec_path.exists():
        print(f"Error: spec file not found: {spec_path}", file=sys.stderr)
        return 1

    try:
        spec_raw = json.loads(spec_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"Error: cannot read spec: {exc}", file=sys.stderr)
        return 1

    if not isinstance(spec_raw, dict):
        print("Error: spec must be a JSON object", file=sys.stderr)
        return 1

    spec = BenchBenchmarkSpec.from_dict(spec_raw)
    results = bench_load_results(from_path)
    if not results:
        print(f"Error: no results loaded from {from_path}", file=sys.stderr)
        return 1

    env = dict(os.environ)
    judged_results = [bench_judge_quality(spec, r, judge=None, environ=env) for r in results]

    serialized = json.dumps([r.to_dict() for r in judged_results], indent=2, sort_keys=True)

    if out_path is not None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(serialized, encoding="utf-8")
        print(f"Judged results written: {out_path} ({len(judged_results)} results)")
    else:
        print(serialized)

    not_judged = [r for r in judged_results if not r.judged]
    if not_judged:
        print(f"Note: {len(not_judged)} result(s) not judged (key absent or call failed)", file=sys.stderr)
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="werktools")
    parser.add_argument("--config", default=".werktools/hub.json")

    subparsers = parser.add_subparsers(dest="command")
    hub = subparsers.add_parser("hub")
    hub_subparsers = hub.add_subparsers(dest="hub_command")

    hub_init = hub_subparsers.add_parser("init")
    hub_init.add_argument("--community", action="store_true")
    hub_subparsers.add_parser("status")

    hub_doctor = hub_subparsers.add_parser("doctor")
    hub_doctor.add_argument("--json", dest="as_json", action="store_true")
    hub_doctor.add_argument("--probe-versions", dest="probe_versions", action="store_true")
    hub_doctor.add_argument("--host", dest="hosts", action="append", default=[])
    hub_doctor.add_argument("--detected-only", dest="detected_only", action="store_true")

    hub_reap = hub_subparsers.add_parser("reap")
    hub_reap.add_argument("--sidecar", default=None)
    hub_reap.add_argument("--ttl", type=float, default=0.0)

    hub_render = hub_subparsers.add_parser("render")
    hub_render.add_argument("--profile", default=None)
    hub_render.add_argument("--host", default="claude")
    hub_render.add_argument("--out", default=None)

    hub_registry = hub_subparsers.add_parser("registry")
    hub_registry_subparsers = hub_registry.add_subparsers(dest="registry_command")
    hub_reg_search = hub_registry_subparsers.add_parser("search")
    hub_reg_search.add_argument("--query", default="")
    hub_reg_install = hub_registry_subparsers.add_parser("install")
    hub_reg_install.add_argument("--query", required=True)
    hub_reg_install.add_argument("--gate-root", default=".werktools/integration-gate")
    hub_reg_approve = hub_registry_subparsers.add_parser("approve")
    hub_reg_approve.add_argument("--request-id", required=True)
    hub_reg_approve.add_argument("--gate-root", default=".werktools/integration-gate")
    hub_reg_approve.add_argument("--hub-config", required=True)
    hub_reg_build = hub_registry_subparsers.add_parser("build")
    hub_reg_build.add_argument("--skills-dir", default=None)
    hub_reg_list = hub_registry_subparsers.add_parser("list")
    hub_reg_list.add_argument("--category", default=None)
    hub_reg_list.add_argument("--deluxe", action="store_true")
    hub_reg_select = hub_registry_subparsers.add_parser("select")
    hub_reg_select.add_argument("task")
    hub_reg_select.add_argument("--budget", type=int, default=8)

    hub_onboard = hub_subparsers.add_parser("onboard")
    hub_onboard.add_argument("--apply", action="store_true", help="Write adopted connectors to hub.json (default: dry-run)")
    hub_onboard.add_argument("--host", dest="onboard_host", default=None, help="Restrict discovery to one host id")
    hub_onboard.add_argument("--home", default=None, help="Override home directory (for testing)")

    hub_export = hub_subparsers.add_parser("export-rules")
    hub_export.add_argument("--agents-md", required=True)
    hub_export.add_argument("--skills-dir", required=True)
    hub_export.add_argument("--out", required=True)
    hub_export.add_argument("--profile", default=None)
    hub_export.add_argument("--host", default="claude")

    hub_serve = hub_subparsers.add_parser("serve")
    hub_serve.add_argument("--profile")
    hub_serve.add_argument("--status-port", type=int, default=None)
    # Accept --config after the subcommand too (matches the README MCP-host
    # snippet `hub serve --profile X --config path`). SUPPRESS so it only sets
    # args.config when explicitly given and never clobbers the top-level value.
    hub_serve.add_argument("--config", default=argparse.SUPPRESS)

    hub_pool_status = hub_subparsers.add_parser("pool-status")
    hub_pool_status.add_argument("--profile", default=None)

    hub_dashboard = hub_subparsers.add_parser("dashboard")
    hub_dashboard.add_argument("--host", default="127.0.0.1")
    hub_dashboard.add_argument("--port", type=int, default=7879)
    hub_dashboard.add_argument("--open", action="store_true")
    hub_dashboard.add_argument("--profile", default=None)

    hub_approvals = hub_subparsers.add_parser("approvals")
    hub_approvals_subparsers = hub_approvals.add_subparsers(dest="approvals_command")
    hub_approvals_subparsers.add_parser("list")
    hub_approvals_approve = hub_approvals_subparsers.add_parser("approve")
    hub_approvals_approve.add_argument("request_id")
    hub_approvals_deny = hub_approvals_subparsers.add_parser("deny")
    hub_approvals_deny.add_argument("request_id")

    tools = hub_subparsers.add_parser("tools")
    tools.add_argument("--profile")

    policy = hub_subparsers.add_parser("policy")
    policy_subparsers = policy.add_subparsers(dest="policy_command")
    explain_parser = policy_subparsers.add_parser("explain")
    explain_parser.add_argument("tool_id")
    explain_parser.add_argument("--profile")

    capability = subparsers.add_parser("capability")
    capability_subparsers = capability.add_subparsers(dest="capability_command")

    capability_subparsers.add_parser("list")

    capability_show = capability_subparsers.add_parser("show")
    capability_show.add_argument("capability_id")

    capability_classify = capability_subparsers.add_parser("classify")
    capability_classify.add_argument("manifest")

    capability_export = capability_subparsers.add_parser("export")
    capability_export.add_argument("--out", required=True)

    truth = subparsers.add_parser("truth")
    truth_subparsers = truth.add_subparsers(dest="truth_command")

    truth_scan = truth_subparsers.add_parser("scan")
    truth_scan.add_argument("--repo", default=".")

    truth_report = truth_subparsers.add_parser("report")
    truth_report.add_argument("--repo", default=".")
    truth_report.add_argument("--out", required=True)

    mine = subparsers.add_parser("mine")
    mine_subparsers = mine.add_subparsers(dest="mine_command")

    mine_extract = mine_subparsers.add_parser("extract")
    mine_extract.add_argument("file")
    mine_extract.add_argument("--out", required=True)
    mine_extract.add_argument("--topic")

    mine_index = mine_subparsers.add_parser("index")
    mine_index.add_argument("dir")

    mine_query = mine_subparsers.add_parser("query")
    mine_query.add_argument("query")
    mine_query.add_argument("--dir", default=".werktools/mine")

    mine_report = mine_subparsers.add_parser("report")
    mine_report.add_argument("--dir", default=".werktools/mine")
    mine_report.add_argument("--out", required=True)
    mine_report.add_argument("--topic")

    trace = subparsers.add_parser("trace")
    trace_subparsers = trace.add_subparsers(dest="trace_command")

    trace_append = trace_subparsers.add_parser("append")
    trace_append.add_argument("--file", default=".werktools/trace.jsonl")
    trace_append.add_argument("--type", dest="event_type", required=True)
    trace_append.add_argument("--actor")
    trace_append.add_argument("--source-id")
    trace_append.add_argument("--payload")
    trace_append.add_argument("--no-hash-chain", action="store_true")

    trace_recent = trace_subparsers.add_parser("recent")
    trace_recent.add_argument("--file", default=".werktools/trace.jsonl")
    trace_recent.add_argument("--limit", type=int, default=20)

    trace_verify = trace_subparsers.add_parser("verify")
    trace_verify.add_argument("file")

    trace_export = trace_subparsers.add_parser("export")
    trace_export.add_argument("file")
    trace_export.add_argument("--out", required=True)
    trace_export.add_argument("--type", dest="event_type")
    trace_export.add_argument("--actor")
    trace_export.add_argument("--source-id")

    vault = subparsers.add_parser("vault")
    vault_subparsers = vault.add_subparsers(dest="vault_command")

    vault_init = vault_subparsers.add_parser("init")
    vault_init.add_argument("--dir", default=".werktools/vault")

    vault_add_source = vault_subparsers.add_parser("add-source")
    vault_add_source.add_argument("source")
    vault_add_source.add_argument("--dir", default=".werktools/vault")
    vault_add_source.add_argument("--label", required=True)
    vault_add_source.add_argument("--class", dest="classification", default="internal")
    vault_add_source.add_argument("--owner", default="")
    vault_add_source.add_argument("--profile", action="append")

    vault_sources_parser = vault_subparsers.add_parser("sources")
    vault_sources_parser.add_argument("--dir", default=".werktools/vault")
    vault_sources_parser.add_argument("--profile")

    vault_search_parser = vault_subparsers.add_parser("search")
    vault_search_parser.add_argument("query")
    vault_search_parser.add_argument("--dir", default=".werktools/vault")
    vault_search_parser.add_argument("--profile", default="default")
    vault_search_parser.add_argument("--limit", type=int, default=10)

    vault_show_parser = vault_subparsers.add_parser("show")
    vault_show_parser.add_argument("item_id")
    vault_show_parser.add_argument("--dir", default=".werktools/vault")
    vault_show_parser.add_argument("--profile", default="default")
    vault_show_parser.add_argument("--reveal-secrets", action="store_true")

    vault_explain = vault_subparsers.add_parser("explain-access")
    vault_explain.add_argument("source_id")
    vault_explain.add_argument("--dir", default=".werktools/vault")
    vault_explain.add_argument("--profile", default="default")

    vault_audit = vault_subparsers.add_parser("audit")
    vault_audit_subparsers = vault_audit.add_subparsers(dest="vault_audit_command")
    vault_audit_tail = vault_audit_subparsers.add_parser("tail")
    vault_audit_tail.add_argument("--dir", default=".werktools/vault")
    vault_audit_tail.add_argument("--limit", type=int, default=20)

    data = subparsers.add_parser("data")
    data_subparsers = data.add_subparsers(dest="data_command")

    data_add_source = data_subparsers.add_parser("add-source")
    data_add_source.add_argument("sqlite_path")
    data_add_source.add_argument("--dir", default=".werktools/data-gate")
    data_add_source.add_argument("--source", required=True)
    data_add_source.add_argument("--label")
    data_add_source.add_argument("--table", action="append", required=True)
    data_add_source.add_argument("--mask", action="append")
    data_add_source.add_argument("--profile", action="append")
    data_add_source.add_argument("--limit", type=int, default=50)

    data_sources_parser = data_subparsers.add_parser("sources")
    data_sources_parser.add_argument("--dir", default=".werktools/data-gate")
    data_sources_parser.add_argument("--profile", default="default")

    data_schema_parser = data_subparsers.add_parser("schema")
    data_schema_parser.add_argument("source")
    data_schema_parser.add_argument("--dir", default=".werktools/data-gate")
    data_schema_parser.add_argument("--profile", default="default")

    data_preview = data_subparsers.add_parser("preview")
    data_preview.add_argument("--source", required=True)
    data_preview.add_argument("--intent", required=True)
    data_preview.add_argument("--dir", default=".werktools/data-gate")
    data_preview.add_argument("--profile", default="default")

    data_read = data_subparsers.add_parser("read")
    data_read.add_argument("--preview-id", required=True)
    data_read.add_argument("--dir", default=".werktools/data-gate")
    data_read.add_argument("--profile", default="default")

    data_audit = data_subparsers.add_parser("audit")
    data_audit.add_argument("--dir", default=".werktools/data-gate")
    data_audit.add_argument("--limit", type=int, default=20)

    swarm = subparsers.add_parser("swarm")
    swarm_subparsers = swarm.add_subparsers(dest="swarm_command")

    swarm_plan = swarm_subparsers.add_parser("plan")
    swarm_plan.add_argument("goal_file")
    swarm_plan.add_argument("--out", default=".werktools/swarm")
    swarm_plan.add_argument("--repo", default=".")
    swarm_plan.add_argument("--agents", type=int, default=3)

    swarm_packet = swarm_subparsers.add_parser("packet")
    swarm_packet.add_argument("agent_id")
    swarm_packet.add_argument("--dir", default=".werktools/swarm")

    swarm_collect = swarm_subparsers.add_parser("collect")
    swarm_collect.add_argument("reports_dir")

    swarm_review = swarm_subparsers.add_parser("review")
    swarm_review.add_argument("reports_dir")

    cost = subparsers.add_parser("cost")
    cost_subparsers = cost.add_subparsers(dest="cost_command")

    cost_record = cost_subparsers.add_parser("record")
    cost_record.add_argument("path")
    cost_record.add_argument("--mission", default="")
    cost_record.add_argument("--task", default="")
    cost_record.add_argument("--tool", default="")
    cost_record.add_argument("--model", default="")
    cost_record.add_argument("--amount")
    cost_record.add_argument("--currency", default="USD")

    cost_rollup = cost_subparsers.add_parser("rollup")
    cost_rollup.add_argument("path")

    cost_budget = cost_subparsers.add_parser("budget-check")
    cost_budget.add_argument("path")
    cost_budget.add_argument("--budget", required=True)

    cost_report = cost_subparsers.add_parser("report")
    cost_report.add_argument("path")
    cost_report.add_argument("--out", required=True)
    cost_report.add_argument("--budget")

    eval_parser = subparsers.add_parser("eval")
    eval_subparsers = eval_parser.add_subparsers(dest="eval_command")

    eval_list = eval_subparsers.add_parser("list")
    eval_list.add_argument("dir", nargs="?", default="tests/cassettes")

    eval_run = eval_subparsers.add_parser("run")
    eval_run.add_argument("cassette")

    eval_report = eval_subparsers.add_parser("report")
    eval_report.add_argument("--dir", default="tests/cassettes")
    eval_report.add_argument("--out", required=True)

    audit = subparsers.add_parser("audit")
    audit_subparsers = audit.add_subparsers(dest="audit_command")

    audit_verify = audit_subparsers.add_parser("verify")
    audit_verify.add_argument("path")

    audit_redact = audit_subparsers.add_parser("redact")
    audit_redact.add_argument("path")
    audit_redact.add_argument("--out", required=True)

    audit_export = audit_subparsers.add_parser("export")
    audit_export.add_argument("--out", required=True)
    audit_export.add_argument("--trace", action="append")
    audit_export.add_argument("--evidence", action="append")

    audit_report = audit_subparsers.add_parser("report")
    audit_report.add_argument("path")
    audit_report.add_argument("--out", required=True)

    canon = subparsers.add_parser("canon")
    canon_subparsers = canon.add_subparsers(dest="canon_command")
    canon_check = canon_subparsers.add_parser("check")
    canon_check.add_argument("--repo", default=".")
    canon_check.add_argument("--strict", action="store_true")
    canon_gen = canon_subparsers.add_parser("gen")
    canon_gen_subparsers = canon_gen.add_subparsers(dest="canon_gen_command")
    canon_gen_agents = canon_gen_subparsers.add_parser("agents")
    canon_gen_agents.add_argument("--repo", default=".")
    canon_gen_agents.add_argument("--out", default=None)
    canon_gen_agents.add_argument("--dry-run", action="store_true")
    canon_gen_spec = canon_gen_subparsers.add_parser("spec")
    canon_gen_spec.add_argument("name")
    canon_gen_spec.add_argument("--out-dir", default=None)
    canon_gen_spec.add_argument("--kind", default="mcp")
    canon_gen_spec.add_argument("--dry-run", action="store_true")

    models = subparsers.add_parser("models")
    models_subparsers = models.add_subparsers(dest="models_command")
    models_list = models_subparsers.add_parser("list")
    models_list.add_argument("--workers", required=True)
    models_report = models_subparsers.add_parser("report")
    models_report.add_argument("--workers", required=True)
    models_call = models_subparsers.add_parser("call")
    models_call.add_argument("--workers", required=True)
    models_call.add_argument("--worker", required=True)
    models_call.add_argument("--model", required=True)
    models_call.add_argument("--prompt", required=True)

    skills = subparsers.add_parser("skills")
    skills_subparsers = skills.add_subparsers(dest="skills_command")

    skills_list = skills_subparsers.add_parser("list")
    skills_list.add_argument("--dir", default=".werktools/skills")
    skills_list.add_argument("--profile", default="default")
    skills_list.add_argument("--all", action="store_true", help="operator view: ignore profile visibility")

    skills_show = skills_subparsers.add_parser("show")
    skills_show.add_argument("skill_id")
    skills_show.add_argument("--dir", default=".werktools/skills")
    skills_show.add_argument("--profile", default="default")

    skills_match = skills_subparsers.add_parser("match")
    skills_match.add_argument("task")
    skills_match.add_argument("--dir", default=".werktools/skills")
    skills_match.add_argument("--profile", default="default")
    skills_match.add_argument("--limit", type=int, default=5)

    skills_export = skills_subparsers.add_parser("export")
    skills_export.add_argument("--dir", default=".werktools/skills")
    skills_export.add_argument("--out", required=True)
    skills_export.add_argument("--profile", default="default")

    skills_discover = skills_subparsers.add_parser("discover")
    skills_discover.add_argument("--dir", default=None)
    skills_discover.add_argument("--no-remote", action="store_true")
    skills_discover.add_argument("--profile", default="default")
    skills_discover.add_argument("--out", default=None)

    integration = subparsers.add_parser("integration")
    integration_subparsers = integration.add_subparsers(dest="integration_command")

    integration_add = integration_subparsers.add_parser("add")
    integration_add.add_argument("connector_id")
    integration_add.add_argument("--dir", default=".werktools/integration-gate")
    integration_add.add_argument("--label")
    integration_add.add_argument("--provider", default="")
    integration_add.add_argument("--scope", action="append", help="name=access:description")
    integration_add.add_argument("--profile", action="append", dest="profiles")
    integration_add.add_argument("--docs-url", default="")

    integration_list = integration_subparsers.add_parser("list")
    integration_list.add_argument("--dir", default=".werktools/integration-gate")
    integration_list.add_argument("--profile", default="default")

    integration_show = integration_subparsers.add_parser("show")
    integration_show.add_argument("connector_id")
    integration_show.add_argument("--dir", default=".werktools/integration-gate")
    integration_show.add_argument("--profile", default="default")

    integration_explain = integration_subparsers.add_parser("explain")
    integration_explain.add_argument("connector_id")
    integration_explain.add_argument("--dir", default=".werktools/integration-gate")
    integration_explain.add_argument("--profile", default="default")
    integration_explain.add_argument("--scope", required=True)

    integration_request = integration_subparsers.add_parser("request-access")
    integration_request.add_argument("connector_id")
    integration_request.add_argument("--dir", default=".werktools/integration-gate")
    integration_request.add_argument("--profile", default="default")
    integration_request.add_argument("--scope", action="append", dest="scopes")

    integration_audit = integration_subparsers.add_parser("audit")
    integration_audit.add_argument("--dir", default=".werktools/integration-gate")
    integration_audit.add_argument("--limit", type=int, default=20)

    bench = subparsers.add_parser("bench")
    bench_subparsers = bench.add_subparsers(dest="bench_command")
    bench_report = bench_subparsers.add_parser("report")
    bench_report.add_argument("--from", dest="from_path", required=True, metavar="RESULTS_JSON")
    bench_report.add_argument("--csv", dest="csv_out", default=None, metavar="OUT_CSV")

    bench_run = bench_subparsers.add_parser("run")
    bench_run.add_argument("--spec", required=True, metavar="SPEC_JSON")
    bench_run.add_argument("--variants", required=True, metavar="VARIANTS_JSON")
    bench_run.add_argument("--cache", dest="cache_dir", default=None, metavar="CACHE_DIR")
    bench_run.add_argument("--out", dest="out_path", default=None, metavar="RESULTS_JSON")

    bench_judge = bench_subparsers.add_parser("judge")
    bench_judge.add_argument("--from", dest="from_path", required=True, metavar="RESULTS_JSON")
    bench_judge.add_argument("--spec", required=True, metavar="SPEC_JSON")
    bench_judge.add_argument("--out", dest="out_path", default=None, metavar="JUDGED_JSON")

    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the werktools CLI and return an exit code."""
    parser = _parser()
    args = parser.parse_args(argv)
    config_path = Path(args.config)

    if args.command == "hub":
        if args.hub_command == "init":
            return _hub_init(config_path, getattr(args, "community", False))
        if args.hub_command == "serve":
            return _hub_serve(config_path, args.profile, args.status_port)
        if args.hub_command == "pool-status":
            return _hub_pool_status(config_path, args.profile)
        if args.hub_command == "dashboard":
            return _hub_dashboard(config_path, args.host, args.port, args.open)
        if args.hub_command == "doctor":
            return _hub_doctor(
                config_path,
                as_json=args.as_json,
                probe_versions=args.probe_versions,
                hosts=tuple(args.hosts),
                detected_only=args.detected_only,
            )
        if args.hub_command == "reap":
            return _hub_reap(config_path, args.sidecar, args.ttl)
        if args.hub_command == "render":
            return _hub_render(config_path, args.profile, args.host, args.out)
        if args.hub_command == "onboard":
            return _hub_onboard(config_path, args.apply, args.onboard_host, args.home)
        if args.hub_command == "export-rules":
            return _hub_export_rules(config_path, args.agents_md, args.skills_dir, args.out, args.profile, args.host)
        if args.hub_command == "registry":
            if args.registry_command == "search":
                return _hub_registry_search(args.query)
            if args.registry_command == "install":
                return _hub_registry_install(config_path, args.gate_root, args.query)
            if args.registry_command == "approve":
                return _hub_registry_approve(config_path, args.gate_root, args.request_id, args.hub_config)
            if args.registry_command == "build":
                return _hub_registry_build(config_path, args.skills_dir)
            if args.registry_command == "list":
                return _hub_registry_list(config_path, args.category, args.deluxe)
            if args.registry_command == "select":
                return _hub_registry_select(config_path, args.task, args.budget)
        if args.hub_command == "approvals":
            if args.approvals_command == "list":
                return _hub_approvals_list(config_path)
            if args.approvals_command == "approve":
                return _hub_approvals_approve(config_path, args.request_id)
            if args.approvals_command == "deny":
                return _hub_approvals_deny(config_path, args.request_id)
        if args.hub_command == "status":
            return _hub_status(config_path)
        if args.hub_command == "tools":
            return _hub_tools(config_path, args.profile)
        if args.hub_command == "policy" and args.policy_command == "explain":
            return _hub_policy_explain(config_path, args.tool_id, args.profile)

    if args.command == "capability":
        if args.capability_command == "list":
            return _capability_list(config_path)
        if args.capability_command == "show":
            return _capability_show(config_path, args.capability_id)
        if args.capability_command == "classify":
            return _capability_classify(Path(args.manifest))
        if args.capability_command == "export":
            return _capability_export(config_path, Path(args.out))

    if args.command == "truth":
        if args.truth_command == "scan":
            return _truth_scan(Path(args.repo))
        if args.truth_command == "report":
            return _truth_report(Path(args.repo), Path(args.out))

    if args.command == "mine":
        if args.mine_command == "extract":
            return _mine_extract(Path(args.file), Path(args.out), args.topic)
        if args.mine_command == "index":
            return _mine_index(Path(args.dir))
        if args.mine_command == "query":
            return _mine_query(Path(args.dir), args.query)
        if args.mine_command == "report":
            return _mine_report(Path(args.dir), Path(args.out), args.topic)

    if args.command == "trace":
        if args.trace_command == "append":
            return _trace_append(
                Path(args.file),
                args.event_type,
                args.actor,
                args.payload,
                args.source_id,
                not args.no_hash_chain,
            )
        if args.trace_command == "recent":
            return _trace_recent(Path(args.file), args.limit)
        if args.trace_command == "verify":
            return _trace_verify(Path(args.file))
        if args.trace_command == "export":
            return _trace_export(
                Path(args.file),
                Path(args.out),
                args.event_type,
                args.actor,
                args.source_id,
            )

    if args.command == "vault":
        if args.vault_command == "init":
            return _vault_init(Path(args.dir))
        if args.vault_command == "add-source":
            return _vault_add_source(
                Path(args.dir),
                Path(args.source),
                args.label,
                args.classification,
                args.owner,
                args.profile,
            )
        if args.vault_command == "sources":
            return _vault_sources(Path(args.dir), args.profile)
        if args.vault_command == "search":
            return _vault_search(Path(args.dir), args.query, args.profile, args.limit)
        if args.vault_command == "show":
            return _vault_show(Path(args.dir), args.item_id, args.profile, args.reveal_secrets)
        if args.vault_command == "explain-access":
            return _vault_explain_access(Path(args.dir), args.source_id, args.profile)
        if args.vault_command == "audit" and args.vault_audit_command == "tail":
            return _vault_audit_tail(Path(args.dir), args.limit)

    if args.command == "data":
        if args.data_command == "add-source":
            return _data_add_source(
                Path(args.dir),
                Path(args.sqlite_path),
                args.source,
                args.label,
                args.table,
                args.mask,
                args.profile,
                args.limit,
            )
        if args.data_command == "sources":
            return _data_sources(Path(args.dir), args.profile)
        if args.data_command == "schema":
            return _data_schema(Path(args.dir), args.source, args.profile)
        if args.data_command == "preview":
            return _data_preview(Path(args.dir), args.source, args.intent, args.profile)
        if args.data_command == "read":
            return _data_read(Path(args.dir), args.preview_id, args.profile)
        if args.data_command == "audit":
            return _data_audit(Path(args.dir), args.limit)

    if args.command == "swarm":
        if args.swarm_command == "plan":
            return _swarm_plan(Path(args.goal_file), Path(args.out), args.repo, args.agents)
        if args.swarm_command == "packet":
            return _swarm_packet(args.agent_id, Path(args.dir))
        if args.swarm_command == "collect":
            return _swarm_collect(Path(args.reports_dir))
        if args.swarm_command == "review":
            return _swarm_review(Path(args.reports_dir))

    if args.command == "cost":
        if args.cost_command == "record":
            return _cost_record(
                Path(args.path),
                args.mission,
                args.task,
                args.tool,
                args.model,
                args.amount,
                args.currency,
            )
        if args.cost_command == "rollup":
            return _cost_rollup(Path(args.path))
        if args.cost_command == "budget-check":
            return _cost_budget_check(Path(args.path), args.budget)
        if args.cost_command == "report":
            return _cost_report(Path(args.path), Path(args.out), args.budget)

    if args.command == "eval":
        if args.eval_command == "list":
            return _eval_list(Path(args.dir))
        if args.eval_command == "run":
            return _eval_run(Path(args.cassette))
        if args.eval_command == "report":
            return _eval_report(Path(args.dir), Path(args.out))

    if args.command == "audit":
        if args.audit_command == "verify":
            return _audit_verify(Path(args.path))
        if args.audit_command == "redact":
            return _audit_redact(Path(args.path), Path(args.out))
        if args.audit_command == "export":
            return _audit_export(Path(args.out), args.trace, args.evidence)
        if args.audit_command == "report":
            return _audit_report(Path(args.path), Path(args.out))

    if args.command == "canon":
        if args.canon_command == "check":
            return _canon_check(Path(args.repo), args.strict)
        if args.canon_command == "gen":
            if args.canon_gen_command == "agents":
                return _canon_gen_agents(Path(args.repo), args.out, args.dry_run)
            if args.canon_gen_command == "spec":
                return _canon_gen_spec(args.name, args.out_dir, args.kind, args.dry_run)

    if args.command == "models":
        if args.models_command == "list":
            return _models_list(Path(args.workers))
        if args.models_command == "report":
            return _models_report(config_path, Path(args.workers))
        if args.models_command == "call":
            return _models_call(config_path, Path(args.workers), args.worker, args.model, args.prompt)

    if args.command == "skills":
        if args.skills_command == "list":
            return _skills_list(Path(args.dir), None if args.all else args.profile)
        if args.skills_command == "show":
            return _skills_show(Path(args.dir), args.skill_id, args.profile)
        if args.skills_command == "match":
            return _skills_match(Path(args.dir), args.task, args.profile, args.limit)
        if args.skills_command == "export":
            return _skills_export(Path(args.dir), Path(args.out), args.profile)
        if args.skills_command == "discover":
            return _skills_discover(args.dir, args.no_remote, args.profile, args.out)

    if args.command == "integration":
        if args.integration_command == "add":
            return _integration_add(
                Path(args.dir),
                args.connector_id,
                args.label,
                args.provider,
                args.scope,
                args.profiles,
                args.docs_url,
            )
        if args.integration_command == "list":
            return _integration_list(Path(args.dir), args.profile)
        if args.integration_command == "show":
            return _integration_show(Path(args.dir), args.connector_id, args.profile)
        if args.integration_command == "explain":
            return _integration_explain(Path(args.dir), args.connector_id, args.profile, args.scope)
        if args.integration_command == "request-access":
            return _integration_request_access(Path(args.dir), args.connector_id, args.profile, args.scopes)
        if args.integration_command == "audit":
            return _integration_audit(Path(args.dir), args.limit)

    if args.command == "bench":
        if args.bench_command == "report":
            return _bench_report(Path(args.from_path), Path(args.csv_out) if args.csv_out else None)
        if args.bench_command == "run":
            return _bench_run(
                Path(args.spec),
                Path(args.variants),
                Path(args.cache_dir) if args.cache_dir else None,
                Path(args.out_path) if args.out_path else None,
            )
        if args.bench_command == "judge":
            return _bench_judge(
                Path(args.from_path),
                Path(args.spec),
                Path(args.out_path) if args.out_path else None,
            )

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
