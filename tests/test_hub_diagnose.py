"""Tests for the downstream discovery-failure classifier (pure, no fastmcp)."""

from werktools.hub.diagnose import classify_discovery_failure


def test_single_instance_from_windows_access_denied():
    """The real codebase-memory-mcp signature: responds, then access-denied."""
    out = classify_discovery_failure(
        "RuntimeError: Failed to initialize server session",
        "level=info msg=mem.init\nZugriff verweigert",
    )
    assert out["cause"] == "single_instance"
    assert out["supported"] is False
    assert "single-instance" in out["remedy"].lower()


def test_single_instance_from_port_in_use():
    out = classify_discovery_failure("OSError", "bind: address already in use")
    assert out["cause"] == "single_instance"
    assert out["supported"] is False


def test_needs_auth_from_401():
    out = classify_discovery_failure("error", "HTTP 401 unauthorized: missing token")
    assert out["cause"] == "needs_auth"
    assert out["supported"] is False
    assert "key" in out["remedy"].lower()


def test_missing_cwd_is_supported_with_cwd_remedy():
    # path-not-found (vs file-not-found) signals a missing working directory.
    out = classify_discovery_failure("error", "The system cannot find the path specified")
    assert out["cause"] == "missing_cwd_or_file"
    assert out["supported"] is True  # the hub CAN run it once cwd is set
    assert "cwd" in out["remedy"].lower()


def test_explicit_auth_wins_over_broad_access_denied():
    # an explicit 401 must classify as needs_auth, not get swallowed by the
    # broad single_instance 'access denied' bucket (ordering precedence).
    out = classify_discovery_failure("error", "HTTP 401 Unauthorized; access denied")
    assert out["cause"] == "needs_auth"


def test_file_not_found_is_not_found_not_missing_cwd():
    # Windows missing-binary ('cannot find the file') must NOT read as a cwd
    # problem with supported=True.
    out = classify_discovery_failure("FileNotFoundError: cannot find the file specified", "")
    assert out["cause"] == "not_found"
    assert out["supported"] is False


def test_verdict_is_flagged_heuristic_and_ascii_only():
    """Every verdict is marked heuristic (a guess) and stays ASCII (printed/logged
    output must not corrupt on a cp1252 console)."""
    samples = [
        ("x", "zugriff verweigert"),
        ("x", "unauthorized"),
        ("x", "spawn failed"),
        ("x", "cannot find the path"),
        ("x", "probe timed out"),
        ("x", "totally unrecognized"),
    ]
    for error, stderr in samples:
        out = classify_discovery_failure(error, stderr)
        assert out["diagnosis_heuristic"] is True
        out["remedy"].encode("ascii")  # raises if any non-ASCII slipped in


def test_not_found_from_spawn_failure():
    out = classify_discovery_failure(
        "RuntimeError",
        "spawn failed: FileNotFoundError: [WinError 2] The system cannot find the file specified",
    )
    assert out["cause"] == "not_found"
    assert out["supported"] is False


def test_startup_hang_from_timeout_marker():
    out = classify_discovery_failure(
        "RuntimeError: Failed to initialize",
        "(probe timed out after 3s with no stderr output)",
    )
    assert out["cause"] == "startup_hang"
    assert out["supported"] is False


def test_unknown_fails_closed():
    """An unrecognized failure is reported unsupported, never silently fine."""
    out = classify_discovery_failure("RuntimeError: something weird", "blah blah")
    assert out["cause"] == "unknown"
    assert out["supported"] is False
    assert out["remedy"]


def test_case_insensitive_and_combines_error_and_stderr():
    # signal only in the error string, mixed case
    out = classify_discovery_failure("OSError: ADDRESS ALREADY IN USE", "")
    assert out["cause"] == "single_instance"
