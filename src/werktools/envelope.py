"""Pure MCP result-envelope helpers.

Concept provenance: copy-by-concept from the WERKCommander MCP result
shape. No source code copied.
"""

from __future__ import annotations

import json


def ok(command: str, data: dict | None = None) -> dict:
    """Return a success envelope with a stable four-field shape."""
    return {
        "ok": True,
        "command": command,
        "data": data if data is not None else {},
        "error": None,
    }


def err(command: str, error: str, data: dict | None = None) -> dict:
    """Return a failure envelope with optional diagnostic data."""
    return {
        "ok": False,
        "command": command,
        "data": data,
        "error": error,
    }


def to_mcp_text(envelope: dict) -> dict:
    """Serialize an envelope as a single MCP text content item."""
    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(envelope, ensure_ascii=True),
            }
        ],
        "isError": not bool(envelope.get("ok", False)),
    }
