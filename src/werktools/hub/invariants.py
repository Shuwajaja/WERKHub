"""Machine-checkable guardrail invariants for CI and the autonomous loop.

Each check returns a list of violation strings (empty list = ok). The checks
are pure text/metadata scans — no imports of the scanned modules, no network,
no side effects — so they run in well under a second as a pre-pytest gate.
"""

from __future__ import annotations

import io
import re
import tokenize
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
_SRC_ROOT = Path(__file__).resolve().parents[2]
_PYPROJECT = _REPO_ROOT / "pyproject.toml"

# Module-scope import of an optional dependency. The codebase's canonical
# form is an indented `try: from fastmcp import ...` block (column-4), so the
# pattern allows leading whitespace; it still rejects function-body imports
# because those are guarded by the allowlist of the two entry files, not by
# indentation alone.
_FASTMCP_IMPORT = re.compile(r"^\s*(?:import fastmcp|from fastmcp\b)", re.MULTILINE)
# Daemon-ish constructs that must never run as a library side effect.
_DAEMON_PATTERNS = (
    re.compile(r"\bthreading\.Thread\b"),
    re.compile(r"\batexit\.register\b"),
    re.compile(r"\bwhile\s+True\b"),
    re.compile(r"\basyncio\.create_task\b"),
    re.compile(r"\bThreadingHTTPServer\b"),
    re.compile(r"\bForkingHTTPServer\b"),
)
_FASTMCP_ALLOWED = {"hub/relay.py", "server.py"}


def _core_py_files(root: Path) -> list[Path]:
    return [p for p in sorted(root.rglob("*.py")) if "__pycache__" not in p.parts]


def _rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def check_core_dependencies_empty(pyproject: Path = _PYPROJECT) -> list[str]:
    """The core must declare no runtime dependencies."""
    text = pyproject.read_text(encoding="utf-8")
    match = re.search(r"^dependencies\s*=\s*\[(.*?)\]", text, re.MULTILINE | re.DOTALL)
    if match is None:
        return ["pyproject.toml has no [project] dependencies field"]
    inner = match.group(1).strip()
    if inner:
        return [f"core dependencies must be empty, found: {inner}"]
    return []


def check_lifecycle_extra_empty(pyproject: Path = _PYPROJECT) -> list[str]:
    """The lifecycle extra is a stdlib marker — it must stay empty."""
    text = pyproject.read_text(encoding="utf-8")
    match = re.search(r"^lifecycle\s*=\s*\[(.*?)\]", text, re.MULTILINE | re.DOTALL)
    if match is None:
        return ["pyproject.toml has no lifecycle extra"]
    if match.group(1).strip():
        return [f"lifecycle extra must be empty, found: {match.group(1).strip()}"]
    return []


def check_no_module_scope_fastmcp(src_root: Path = _SRC_ROOT, exclude: set[str] | None = None) -> list[str]:
    """FastMCP may only be imported at module scope in the two allowed files."""
    allowed = exclude if exclude is not None else _FASTMCP_ALLOWED
    violations: list[str] = []
    werktools_root = src_root / "werktools"
    base = werktools_root if werktools_root.exists() else src_root
    for path in _core_py_files(base):
        rel = _rel(path, base)
        if rel in allowed:
            continue
        if _FASTMCP_IMPORT.search(path.read_text(encoding="utf-8")):
            violations.append(f"module-scope fastmcp import outside the allowlist: {rel}")
    return violations


# Serve/dashboard entry points may run a daemon thread (the status/SSE server)
# — they are the explicit "inside hub serve" location, never the stdlib core.
_DAEMON_ALLOWED = {"hub/server.py", "hub/dashboard.py"}


def check_no_in_core_daemon(src_root: Path = _SRC_ROOT, exclude: set[str] | None = None) -> list[str]:
    """No background thread / atexit / while-True / create_task in core modules."""
    allowed = exclude if exclude is not None else _DAEMON_ALLOWED
    violations: list[str] = []
    werktools_root = src_root / "werktools"
    base = werktools_root if werktools_root.exists() else src_root
    for path in _core_py_files(base):
        rel = _rel(path, base)
        if rel in allowed:
            continue
        text = path.read_text(encoding="utf-8")
        # Strip both comments AND string literals using tokenize so that daemon
        # patterns inside docstrings (e.g. ``'while True'``) do not trigger
        # false positives.  Falls back to the original text on tokenize errors
        # (fail-safe: better to scan extra text than to skip a real violation).
        # We replace token chars in-place by character position to preserve all
        # surrounding whitespace, so patterns like ``while True`` remain intact
        # when they appear in real code (not just in strings/comments).
        try:
            _STRING_TYPES = {tokenize.STRING, tokenize.COMMENT}
            src_lines = text.splitlines(keepends=True)
            out_lines: list[list[str]] = [list(ln) for ln in src_lines]
            for tok in tokenize.generate_tokens(io.StringIO(text).readline):
                if tok.type not in _STRING_TYPES:
                    continue
                sr, sc = tok.start
                er, ec = tok.end
                for row in range(sr - 1, er):
                    if row >= len(out_lines):
                        break
                    s_col = sc if row == sr - 1 else 0
                    e_col = ec if row == er - 1 else len(out_lines[row])
                    for col in range(s_col, min(e_col, len(out_lines[row]))):
                        out_lines[row][col] = " "
            stripped = "".join("".join(ln) for ln in out_lines)
        except tokenize.TokenError:
            stripped = text
        for pattern in _DAEMON_PATTERNS:
            if pattern.search(stripped):
                violations.append(f"daemon-ish construct {pattern.pattern!r} in core module: {rel}")
    return violations


def check_event_names_unique() -> list[str]:
    """EVENT_NAMES must be a non-empty tuple of unique strings."""
    from .contracts import EVENT_NAMES

    if not isinstance(EVENT_NAMES, tuple) or not EVENT_NAMES:
        return ["EVENT_NAMES must be a non-empty tuple"]
    seen: set[str] = set()
    dupes: set[str] = set()
    for name in EVENT_NAMES:
        if name in seen:
            dupes.add(name)
        seen.add(name)
    return [f"duplicate event names: {', '.join(sorted(dupes))}"] if dupes else []


_SUBPROCESS_MODULE_SCOPE = re.compile(r"^(?:import subprocess|from subprocess\b)", re.MULTILINE)


def check_runtimes_subprocess_deferred(src_root: Path = _SRC_ROOT) -> list[str]:
    """Enforce the runtimes.py contract: subprocess must be imported lazily inside probe_one.

    The runtimes.py docstring explicitly states 'subprocess is imported lazily
    inside probe_one'; a module-scope import would violate the zero-I/O-on-import
    guarantee and potentially trigger subprocess availability at import time.
    """
    werktools_root = src_root / "werktools"
    base = werktools_root if werktools_root.exists() else src_root
    runtimes = base / "hub" / "runtimes.py"
    if not runtimes.exists():
        return [f"runtimes.py not found at expected path: {runtimes}"]
    text = runtimes.read_text(encoding="utf-8")
    if _SUBPROCESS_MODULE_SCOPE.search(text):
        return ["runtimes.py has a module-scope 'subprocess' import; must be deferred inside probe_one"]
    return []


def run_all() -> dict[str, list[str]]:
    """Run every invariant; return {check_name: violations}."""
    return {
        "core_dependencies_empty": check_core_dependencies_empty(),
        "lifecycle_extra_empty": check_lifecycle_extra_empty(),
        "no_module_scope_fastmcp": check_no_module_scope_fastmcp(),
        "no_in_core_daemon": check_no_in_core_daemon(),
        "event_names_unique": check_event_names_unique(),
        "runtimes_subprocess_deferred": check_runtimes_subprocess_deferred(),
    }
