"""Offline heuristic risk-classifier for MCP tool manifests."""

from __future__ import annotations

import re
import warnings

_RISK_ORDER = ("low", "medium", "high", "critical")

_SIGNAL_RULES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "shell/exec",
        "critical",
        (
            r"\bshell\b",
            r"\bexec\b",
            r"\bexecute\b",
            r"\bsubprocess\b",
            r"\beval\b",
            # "spawn a worker/thread" is concurrency vocabulary, not shell access.
            r"\bspawn\b(?!\s+(?:a\s+|an\s+|the\s+)?(?:worker|thread))",
            r"\bcmd\b",
            # bare "command" over-flags CLI help text, but run/system command
            # phrasing is genuine shell vocabulary.
            r"\brun(?:s|ning)?[_\-\s]+(?:a\s+|an\s+|any\s+|arbitrary\s+)?commands?\b",
            r"\bsystem[_\-\s]commands?\b",
            r"\bcommand[_\-\s](?:execution|injection)\b",
        ),
    ),
    (
        "injection-phrasing",
        "critical",
        (
            r"ignore\s+previous",
            r"system\s+prompt",
            r"disregard\s+(your\s+)?(previous\s+)?instructions?",
            r"forget\s+(all\s+)?previous",
            r"new\s+instructions?",
        ),
    ),
    (
        "secret/credential",
        "high",
        (
            r"\bapi[_\-\s]?key\b",
            r"\bpassword\b",
            r"\bsecret\b",
            # bare "token" is ML-tokenizer vocabulary; require a credential qualifier.
            r"\b(?:access|api|auth|bearer|oauth|refresh|secret|session)[_\-\s]?tokens?\b",
            r"\bcredential\b",
            r"\bauth[_\-]?key\b",
        ),
    ),
    (
        "fs-write/delete/destructive",
        "high",
        (
            r"\bdelete(?:[_-]|\b)",
            r"\bremove(?:[_-]|\b)",
            r"\bwrite(?:[_-]|\b)",
            r"\boverwrite\b",
            r"\btruncate\b",
            r"\bunlink\b",
            r"\berase\b",
            r"\bdestruct\b",
        ),
    ),
    (
        "network/fetch/url",
        "medium",
        (
            r"\bfetch\b",
            r"\burl\b",
            r"\bhttp\b",
            r"\bdownload\b",
            r"\brequest\b",
            r"\bwebhook\b",
            r"\bapi\b",
            r"\bendpoint\b",
        ),
    ),
)


def _bump_risk(current: str, candidate: str) -> str:
    try:
        candidate_idx = _RISK_ORDER.index(candidate)
    except ValueError:
        warnings.warn(
            f"_bump_risk: unknown risk level {candidate!r}; falling back to 'critical'",
            stacklevel=2,
        )
        return "critical"
    try:
        current_idx = _RISK_ORDER.index(current)
    except ValueError:
        warnings.warn(
            f"_bump_risk: unknown risk level {current!r}; falling back to 'critical'",
            stacklevel=2,
        )
        return "critical"
    if candidate_idx > current_idx:
        return candidate
    return current


def _collect_text(manifest: dict) -> str:
    parts: list[str] = []
    name = manifest.get("name")
    if isinstance(name, str):
        parts.append(name)
    description = manifest.get("description")
    if isinstance(description, str):
        parts.append(description)
    return " ".join(parts)


def _collect_schema_keys(manifest: dict) -> list[str]:
    schema = manifest.get("inputSchema")
    if not isinstance(schema, dict):
        return []
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return []
    return [key for key in properties if isinstance(key, str)]


def _collect_schema_descriptions(manifest: dict) -> list[str]:
    """Collect 'description' and 'title' string values from inputSchema.properties.

    Injection phrasing can be embedded in property metadata text, not just in
    key names or the tool's own description. Including them closes that evasion
    path.
    """
    schema = manifest.get("inputSchema")
    if not isinstance(schema, dict):
        return []
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return []
    texts: list[str] = []
    for prop_value in properties.values():
        if not isinstance(prop_value, dict):
            continue
        for field in ("description", "title"):
            val = prop_value.get(field)
            if isinstance(val, str):
                texts.append(val)
    return texts


def classify_tool(manifest: dict) -> dict:
    """Classify a tool manifest by static keyword heuristics only."""
    if not isinstance(manifest, dict):
        return {"risk": "low", "signals": [], "reasons": []}

    combined = " ".join([
        _collect_text(manifest),
        *_collect_schema_keys(manifest),
        *_collect_schema_descriptions(manifest),
    ])
    signals: list[str] = []
    reasons: list[str] = []
    risk = "low"

    for signal, signal_risk, patterns in _SIGNAL_RULES:
        matched = [
            pattern
            for pattern in patterns
            if re.search(pattern, combined, re.IGNORECASE)
        ]
        if not matched:
            continue

        signals.append(signal)
        reasons.append(f"{signal} detected (matched: {', '.join(matched[:3])})")
        risk = _bump_risk(risk, signal_risk)

    return {"risk": risk, "signals": signals, "reasons": reasons}
