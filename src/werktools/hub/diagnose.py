"""Classify why a downstream MCP failed discovery, from its error + stderr.

No MCP declares "I am single-instance" or "I need a working directory" -- the
only signal is the failure itself. This maps the captured discovery error plus
the downstream's own stderr (see ``relay.collect_startup_stderr``) to a probable
cause, whether the hub can run the server as-is, and an operator-facing remedy.

The verdict is a HEURISTIC guess over the error text, never a verified fact
(``diagnosis_heuristic`` is always True). Fail-closed: an unrecognized failure
is reported as ``supported=False, cause="unknown"`` -- never silently "fine".
Pure stdlib, no side effects, ASCII-only strings (the verdict is printed/logged),
so it lives in the dependency-free core and is testable on its own.
"""

from __future__ import annotations

from typing import Any

# (cause, supported, remedy, signature substrings). Checked in order; first
# match wins, so order encodes precedence. needs_auth is FIRST so an explicit
# 401/unauthorized is not swallowed by the broad "access denied" lock bucket.
# All needles are lower-case; matching is case-insensitive over "error\nstderr".
_SIGNATURES: tuple[tuple[str, bool, str, tuple[str, ...]], ...] = (
    (
        "needs_auth",
        False,
        "Needs credentials: set the required key as a connector env var "
        "(presence-only -- the hub never stores the value).",
        (
            "unauthorized",
            "401 unauthorized",
            "http 401",
            "authentication failed",
            "not authenticated",
            "login required",
            "invalid api key",
            "missing api key",
            "invalid token",
        ),
    ),
    (
        "single_instance",
        False,
        "Access/resource denied -- two likely causes, check in this order: "
        "(1) an exclusive resource already held (a fixed port or a DB/index lock), "
        "often a single-instance MCP already running -- run ONE copy and route "
        "clients through the hub (a spawn-per-call hub needs connection reuse); "
        "or (2) an OS file/directory permission denial -- check file permissions.",
        (
            "zugriff verweigert",
            "access is denied",
            "access denied",
            "permission denied",
            "errno 13",
            "address already in use",
            "eaddrinuse",
            "already in use",
            "another instance",
            "already running",
            "could not lock",
            "failed to lock",
            "being used by another process",
        ),
    ),
    (
        "not_found",
        False,
        "Binary or runtime file not found -- check the connector command path "
        "(and the cwd if it needs a data file).",
        (
            "spawn failed",
            "is not recognized",
            "command not found",
            "no such file",
            "enoent",
            "cannot find the file",
            "winerror 2",
        ),
    ),
    (
        "missing_cwd_or_file",
        True,
        "Needs a working directory: set the connector `cwd` to the directory the "
        "host normally launches it from.",
        ("cannot find the path", "no such directory", "winerror 3"),
    ),
    (
        "startup_hang",
        False,
        "Hung on startup with no output: it may be waiting on env, auth, or a "
        "slow first-run index. Try a longer timeout or the host's launch context.",
        ("probe timed out",),
    ),
)


def classify_discovery_failure(error: str, stderr: str = "") -> dict[str, Any]:
    """Map a downstream discovery failure to a heuristic verdict.

    Returns ``{cause, supported, remedy, diagnosis_heuristic}``, a guess over the
    combined ``error`` + ``stderr`` text -- it augments, never replaces, the raw
    error/stderr in the event. Fail-closed: an unrecognized failure returns
    ``cause="unknown", supported=False``.
    """
    haystack = f"{error}\n{stderr}".lower()
    for cause, supported, remedy, needles in _SIGNATURES:
        if any(needle in haystack for needle in needles):
            return {
                "cause": cause,
                "supported": supported,
                "remedy": remedy,
                "diagnosis_heuristic": True,
            }
    return {
        "cause": "unknown",
        "supported": False,
        "remedy": "Discovery failed for an unrecognized reason -- inspect the "
        "stderr field and the connector's own logs.",
        "diagnosis_heuristic": True,
    }
