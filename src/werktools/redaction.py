"""Shared secret-redaction primitives.

Single audited surface for every werktools tool that masks secrets:
trace/audit payload redaction, hub ledger redaction, and vault text
masking all import from here instead of keeping private marker lists.
"""

from __future__ import annotations

from typing import Any

REDACTED = "[redacted]"

# Markers matched as substrings of a normalized key (lowercase, "-" -> "_").
_KEY_SUBSTRING_MARKERS = (
    "access_key",
    "api_key",
    "apikey",
    "authorization",
    "auth_key",
    "bearer",
    "client_secret",
    "credential",
    "jwt",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "token",
)

# Short, ambiguous markers that must match a whole "_"-separated segment so
# that keys like "author" or "authored_by" stay untouched.
_KEY_SEGMENT_MARKERS = ("auth",)

# Markers matched as substrings of a normalized text line for mask_secret_text.
_TEXT_MARKERS = (
    "access_key",
    "api_key",
    "apikey",
    "authorization",
    "bearer",
    "credential",
    "jwt",
    "password",
    "private_key",
    "secret",
    "token",
)

_BLOCK_SCALAR_VALUES = {"", "|", ">", "|-", ">-", "|+", ">+"}


def is_secret_key(key: str) -> bool:
    """Return True when a payload key looks like it holds a secret."""
    normalized = key.lower().replace("-", "_")
    if any(marker in normalized for marker in _KEY_SUBSTRING_MARKERS):
        return True
    segments = normalized.split("_")
    return any(marker in segments for marker in _KEY_SEGMENT_MARKERS)


def redact_payload(value: Any) -> Any:
    """Redact secret-like keys from a JSON-compatible payload."""
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            text_key = str(key)
            redacted[text_key] = REDACTED if is_secret_key(text_key) else redact_payload(item)
        return redacted
    if isinstance(value, (list, tuple)):
        return [redact_payload(item) for item in value]
    return value


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def mask_secret_text(text: str) -> str:
    """Mask secret-looking lines in free text, including YAML block scalars.

    When a masked key has no inline value (e.g. ``password: |``), the
    following deeper-indented continuation lines are masked as well.
    """
    lines: list[str] = []
    block_indent: int | None = None
    for line in text.splitlines():
        if block_indent is not None:
            if line.strip() and _indent(line) > block_indent:
                lines.append(REDACTED)
                continue
            block_indent = None
        normalized = line.lower().replace("-", "_")
        if any(marker in normalized for marker in _TEXT_MARKERS):
            if ":" in line:
                key, _, value = line.partition(":")
                lines.append(f"{key}: {REDACTED}")
                if value.strip() in _BLOCK_SCALAR_VALUES:
                    block_indent = _indent(line)
            elif "=" in line:
                lines.append(f"{line.split('=', 1)[0]}= {REDACTED}")
            else:
                lines.append(REDACTED)
            continue
        lines.append(line)
    return "\n".join(lines)
