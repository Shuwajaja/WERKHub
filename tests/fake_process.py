"""Fake downstream + lifecycle child factories for offline tests.

make_fake_downstream builds an in-process FastMCP server (guarded import,
so the module is safe to import without the [server] extra). The actual
FastMCP call only happens when the factory is invoked inside a test that is
already skipped when FastMCP is missing.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any


def make_fake_downstream(name: str, tools: list[dict[str, Any]]):
    """Return an in-process FastMCP server exposing the given tool specs.

    Each tool dict: {name, description, read_only, return, annotations?}.
    """
    from fastmcp import FastMCP

    server = FastMCP(name=name)
    for spec in tools:
        annotations = dict(spec.get("annotations") or {})
        if "read_only" in spec and "readOnlyHint" not in annotations:
            annotations["readOnlyHint"] = bool(spec["read_only"])
        return_value = spec.get("return", {})

        def _make(value):
            def _tool(**kwargs):
                return value

            return _tool

        fn = _make(return_value)
        fn.__name__ = str(spec["name"])
        fn.__doc__ = str(spec.get("description", ""))
        server.tool(annotations=annotations)(fn)
    return server


def make_fake_lifecycle_child() -> subprocess.Popen:
    """Spawn a real long-lived child process for PID/kill tests."""
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform.startswith("win") else 0
    return subprocess.Popen(
        [sys.executable, "-c", "import time\nwhile True:\n    time.sleep(1)"],
        creationflags=creationflags,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
