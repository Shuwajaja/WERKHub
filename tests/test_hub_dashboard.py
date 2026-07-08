import json
import socket
import struct
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from werktools.hub.dashboard import (
    ProcessRow,
    build_snapshot,
    config_write_allowed,
    kill_allowed,
    make_handler,
    process_kill,
    registry_browse_allowed,
    snapshot_to_json,
    stub_processes,
)
from werktools.hub.ledger import recent_events
from werktools.hub.registry import load_config


def _config():
    return load_config(
        {
            "name": "werk-hub",
            "default_profile": "p",
            "profiles": [{"id": "p", "permission_profile": "balanced", "visible_tags": ["read"]}],
            "tools": [],
            "servers": [
                {"id": "docs", "command": "python", "args": []},
                {"id": "off", "command": "python", "args": [], "enabled": False},
            ],
        }
    )


def _config_with_secret_header():
    return load_config(
        {
            "name": "werk-hub",
            "default_profile": "p",
            "profiles": [{"id": "p", "permission_profile": "balanced", "visible_tags": ["read"]}],
            "tools": [],
            "servers": [
                {
                    "id": "docs",
                    "transport": "http",
                    "url": "https://x/mcp",
                    "headers": {"Authorization": "Bearer super-secret"},
                },
            ],
        }
    )


def test_stub_processes_one_per_enabled_server():
    rows = stub_processes(_config())
    assert [r.server_id for r in rows] == ["docs"]
    assert rows[0].killable is False


def test_build_snapshot(tmp_path):
    snap = build_snapshot(_config(), tmp_path / "l.jsonl")
    assert snap.hub_name == "werk-hub"
    assert snap.total_processes == 1
    assert snap.generated_at.endswith("Z")


def test_snapshot_to_json_keys(tmp_path):
    body = json.loads(snapshot_to_json(build_snapshot(_config(), tmp_path / "l.jsonl")))
    assert set(body) >= {"hub_name", "total_processes", "reclaimable_ram_bytes", "processes", "recent_events"}


def test_build_snapshot_flags_tampered_ledger(tmp_path):
    # MF12: /api/status (build_snapshot -> snapshot_to_json) must not serve a
    # forged ledger as clean evidence.
    from werktools.hub.ledger import record_event

    path = tmp_path / "l.jsonl"
    record_event(path, "policy.explained", {"a": 1})
    record_event(path, "tool.search", {"b": 2})

    clean = build_snapshot(_config(), path).to_dict()
    assert clean["chain_verified"] is True
    assert clean["chain_errors"] == 0

    lines = path.read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[0])
    rec["payload"]["forged"] = True
    lines[0] = json.dumps(rec, ensure_ascii=False)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    forged_snap = build_snapshot(_config(), path)
    forged = forged_snap.to_dict()
    assert forged["chain_verified"] is False
    assert forged["chain_errors"] >= 1
    assert json.loads(snapshot_to_json(forged_snap))["chain_verified"] is False


def test_kill_allowed(monkeypatch):
    monkeypatch.delenv("WERK_ALLOW_HUB_KILL", raising=False)
    assert kill_allowed() is False
    monkeypatch.setenv("WERK_ALLOW_HUB_KILL", "1")
    assert kill_allowed() is True
    monkeypatch.setenv("WERK_ALLOW_HUB_KILL", "yes")
    assert kill_allowed() is False


def test_process_kill_denied_no_ledger(tmp_path, monkeypatch):
    monkeypatch.delenv("WERK_ALLOW_HUB_KILL", raising=False)
    ledger = tmp_path / "l.jsonl"
    with pytest.raises(PermissionError):
        process_kill(123, "docs", _config(), ledger)
    assert not ledger.exists()


def test_process_kill_completed(tmp_path, monkeypatch):
    monkeypatch.setenv("WERK_ALLOW_HUB_KILL", "1")
    ledger = tmp_path / "l.jsonl"
    monkeypatch.setattr("werktools.hub.dashboard._win_terminate", lambda pid: None)
    monkeypatch.setattr("werktools.hub.dashboard.os.kill", lambda pid, sig: None)
    result = process_kill(4321, "docs", _config(), ledger, fleet_pids={4321})
    assert result["ok"] is True
    types = [e["payload"]["type"] for e in recent_events(ledger, limit=10)]
    assert "process.kill.requested" in types
    assert "process.kill.completed" in types


def test_process_kill_failed(tmp_path, monkeypatch):
    monkeypatch.setenv("WERK_ALLOW_HUB_KILL", "1")
    ledger = tmp_path / "l.jsonl"

    def boom(pid):
        raise OSError("gone")

    monkeypatch.setattr("werktools.hub.dashboard._win_terminate", boom)
    monkeypatch.setattr("werktools.hub.dashboard.os.kill", lambda pid, sig: (_ for _ in ()).throw(OSError("gone")))
    result = process_kill(4321, "docs", _config(), ledger, fleet_pids={4321})
    assert result["ok"] is False
    types = [e["payload"]["type"] for e in recent_events(ledger, limit=10)]
    assert "process.kill.failed" in types


def _resilient_urlopen(req_or_url, *, timeout=5, _retries=1):
    """urllib.request.urlopen wrapper that retries once on WinError 10053.

    On Windows, ThreadingHTTPServer can close the underlying socket while the
    OS is still draining the TCP send-buffer, causing urllib to raise
    ConnectionAbortedError (WinError 10053) even though the response was fully
    delivered.  A single immediate retry is safe for the read-only / assert
    patterns used here.
    """
    for attempt in range(_retries + 1):
        try:
            return urllib.request.urlopen(req_or_url, timeout=timeout)
        except ConnectionAbortedError:
            if attempt >= _retries:
                raise
            time.sleep(0.05)


def _fake_registry():
    """Offline registry getter: one installable (npm) + one non-installable."""

    def getter(url):
        return {
            "servers": [
                {
                    "id": "docs-server",
                    "name": "docs",
                    "description": "a docs server",
                    "version": "1.0",
                    "packages": [{"name": "docs-mcp", "registry_type": "npm", "version": "1.0"}],
                },
                {
                    "id": "weird-server",
                    "name": "weird",
                    "description": "no installable package",
                    "version": "1.0",
                    "packages": [],
                },
            ]
        }

    return getter


class _Server:
    def __init__(self, tmp_path, cfg=None, config_path=None, registry_http_get=None):
        handler = make_handler(
            cfg or _config(),
            tmp_path / "l.jsonl",
            config_path=config_path,
            registry_http_get=registry_http_get,
        )
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def close(self):
        # shutdown() signals serve_forever() to stop and waits for the selector
        # loop to exit, but on Windows the OS may not have fully released the
        # port or flushed in-flight responses yet.  Calling server_close()
        # explicitly closes the listening socket, and join() waits for the
        # server thread to terminate — together they eliminate the WinError
        # 10053 race where test teardown races the socket close.
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)


def test_http_status_html_and_404(tmp_path):
    srv = _Server(tmp_path)
    try:
        with _resilient_urlopen(srv.url("/api/status")) as r:
            assert r.status == 200
            body = json.loads(r.read())
        assert body["processes"][0]["server_id"] == "docs"

        with _resilient_urlopen(srv.url("/")) as r:
            html = r.read().decode("utf-8")
        assert "WERK Hub" in html
        assert 'id="fleet"' in html and 'id="strip"' in html and 'id="evidence"' in html
        assert "kill-btn" in html
        # A-FINAL token present; retired gold #F5B301 must be absent
        assert "--wa-bg" in html
        assert "#0c0d10" in html
        assert "#F5B301" not in html

        try:
            _resilient_urlopen(srv.url("/nope"))
            raise AssertionError("expected 404")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
    finally:
        srv.close()


def test_http_kill_403_when_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("WERK_ALLOW_HUB_KILL", raising=False)
    srv = _Server(tmp_path)
    try:
        req = urllib.request.Request(srv.url("/api/kill"), data=json.dumps({"pid": 1, "server_id": "docs"}).encode(), method="POST")
        try:
            _resilient_urlopen(req)
            raise AssertionError("expected 403")
        except urllib.error.HTTPError as exc:
            assert exc.code == 403
        assert not (tmp_path / "l.jsonl").exists()
    finally:
        srv.close()


def test_http_events_sse_headers(tmp_path):
    srv = _Server(tmp_path)
    try:
        with _resilient_urlopen(srv.url("/api/events")) as r:
            assert r.headers["Content-Type"] == "text/event-stream"
            assert b"event: ledger" in r.read()
    finally:
        srv.close()


def test_cli_dashboard_help(capsys):
    from werktools.cli import main

    with pytest.raises(SystemExit) as exc:
        main(["hub", "dashboard", "--help"])
    assert exc.value.code == 0


def test_killable_only_with_pid_and_flag():
    row = ProcessRow("docs", "p", "running", 100, 1.0, 500, killable=True)
    assert row.to_dict()["killable"] is True


def test_fleet_kill_button_disabled_when_pid_is_none():
    """A fleet row with killable=True but pid=None must render with the button disabled.

    The JS condition is (p.killable&&p.pid!=null?"":"disabled"). This test verifies
    that 'disabled' appears when pid is None, preventing a kill(NaN, ...) call.
    """
    from werktools.hub.dashboard import DASHBOARD_HTML

    # Locate the kill button render expression in the HTML template.
    assert "p.killable&&p.pid!=null" in DASHBOARD_HTML, (
        "Kill button guard must be (p.killable&&p.pid!=null), not (p.killable) alone"
    )


# --- Track D: dashboard hardening (MF6 CORS, MF7 kill gates) ----------------

def _scrape_token(srv):
    import re

    with _resilient_urlopen(srv.url("/")) as r:
        html = r.read().decode("utf-8")
    match = re.search(r'WERK_TOKEN="([^"]+)"', html)
    assert match, "session token not injected into the page"
    return match.group(1)


def _raw_post(port, path, body, headers):
    raw = f"POST {path} HTTP/1.1\r\n"
    for key, value in headers.items():
        raw += f"{key}: {value}\r\n"
    raw += f"Content-Length: {len(body)}\r\n\r\n{body}"
    sock = socket.create_connection(("127.0.0.1", port), timeout=5)
    # SO_LINGER=0 causes an immediate RST on close rather than a lingering
    # half-open FIN.  Without this the server's ThreadingHTTPServer handler
    # thread may be mid-write when the client socket disappears, triggering
    # WinError 10053 (WSAECONNABORTED) that can surface as a spurious failure
    # in subsequent requests on the same test server.
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    try:
        sock.sendall(raw.encode("latin-1"))
        resp = sock.recv(8192).decode("latin-1")
    finally:
        sock.close()
    return int(resp.split(" ", 2)[1])


def test_no_cors_header_on_status_and_events(tmp_path):
    # MF6: the wildcard Access-Control-Allow-Origin must be gone from both the
    # status JSON and the SSE event stream.
    srv = _Server(tmp_path)
    try:
        with _resilient_urlopen(srv.url("/api/status")) as r:
            assert r.headers.get("Access-Control-Allow-Origin") is None
        with _resilient_urlopen(srv.url("/api/events")) as r:
            assert r.headers.get("Access-Control-Allow-Origin") is None
    finally:
        srv.close()


def test_process_kill_rejects_non_fleet_pid_even_with_flag(tmp_path, monkeypatch):
    # MF7: a pid not in the live fleet is rejected before any OS call, even
    # with the flag set.
    monkeypatch.setenv("WERK_ALLOW_HUB_KILL", "1")
    ledger = tmp_path / "l.jsonl"
    killed = []
    monkeypatch.setattr("werktools.hub.dashboard._win_terminate", lambda pid: killed.append(pid))
    monkeypatch.setattr("werktools.hub.dashboard.os.kill", lambda pid, sig: killed.append(pid))

    result = process_kill(9999, "docs", _config(), ledger, fleet_pids={111})

    assert result["ok"] is False
    assert result["error"] == "pid not in live fleet"
    assert killed == []  # no OS kill attempted
    types = [e["payload"]["type"] for e in recent_events(ledger, limit=10)]
    assert "process.kill.failed" in types
    assert "process.kill.requested" not in types
    assert "process.kill.completed" not in types


def test_process_kill_allows_fleet_pid(tmp_path, monkeypatch):
    # MF7: the constraint does not break the legitimate in-fleet path.
    monkeypatch.setenv("WERK_ALLOW_HUB_KILL", "1")
    ledger = tmp_path / "l.jsonl"
    monkeypatch.setattr("werktools.hub.dashboard._win_terminate", lambda pid: None)
    monkeypatch.setattr("werktools.hub.dashboard.os.kill", lambda pid, sig: None)

    result = process_kill(4321, "docs", _config(), ledger, fleet_pids={4321})

    assert result["ok"] is True
    types = [e["payload"]["type"] for e in recent_events(ledger, limit=10)]
    assert "process.kill.requested" in types
    assert "process.kill.completed" in types


def test_http_kill_403_without_session_token(tmp_path, monkeypatch):
    # MF7: a kill POST without the per-session token is rejected (no ledger).
    monkeypatch.setenv("WERK_ALLOW_HUB_KILL", "1")
    srv = _Server(tmp_path)
    try:
        req = urllib.request.Request(
            srv.url("/api/kill"),
            data=json.dumps({"pid": 1, "server_id": "docs"}).encode(),
            method="POST",
        )
        try:
            _resilient_urlopen(req)
            raise AssertionError("expected 403")
        except urllib.error.HTTPError as exc:
            assert exc.code == 403
        assert not (tmp_path / "l.jsonl").exists()
    finally:
        srv.close()


def test_http_kill_403_foreign_host_and_origin(tmp_path, monkeypatch):
    # MF7: a valid token cannot save a cross-origin / foreign-Host kill.
    monkeypatch.setenv("WERK_ALLOW_HUB_KILL", "1")
    srv = _Server(tmp_path)
    try:
        token = _scrape_token(srv)
        body = json.dumps({"pid": 1, "server_id": "docs"})

        foreign_host = _raw_post(
            srv.port, "/api/kill", body,
            {"Host": "evil.example.com", "X-Werk-Token": token, "Content-Type": "application/json"},
        )
        assert foreign_host == 403

        foreign_origin = _raw_post(
            srv.port, "/api/kill", body,
            {
                "Host": f"127.0.0.1:{srv.port}",
                "Origin": "http://evil.example.com",
                "X-Werk-Token": token,
                "Content-Type": "application/json",
            },
        )
        assert foreign_origin == 403
        assert not (tmp_path / "l.jsonl").exists()
    finally:
        srv.close()


def test_session_token_not_in_ledger(tmp_path, monkeypatch):
    # MF7 guardrail: the per-session token must never reach the ledger. A
    # token-authenticated kill is rejected by the empty stub fleet (writing
    # process.kill.failed), and the token must not appear in that record.
    monkeypatch.setenv("WERK_ALLOW_HUB_KILL", "1")
    srv = _Server(tmp_path)
    try:
        token = _scrape_token(srv)
        req = urllib.request.Request(
            srv.url("/api/kill"),
            data=json.dumps({"pid": 4321, "server_id": "docs"}).encode(),
            method="POST",
            headers={"X-Werk-Token": token, "Content-Type": "application/json"},
        )
        with _resilient_urlopen(req) as r:
            assert r.status == 200  # reached process_kill (rejected by empty fleet)
        ledger_text = (tmp_path / "l.jsonl").read_text(encoding="utf-8")
        assert "process.kill.failed" in ledger_text
        assert token not in ledger_text
    finally:
        srv.close()


def test_run_dashboard_warns_on_non_loopback_host(tmp_path, monkeypatch, capsys):
    from werktools.hub import dashboard as dash

    class _FakeHTTPD:
        def __init__(self, addr, handler):
            self.server_address = (addr[0], 0)

        def serve_forever(self):
            raise KeyboardInterrupt

        def shutdown(self):
            pass

    monkeypatch.setattr(dash, "ThreadingHTTPServer", _FakeHTTPD)

    dash.run_dashboard(_config(), tmp_path / "l.jsonl", host="0.0.0.0", port=0)
    assert "WARNING" in capsys.readouterr().err

    dash.run_dashboard(_config(), tmp_path / "l.jsonl", host="127.0.0.1", port=0)
    assert "WARNING" not in capsys.readouterr().err


# --- Connector-manager slice 1 (read) -------------------------------------

def test_api_connectors_returns_servers_with_enabled_state(tmp_path):
    srv = _Server(tmp_path)
    try:
        with urllib.request.urlopen(srv.url("/api/connectors"), timeout=5) as r:
            body = json.loads(r.read())
        by_id = {c["id"]: c for c in body}
        assert by_id["docs"]["enabled"] is True
        assert by_id["off"]["enabled"] is False
    finally:
        srv.close()


def test_api_connectors_redacts_secret_headers(tmp_path):
    # The connector list must never leak a downstream auth header (redact=True).
    srv = _Server(tmp_path, cfg=_config_with_secret_header())
    try:
        with urllib.request.urlopen(srv.url("/api/connectors"), timeout=5) as r:
            body = json.loads(r.read())
        assert "super-secret" not in json.dumps(body)
        assert body[0]["headers"]["Authorization"] == "[redacted]"
    finally:
        srv.close()


# --- Connector-manager slice 2 (gated write) ------------------------------

def test_config_write_allowed(monkeypatch):
    monkeypatch.delenv("WERK_ALLOW_HUB_CONFIG_WRITE", raising=False)
    assert config_write_allowed() is False
    monkeypatch.setenv("WERK_ALLOW_HUB_CONFIG_WRITE", "1")
    assert config_write_allowed() is True
    monkeypatch.setenv("WERK_ALLOW_HUB_CONFIG_WRITE", "yes")
    assert config_write_allowed() is False


def test_connector_toggle_persists_and_ledgers(tmp_path, monkeypatch):
    monkeypatch.setenv("WERK_ALLOW_HUB_CONFIG_WRITE", "1")
    hub_json = tmp_path / "hub.json"
    srv = _Server(tmp_path, config_path=hub_json)
    try:
        token = _scrape_token(srv)
        req = urllib.request.Request(
            srv.url("/api/connectors/toggle"),
            data=json.dumps({"id": "docs"}).encode(),
            method="POST",
            headers={"X-Werk-Token": token, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            body = json.loads(r.read())
        assert body["ok"] is True
        assert body["enabled"] is False  # docs was on -> now off
        persisted = json.loads(hub_json.read_text(encoding="utf-8"))
        docs = next(s for s in persisted["servers"] if s["id"] == "docs")
        assert docs["enabled"] is False
        ledger_text = (tmp_path / "l.jsonl").read_text(encoding="utf-8")
        assert "config.connector.toggled" in ledger_text
        assert token not in ledger_text
        assert token not in hub_json.read_text(encoding="utf-8")
    finally:
        srv.close()


def test_connector_remove_persists(tmp_path, monkeypatch):
    monkeypatch.setenv("WERK_ALLOW_HUB_CONFIG_WRITE", "1")
    hub_json = tmp_path / "hub.json"
    srv = _Server(tmp_path, config_path=hub_json)
    try:
        token = _scrape_token(srv)
        req = urllib.request.Request(
            srv.url("/api/connectors/remove"),
            data=json.dumps({"id": "off"}).encode(),
            method="POST",
            headers={"X-Werk-Token": token, "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            assert json.loads(r.read())["ok"] is True
        persisted = json.loads(hub_json.read_text(encoding="utf-8"))
        assert all(s["id"] != "off" for s in persisted["servers"])
    finally:
        srv.close()


def test_connector_toggle_403_without_token(tmp_path, monkeypatch):
    monkeypatch.setenv("WERK_ALLOW_HUB_CONFIG_WRITE", "1")
    hub_json = tmp_path / "hub.json"
    srv = _Server(tmp_path, config_path=hub_json)
    try:
        req = urllib.request.Request(
            srv.url("/api/connectors/toggle"), data=json.dumps({"id": "docs"}).encode(), method="POST"
        )
        try:
            urllib.request.urlopen(req, timeout=5)
            raise AssertionError("expected 403")
        except urllib.error.HTTPError as exc:
            assert exc.code == 403
        assert not hub_json.exists()  # no write
    finally:
        srv.close()


def test_connector_toggle_403_when_env_unset(tmp_path, monkeypatch):
    monkeypatch.delenv("WERK_ALLOW_HUB_CONFIG_WRITE", raising=False)
    hub_json = tmp_path / "hub.json"
    srv = _Server(tmp_path, config_path=hub_json)
    try:
        token = _scrape_token(srv)
        req = urllib.request.Request(
            srv.url("/api/connectors/toggle"),
            data=json.dumps({"id": "docs"}).encode(),
            method="POST",
            headers={"X-Werk-Token": token, "Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(req, timeout=5)
            raise AssertionError("expected 403")
        except urllib.error.HTTPError as exc:
            assert exc.code == 403
        assert not hub_json.exists()  # no write
        assert not (tmp_path / "l.jsonl").exists()  # no ledger
    finally:
        srv.close()


def test_connector_toggle_403_foreign_host(tmp_path, monkeypatch):
    monkeypatch.setenv("WERK_ALLOW_HUB_CONFIG_WRITE", "1")
    hub_json = tmp_path / "hub.json"
    srv = _Server(tmp_path, config_path=hub_json)
    try:
        token = _scrape_token(srv)
        code = _raw_post(
            srv.port, "/api/connectors/toggle", json.dumps({"id": "docs"}),
            {"Host": "evil.example.com", "X-Werk-Token": token, "Content-Type": "application/json"},
        )
        assert code == 403
        assert not hub_json.exists()
    finally:
        srv.close()


# --- Connector-manager slice 3 (gated registry browse + add) ---------------

def _post_json(srv, path, body, token=None):
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["X-Werk-Token"] = token
    req = urllib.request.Request(srv.url(path), data=json.dumps(body).encode(), method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def test_registry_browse_allowed(monkeypatch):
    monkeypatch.delenv("WERK_ALLOW_HUB_REGISTRY", raising=False)
    assert registry_browse_allowed() is False
    monkeypatch.setenv("WERK_ALLOW_HUB_REGISTRY", "1")
    assert registry_browse_allowed() is True
    monkeypatch.setenv("WERK_ALLOW_HUB_REGISTRY", "yes")
    assert registry_browse_allowed() is False


def test_registry_search_403_without_token(tmp_path, monkeypatch):
    monkeypatch.setenv("WERK_ALLOW_HUB_REGISTRY", "1")
    srv = _Server(tmp_path, registry_http_get=_fake_registry())
    try:
        code, _ = _post_json(srv, "/api/registry/search", {"query": ""})
        assert code == 403
    finally:
        srv.close()


def test_registry_search_403_without_env(tmp_path, monkeypatch):
    monkeypatch.delenv("WERK_ALLOW_HUB_REGISTRY", raising=False)
    srv = _Server(tmp_path, registry_http_get=_fake_registry())
    try:
        token = _scrape_token(srv)
        code, _ = _post_json(srv, "/api/registry/search", {"query": ""}, token)
        assert code == 403  # token ok, but the network opt-in is off
    finally:
        srv.close()


def test_registry_search_returns_candidates_offline(tmp_path, monkeypatch):
    monkeypatch.setenv("WERK_ALLOW_HUB_REGISTRY", "1")
    srv = _Server(tmp_path, registry_http_get=_fake_registry())
    try:
        token = _scrape_token(srv)
        code, body = _post_json(srv, "/api/registry/search", {"query": ""}, token)
        assert code == 200
        by_id = {c["id"]: c for c in body["candidates"]}
        assert by_id["docs-server"]["installable"] is True
        assert by_id["weird-server"]["installable"] is False
    finally:
        srv.close()


def test_connector_add_persists_and_ledgers(tmp_path, monkeypatch):
    monkeypatch.setenv("WERK_ALLOW_HUB_REGISTRY", "1")
    monkeypatch.setenv("WERK_ALLOW_HUB_CONFIG_WRITE", "1")
    hub_json = tmp_path / "hub.json"
    srv = _Server(tmp_path, config_path=hub_json, registry_http_get=_fake_registry())
    try:
        token = _scrape_token(srv)
        _post_json(srv, "/api/registry/search", {"query": ""}, token)  # populate the stash
        code, body = _post_json(srv, "/api/connectors/add", {"id": "docs-server"}, token)
        assert code == 200
        assert body["ok"] is True
        assert body["command"] == "npx"
        persisted = json.loads(hub_json.read_text(encoding="utf-8"))
        assert any(s["id"] == "docs-server" for s in persisted["servers"])
        ledger_text = (tmp_path / "l.jsonl").read_text(encoding="utf-8")
        assert "config.connector.added" in ledger_text
        assert token not in ledger_text
        assert token not in hub_json.read_text(encoding="utf-8")
    finally:
        srv.close()


def test_connector_add_400_non_installable(tmp_path, monkeypatch):
    monkeypatch.setenv("WERK_ALLOW_HUB_REGISTRY", "1")
    monkeypatch.setenv("WERK_ALLOW_HUB_CONFIG_WRITE", "1")
    hub_json = tmp_path / "hub.json"
    srv = _Server(tmp_path, config_path=hub_json, registry_http_get=_fake_registry())
    try:
        token = _scrape_token(srv)
        _post_json(srv, "/api/registry/search", {"query": ""}, token)
        code, _ = _post_json(srv, "/api/connectors/add", {"id": "weird-server"}, token)
        assert code == 400
        assert not hub_json.exists()
    finally:
        srv.close()


def test_connector_add_404_unknown_candidate(tmp_path, monkeypatch):
    monkeypatch.setenv("WERK_ALLOW_HUB_REGISTRY", "1")
    monkeypatch.setenv("WERK_ALLOW_HUB_CONFIG_WRITE", "1")
    hub_json = tmp_path / "hub.json"
    srv = _Server(tmp_path, config_path=hub_json, registry_http_get=_fake_registry())
    try:
        token = _scrape_token(srv)
        # no search first -> the stash is empty
        code, _ = _post_json(srv, "/api/connectors/add", {"id": "never-searched"}, token)
        assert code == 404
        assert not hub_json.exists()
    finally:
        srv.close()


def test_connector_add_409_duplicate(tmp_path, monkeypatch):
    monkeypatch.setenv("WERK_ALLOW_HUB_REGISTRY", "1")
    monkeypatch.setenv("WERK_ALLOW_HUB_CONFIG_WRITE", "1")
    hub_json = tmp_path / "hub.json"
    srv = _Server(tmp_path, config_path=hub_json, registry_http_get=_fake_registry())
    try:
        token = _scrape_token(srv)
        _post_json(srv, "/api/registry/search", {"query": ""}, token)
        assert _post_json(srv, "/api/connectors/add", {"id": "docs-server"}, token)[0] == 200
        code, _ = _post_json(srv, "/api/connectors/add", {"id": "docs-server"}, token)
        assert code == 409
    finally:
        srv.close()


def test_connector_add_403_without_config_write(tmp_path, monkeypatch):
    monkeypatch.setenv("WERK_ALLOW_HUB_REGISTRY", "1")
    monkeypatch.delenv("WERK_ALLOW_HUB_CONFIG_WRITE", raising=False)
    hub_json = tmp_path / "hub.json"
    srv = _Server(tmp_path, config_path=hub_json, registry_http_get=_fake_registry())
    try:
        token = _scrape_token(srv)
        _post_json(srv, "/api/registry/search", {"query": ""}, token)
        code, _ = _post_json(srv, "/api/connectors/add", {"id": "docs-server"}, token)
        assert code == 403
        assert not hub_json.exists()
    finally:
        srv.close()


# --- P2-0: token reconcile ---------------------------------------------------

def test_html_canonical_tokens_no_rogue_blue(tmp_path):
    # A-FINAL: the inline :root block must match A-FINAL --wa-* tokens.
    # Retired hues (#378ADD rogue blue, #F5B301 gold, #18181B Cool-Graphite bg,
    # #1F1F22, #F3F4F6) must be absent.
    srv = _Server(tmp_path)
    try:
        with _resilient_urlopen(srv.url("/")) as r:
            html = r.read().decode("utf-8")
        # Rogue hues must be gone
        assert "#378ADD" not in html
        assert "#F5B301" not in html  # retired gold
        assert "#18181B" not in html  # retired Cool-Graphite bg
        # A-FINAL canonical tokens present
        assert "--wa-bg" in html
        assert "#0c0d10" in html   # --wa-bg
        assert "#131519" in html   # --wa-surface
        assert "#e8ecf2" in html   # --wa-text
        assert "#4ea46e" in html   # --wa-ok
        assert "#e0584e" in html   # --wa-danger
        assert "#d7a13a" in html   # --wa-warn (amber, not gold)
        assert "#7b88db" in html   # --wa-accent (indigo)
        assert "#c4ccd6" in html   # --wa-brand (platinum)
    finally:
        srv.close()


def test_hidden_attribute_is_honored_css_reset():
    """Regression (pixel-only bug): the kill-strip + approval scrim must be HIDDEN
    on first load. `.scrim{display:flex}` / `.kill-strip{display:flex}` outrank the
    bare [hidden] attribute (equal specificity, author wins by source order), so a
    global [hidden]{display:none!important} reset is REQUIRED -- without it both
    overlays render open AND undismissable on load, covering the board. This defect
    is invisible to node-check / DOM-free pytest, so lock the CSS rule at the source.
    """
    from werktools.hub.dashboard import DASHBOARD_HTML

    css = DASHBOARD_HTML.replace(" ", "")
    assert "[hidden]{display:none!important}" in css, (
        "missing [hidden]{display:none!important} reset; kill-strip + queue-scrim "
        "render open on load because .scrim/.kill-strip display:flex overrides [hidden]"
    )


def test_evtone_does_not_leak_indigo_onto_status():
    """A-FINAL canon: --wa-indigo is the INTERACTION/selection colour ONLY. The
    evidence-ledger tone map (_evTone) must not paint lifecycle/dispatch event types
    with indigo -- a brand leak visible in the rendered ledger pixels."""
    from werktools.hub.dashboard import DASHBOARD_HTML

    start = DASHBOARD_HTML.index("function _evTone(")
    end = DASHBOARD_HTML.index("function _evRow(")
    evtone = DASHBOARD_HTML[start:end]
    assert "--wa-indigo" not in evtone, (
        "_evTone must not return var(--wa-indigo) for event types -- indigo is "
        "interaction-only per A-FINAL canon (use a neutral tone for dispatch events)"
    )


def test_sidebar_nav_present_and_collapsible():
    """The control-plane nav is a vertical sidebar tablist with all four sections and a
    persisted collapse toggle (icon-rail). Replaces the old horizontal-tab nav so the
    SOTA sidebar layout cannot silently regress to a topbar."""
    from werktools.hub.dashboard import DASHBOARD_HTML

    html = DASHBOARD_HTML
    assert 'aria-orientation="vertical"' in html, "sidebar nav must be a vertical tablist"
    for tab_id in ("tab-board", "tab-timeline", "tab-connectors", "tab-registry"):
        assert f'id="{tab_id}"' in html, f"missing sidebar nav item {tab_id}"
    assert 'id="side-toggle"' in html, "missing sidebar collapse toggle"
    css = DASHBOARD_HTML.replace(" ", "")
    assert ".app.col.side{width:62px}" in css, "collapsed icon-rail width rule missing"


def test_board_panels_are_collapsible():
    """Grafana-style show/hide: the heavy board panels (agent runtimes, evidence
    ledger) carry a fold toggle bound to a .foldbody wrapper, hidden via CSS."""
    from werktools.hub.dashboard import DASHBOARD_HTML

    html = DASHBOARD_HTML
    assert 'data-fold="runtimes"' in html, "missing fold toggle on agent-runtimes panel"
    assert 'data-fold="ev-tile"' in html, "missing fold toggle on evidence panel"
    assert 'class="foldbody"' in html, "missing foldable panel body wrapper"
    css = DASHBOARD_HTML.replace(" ", "")
    assert ".folded.foldbody{display:none}" in css, "fold-hide CSS rule missing"


def test_brand_fonts_self_hosted_and_no_em_dash():
    """taste-skill pass: brand typography is actually loaded (self-hosted woff2,
    zero-external) and the em-dash AI-tell is banned everywhere in the served page."""
    from werktools.hub.dashboard import DASHBOARD_HTML

    assert DASHBOARD_HTML.count("@font-face") == 3, "expected 3 self-hosted @font-face rules"
    assert "font-family:'Space Grotesk'" in DASHBOARD_HTML
    assert "font-family:'IBM Plex Mono'" in DASHBOARD_HTML
    assert DASHBOARD_HTML.count("font-display:swap") == 3, "font-display:swap required on each face"
    assert "data:font/woff2;base64," in DASHBOARD_HTML, "fonts must be inlined as data: (zero-external)"
    assert "__WERK_FONTS__" not in DASHBOARD_HTML, "font placeholder was not replaced"
    assert "—" not in DASHBOARD_HTML, "em-dash (—) found; taste-skill 9.G bans it"
    assert "–" not in DASHBOARD_HTML, "en-dash (–) found; taste-skill 9.G bans it"


def test_filter_pills_are_pane_scoped():
    """Regression: the connectors-tab and registry-tab filter pills both use class
    .fpill. Their click handlers must be scoped to their own pane, otherwise clicking
    a registry filter also fires the connector handler, sets _connFilter.pill to
    undefined (registry pills carry data-reg-filter, not data-filter) and clobbers the
    connector filter (and vice versa)."""
    from werktools.hub.dashboard import DASHBOARD_HTML

    assert '"#pane-connectors .fpill"' in DASHBOARD_HTML, "connector pill handler must be pane-scoped"
    assert '"#pane-registry .fpill"' in DASHBOARD_HTML, "registry pill handler must be pane-scoped"
    assert 'querySelectorAll(".fpill")' not in DASHBOARD_HTML, (
        "global .fpill query causes cross-pane filter collision; scope to the pane"
    )


def test_mobile_board_collapses_to_single_column():
    """A narrow viewport must collapse the bento board to one column so tiles do not
    shrink to unreadable thirds (the 390px breakage caught in the pixel review)."""
    from werktools.hub.dashboard import DASHBOARD_HTML

    css = DASHBOARD_HTML.replace(" ", "")
    assert "@media(max-width:640px){.board{display:block" in css, (
        "mobile breakpoint must collapse the bento board to a single block-flow column "
        "(grid-template-columns overrides do not take here; display:block is the robust fix)"
    )


# --- P2-1: /api/runtimes shape -----------------------------------------------

def test_api_runtimes_shape(tmp_path):
    # /api/runtimes must return {generated_at, total, detected, probes:[...]}
    # with each probe carrying the merged runtime_row fields.
    srv = _Server(tmp_path)
    try:
        with _resilient_urlopen(srv.url("/api/runtimes")) as r:
            assert r.status == 200
            body = json.loads(r.read())
        assert "generated_at" in body
        assert isinstance(body["total"], int)
        assert isinstance(body["detected"], list)
        assert isinstance(body["probes"], list)
        assert len(body["probes"]) == body["total"]
        # Each probe must carry the merged descriptor fields
        assert len(body["probes"]) > 0, "expected at least one probed host"
        probe = body["probes"][0]
        for key in ("host_id", "detected", "display_name", "monogram", "at_risk", "at_risk_reason"):
            assert key in probe, f"missing key {key!r} in probe"
        # at_risk_reason must always be present (stable JSON — "" by default)
        assert probe["at_risk_reason"] is not None
    finally:
        srv.close()


def test_api_runtimes_no_ledger_event(tmp_path):
    # GET /api/runtimes must NOT write a ledger event (no spam on auto-refresh).
    srv = _Server(tmp_path)
    ledger_path = tmp_path / "l.jsonl"
    try:
        with _resilient_urlopen(srv.url("/api/runtimes")):
            pass
        # The ledger file must not exist (no write triggered by the GET).
        assert not ledger_path.exists()
    finally:
        srv.close()


def test_api_runtimes_html_panel_present(tmp_path):
    # The runtimes panel div and its table must be present in the served HTML.
    srv = _Server(tmp_path)
    try:
        with _resilient_urlopen(srv.url("/")) as r:
            html = r.read().decode("utf-8")
        assert 'id="runtimes"' in html
        assert 'id="runtimelist"' in html
        assert "loadRuntimes" in html
    finally:
        srv.close()


# --- P2-1: trust-tier badges on connectors panel ----------------------------

def _config_with_trust_tiers():
    return load_config(
        {
            "name": "werk-hub",
            "default_profile": "p",
            "profiles": [{"id": "p", "permission_profile": "balanced", "visible_tags": ["read"]}],
            "tools": [],
            "servers": [
                {"id": "official-srv", "command": "python", "args": [], "trust_tier": "Official"},
                {"id": "scanned-srv", "command": "python", "args": [], "trust_tier": "Security-Scanned"},
                {"id": "community-srv", "command": "python", "args": [], "trust_tier": "Community-Unverified"},
            ],
        }
    )


def test_api_connectors_includes_trust_tier(tmp_path):
    # /api/connectors must expose trust_tier on each connector row.
    srv = _Server(tmp_path, cfg=_config_with_trust_tiers())
    try:
        with _resilient_urlopen(srv.url("/api/connectors")) as r:
            body = json.loads(r.read())
        by_id = {c["id"]: c for c in body}
        assert by_id["official-srv"]["trust_tier"] == "Official"
        assert by_id["scanned-srv"]["trust_tier"] == "Security-Scanned"
        assert by_id["community-srv"]["trust_tier"] == "Community-Unverified"
    finally:
        srv.close()


def test_html_trust_badge_css_classes_present(tmp_path):
    # The trust-tier badge CSS classes must be in the served HTML.
    srv = _Server(tmp_path)
    try:
        with _resilient_urlopen(srv.url("/")) as r:
            html = r.read().decode("utf-8")
        assert "trust-official" in html
        assert "trust-scanned" in html
        assert "trust-unverified" in html
    finally:
        srv.close()


# ---------------------------------------------------------------------------
# Regression tests: sweep-8 security hardening
# ---------------------------------------------------------------------------

def _raw_get(port, path, headers=None):
    """Open a raw socket, send a minimal GET, return the HTTP status code."""
    hdrs = headers or {}
    raw = f"GET {path} HTTP/1.1\r\n"
    for key, value in hdrs.items():
        raw += f"{key}: {value}\r\n"
    raw += "Connection: close\r\n\r\n"
    import struct
    sock = socket.create_connection(("127.0.0.1", port), timeout=5)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    try:
        sock.sendall(raw.encode("latin-1"))
        resp = sock.recv(8192).decode("latin-1")
    finally:
        sock.close()
    return int(resp.split(" ", 2)[1])


@pytest.mark.parametrize("host,expected", [
    ("[::1]:7879", 200),
    ("[::1]", 200),
    ("127.0.0.1:7879", 200),
    ("localhost", 200),
    ("192.168.1.1", 403),
])
def test_host_is_loopback_via_http(tmp_path, host, expected):
    # _host_is_loopback must accept IPv6 bracket form, IPv4 with port, bare
    # 'localhost', and reject a non-loopback IP address.
    srv = _Server(tmp_path)
    try:
        # Replace the real port in host strings that contain 7879 (test-fixed)
        h = host.replace("7879", str(srv.port))
        code = _raw_get(srv.port, "/api/status", {"Host": h})
        assert code == expected, f"Host: {h!r} -> expected {expected}, got {code}"
    finally:
        srv.close()


def test_get_root_returns_403_for_non_loopback_host(tmp_path):
    # GET / must return 403 when Host is non-loopback (sweep-8 guard).
    srv = _Server(tmp_path)
    try:
        code = _raw_get(srv.port, "/", {"Host": "evil.example.com"})
        assert code == 403
    finally:
        srv.close()


@pytest.mark.parametrize("path", [
    "/api/status",
    "/api/connectors",
    "/api/runtimes",
    "/api/events",
])
def test_get_api_endpoints_return_403_for_non_loopback_host(tmp_path, path):
    # All four GET API paths must return 403 for a non-loopback Host header.
    srv = _Server(tmp_path)
    try:
        code = _raw_get(srv.port, path, {"Host": "evil.example.com"})
        assert code == 403, f"{path} returned {code}, expected 403"
    finally:
        srv.close()


def test_send_sets_security_headers_on_standard_responses(tmp_path):
    # _send must attach X-Frame-Options, X-Content-Type-Options, and
    # Content-Security-Policy on every response.
    #
    # CSP DESIGN DECISION (committed; do NOT "harden" away): script-src uses a
    # per-request nonce, but style-src MUST keep 'unsafe-inline'. The dashboard
    # template uses ~69 inline style="..." ATTRIBUTES for its Bento layout, and a
    # CSP nonce/hash covers <style> BLOCKS only — never inline style attributes.
    # Replacing 'unsafe-inline' with a nonce or sha256 hash silently breaks all
    # inline styling under an enforcing browser (uncatchable by pytest/node-check).
    # The injection surface is near-zero: loopback-only, token-gated, every
    # innerHTML path goes through esc(). This assertion locks the decision so a
    # future automated sweep cannot quietly reintroduce the regression.
    srv = _Server(tmp_path)
    try:
        for path in ("/", "/api/status"):
            with _resilient_urlopen(srv.url(path)) as r:
                assert r.headers["X-Frame-Options"] == "DENY", f"{path}: X-Frame-Options missing"
                assert r.headers["X-Content-Type-Options"] == "nosniff", f"{path}: X-Content-Type-Options missing"
                assert "Content-Security-Policy" in r.headers, f"{path}: CSP missing"
                csp = r.headers["Content-Security-Policy"]
                assert "default-src 'self'" in csp
                assert "script-src 'nonce-" in csp, f"{path}: script-src nonce missing"
                assert "style-src 'unsafe-inline'" in csp, (
                    f"{path}: style-src MUST keep 'unsafe-inline' (inline style attrs); see comment above"
                )
    finally:
        srv.close()


def test_send_sets_security_headers_on_sse_response(tmp_path):
    # The SSE path (/api/events) must carry X-Frame-Options,
    # X-Content-Type-Options, AND Content-Security-Policy (the dashboard
    # explicitly sends all three headers on the SSE response as well).
    srv = _Server(tmp_path)
    try:
        with _resilient_urlopen(srv.url("/api/events")) as r:
            assert r.headers["X-Frame-Options"] == "DENY"
            assert r.headers["X-Content-Type-Options"] == "nosniff"
            assert "Content-Security-Policy" in r.headers, "CSP must be present on SSE response"
    finally:
        srv.close()


def test_snapshot_to_dict_includes_kill_allowed_key(tmp_path, monkeypatch):
    # DashboardSnapshot.to_dict() must include 'kill_allowed', whose value
    # reflects the current env flag state.

    # Flag off
    monkeypatch.delenv("WERK_ALLOW_HUB_KILL", raising=False)
    snap = build_snapshot(_config(), tmp_path / "l.jsonl")
    d = snap.to_dict()
    assert "kill_allowed" in d
    assert d["kill_allowed"] is False

    # Flag on
    monkeypatch.setenv("WERK_ALLOW_HUB_KILL", "1")
    snap2 = build_snapshot(_config(), tmp_path / "l2.jsonl")
    d2 = snap2.to_dict()
    assert "kill_allowed" in d2
    assert d2["kill_allowed"] is True

    # snapshot_to_json round-trips the same value
    import json as _json
    assert _json.loads(snapshot_to_json(snap2))["kill_allowed"] is True


@pytest.mark.parametrize("path", [
    "/api/kill",
    "/api/connectors/toggle",
    "/api/connectors/remove",
    "/api/registry/search",
    "/api/connectors/add",
])
def test_post_endpoints_return_413_for_oversized_body(tmp_path, monkeypatch, path):
    # Each POST handler must return 413 before reading when Content-Length > 65536,
    # and must not write a ledger entry.
    monkeypatch.setenv("WERK_ALLOW_HUB_KILL", "1")
    monkeypatch.setenv("WERK_ALLOW_HUB_CONFIG_WRITE", "1")
    monkeypatch.setenv("WERK_ALLOW_HUB_REGISTRY", "1")
    srv = _Server(tmp_path)
    ledger_path = tmp_path / "l.jsonl"
    try:
        code = _raw_post(
            srv.port, path, "x" * 65537,
            {
                "Host": f"127.0.0.1:{srv.port}",
                "Content-Type": "application/json",
            },
        )
        assert code == 413, f"{path} returned {code}, expected 413"
        # No ledger write before the body cap
        assert not ledger_path.exists()
    finally:
        srv.close()


def test_html_at_risk_badge_css_class_present(tmp_path):
    # The at-risk badge CSS class must be in the served HTML for runtime rows.
    srv = _Server(tmp_path)
    try:
        with _resilient_urlopen(srv.url("/")) as r:
            html = r.read().decode("utf-8")
        assert "at-risk-badge" in html
        assert "mono-chip" in html
    finally:
        srv.close()


def test_placeholder_replacement_and_nonce_match(tmp_path):
    """GET / must not expose literal placeholder strings, and the CSP nonce
    must match the nonce= attribute on the <script> tag (Fix 6)."""
    import re as _re

    srv = _Server(tmp_path)
    try:
        with _resilient_urlopen(srv.url("/")) as r:
            html = r.read().decode("utf-8")
            csp = r.headers.get("Content-Security-Policy", "")
        # (1) Placeholders must be absent from the rendered page.
        assert "__WERK_SESSION_TOKEN__" not in html, "session token placeholder was not replaced"
        assert "__WERK_NONCE__" not in html, "nonce placeholder was not replaced"
        # (2) The nonce in the CSP header must match the script nonce attribute.
        csp_match = _re.search(r"nonce-([A-Za-z0-9_-]+)", csp)
        assert csp_match, f"no nonce found in CSP header: {csp!r}"
        csp_nonce = csp_match.group(1)
        script_match = _re.search(r'<script\s+nonce="([^"]+)"', html)
        assert script_match, "no nonce= attribute found on <script> tag"
        script_nonce = script_match.group(1)
        assert csp_nonce == script_nonce, (
            f"CSP nonce {csp_nonce!r} does not match script nonce {script_nonce!r}"
        )
    finally:
        srv.close()


# ---------------------------------------------------------------------------
# P2-2c: GET /api/registry — capability catalog endpoint + Registry HTML section
# ---------------------------------------------------------------------------

def _make_registry_db(tmp_path, rows=None):
    """Build a minimal registry.db beside hub.json and return the db path."""
    from werktools.hub.registry_db import build_registry

    db_path = tmp_path / "registry.db"
    default_rows = rows if rows is not None else [
        {
            "id": "test-stripe",
            "kind": "tool",
            "category": "payments",
            "trust_tier": "Official",
            "what_it_is": "Stripe payment tool",
            "maintainer": "stripe",
            "maintenance": "active",
            "popularity": "high",
            "security_note": "Requires STRIPE_SECRET_KEY",
            "deluxe_reason": "",
            "deluxe_base": True,
            "verified": True,
            "needs_keys": ["STRIPE_SECRET_KEY"],
            "risk": "external",
        },
        {
            "id": "test-no-key",
            "kind": "skill",
            "category": "search",
            "trust_tier": "Security-Scanned",
            "what_it_is": "A key-free search skill",
            "maintainer": "community",
            "maintenance": "unknown",
            "popularity": "unverified",
            "security_note": "",
            "deluxe_reason": "",
            "deluxe_base": False,
            "verified": False,
            "needs_keys": [],
            "risk": "read",
        },
    ]
    build_registry(db_path, default_rows)
    return db_path


def _server_with_registry_db(tmp_path, rows=None):
    """Build a registry.db at tmp_path/registry.db, start a Server using
    config_path=tmp_path/hub.json so the handler can find registry.db via
    Path(config_path).parent / 'registry.db'."""
    hub_json = tmp_path / "hub.json"
    _make_registry_db(tmp_path, rows=rows)
    return _Server(tmp_path, config_path=hub_json)


def test_api_registry_returns_catalog(tmp_path):
    """GET /api/registry with a populated DB returns the capability list."""
    srv = _server_with_registry_db(tmp_path)
    try:
        with _resilient_urlopen(srv.url("/api/registry")) as r:
            assert r.status == 200
            body = json.loads(r.read())
        assert "capabilities" in body
        caps = body["capabilities"]
        assert len(caps) == 2
        by_id = {c["id"]: c for c in caps}
        assert "test-stripe" in by_id
        assert "test-no-key" in by_id
    finally:
        srv.close()


def test_api_registry_403_non_loopback(tmp_path):
    """GET /api/registry must return 403 when the Host header is non-loopback."""
    srv = _server_with_registry_db(tmp_path)
    try:
        code = _raw_get(srv.port, "/api/registry", {"Host": "evil.example.com"})
        assert code == 403
    finally:
        srv.close()


def test_api_registry_capability_fields(tmp_path):
    """Each capability row must include the required fields (no key values)."""
    srv = _server_with_registry_db(tmp_path)
    try:
        with _resilient_urlopen(srv.url("/api/registry")) as r:
            body = json.loads(r.read())
        caps = body["capabilities"]
        required_fields = {
            "id", "kind", "category", "trust_tier", "deluxe_base",
            "maintenance", "popularity", "needs_keys", "keys_present",
        }
        for cap in caps:
            missing = required_fields - set(cap)
            assert not missing, f"capability {cap.get('id')!r} missing fields: {missing}"
    finally:
        srv.close()


def test_api_registry_needs_keys_are_names_only(tmp_path):
    """needs_keys must contain env-var NAMES only; no values must appear."""
    # Set a dummy env var so we can verify the NAME appears but the VALUE does not.
    dummy_key = "STRIPE_SECRET_KEY"
    dummy_value = "sk_test_SUPERSECRET_VALUE_12345"
    import os as _os
    old = _os.environ.get(dummy_key)
    _os.environ[dummy_key] = dummy_value
    try:
        srv = _server_with_registry_db(tmp_path)
        try:
            with _resilient_urlopen(srv.url("/api/registry")) as r:
                raw = r.read()
            body = json.loads(raw)
            raw_text = raw.decode("utf-8")
            # The key NAME may appear (it is metadata)
            assert dummy_key in raw_text, "key name must appear in the response"
            # The key VALUE must NEVER appear
            assert dummy_value not in raw_text, "key VALUE must never appear in response"
            # keys_present must be a boolean
            caps = body["capabilities"]
            by_id = {c["id"]: c for c in caps}
            stripe_cap = by_id["test-stripe"]
            assert isinstance(stripe_cap["keys_present"], bool)
            assert stripe_cap["keys_present"] is True  # we set the env var above
        finally:
            srv.close()
    finally:
        if old is None:
            _os.environ.pop(dummy_key, None)
        else:
            _os.environ[dummy_key] = old


def test_api_registry_key_absent_reported_as_false(tmp_path):
    """When a needed env var is absent, keys_present must be False."""
    import os as _os
    dummy_key = "STRIPE_SECRET_KEY"
    old = _os.environ.pop(dummy_key, None)
    try:
        srv = _server_with_registry_db(tmp_path)
        try:
            with _resilient_urlopen(srv.url("/api/registry")) as r:
                body = json.loads(r.read())
            caps = body["capabilities"]
            by_id = {c["id"]: c for c in caps}
            stripe_cap = by_id["test-stripe"]
            assert stripe_cap["keys_present"] is False
            # The capability with no keys needed -> keys_present True (vacuously present)
            no_key_cap = by_id["test-no-key"]
            assert no_key_cap["keys_present"] is True
        finally:
            srv.close()
    finally:
        if old is not None:
            _os.environ[dummy_key] = old


def test_api_registry_missing_db_returns_empty(tmp_path):
    """When registry.db does not exist, /api/registry must return empty list, not crash."""
    hub_json = tmp_path / "hub.json"
    # No _make_registry_db call — db is absent
    srv = _Server(tmp_path, config_path=hub_json)
    try:
        with _resilient_urlopen(srv.url("/api/registry")) as r:
            assert r.status == 200
            body = json.loads(r.read())
        assert body["capabilities"] == []
    finally:
        srv.close()


def test_api_registry_no_config_path_returns_empty(tmp_path):
    """When config_path is None (no hub.json), /api/registry must return empty list."""
    # No config_path -> handler cannot derive db path -> fail-closed to []
    srv = _Server(tmp_path)  # config_path=None (default)
    try:
        with _resilient_urlopen(srv.url("/api/registry")) as r:
            assert r.status == 200
            body = json.loads(r.read())
        assert body["capabilities"] == []
    finally:
        srv.close()


def test_api_registry_includes_category_counts(tmp_path):
    """The response should include category_counts mapping."""
    srv = _server_with_registry_db(tmp_path)
    try:
        with _resilient_urlopen(srv.url("/api/registry")) as r:
            body = json.loads(r.read())
        assert "category_counts" in body
        cc = body["category_counts"]
        assert isinstance(cc, dict)
        assert cc.get("payments") == 1
        assert cc.get("search") == 1
    finally:
        srv.close()


def test_html_registry_section_present(tmp_path):
    """The served HTML must contain the Registry tab and section markers."""
    srv = _Server(tmp_path)
    try:
        with _resilient_urlopen(srv.url("/")) as r:
            html = r.read().decode("utf-8")
        # Nav tab entry
        assert "pane-registry" in html, "Registry pane id must be in HTML"
        assert "tab-registry" in html, "Registry tab id must be in HTML"
        # Section content markers (JS fetch + render + filter controls)
        assert "loadRegistry" in html, "loadRegistry JS function must be in HTML"
        assert "/api/registry" in html, "/api/registry fetch path must be in HTML"
        # Filter controls
        assert "reg-cat-filter" in html or "regcatfilter" in html or "reg-filter" in html, (
            "Registry filter element must be in HTML"
        )
    finally:
        srv.close()


def test_api_registry_no_cors_header(tmp_path):
    """GET /api/registry must not set Access-Control-Allow-Origin."""
    srv = _server_with_registry_db(tmp_path)
    try:
        with _resilient_urlopen(srv.url("/api/registry")) as r:
            assert r.headers.get("Access-Control-Allow-Origin") is None
    finally:
        srv.close()


def test_api_registry_security_headers(tmp_path):
    """GET /api/registry must carry X-Frame-Options, X-Content-Type-Options, CSP."""
    srv = _server_with_registry_db(tmp_path)
    try:
        with _resilient_urlopen(srv.url("/api/registry")) as r:
            assert r.headers["X-Frame-Options"] == "DENY"
            assert r.headers["X-Content-Type-Options"] == "nosniff"
            assert "Content-Security-Policy" in r.headers
            csp = r.headers["Content-Security-Policy"]
            assert "style-src 'unsafe-inline'" in csp
    finally:
        srv.close()


# --- Approvals queue endpoints ------------------------------------------------


def _make_approval(approvals_dir, ledger_path, tool_id="my_tool", profile_id="p"):
    from werktools.hub.approvals import request_approval

    return request_approval(approvals_dir, ledger_path, tool_id, profile_id, call_args={"x": 1})


def test_get_approvals_returns_pending_redacted(tmp_path):
    """GET /api/approvals lists pending records; the one-use token is redacted."""
    ledger = tmp_path / "l.jsonl"
    adir = tmp_path / "hub-approvals"
    rec = _make_approval(adir, ledger)
    assert rec.token  # sanity: record carries a token on disk

    srv = _Server(tmp_path)
    try:
        with _resilient_urlopen(srv.url("/api/approvals")) as r:
            assert r.status == 200
            rows = json.loads(r.read())
        assert len(rows) == 1
        assert rows[0]["request_id"] == rec.request_id
        assert rows[0]["status"] == "pending"
        assert rows[0]["tool_id"] == "my_tool"
        assert "token" not in rows[0], "token must be redacted from GET /api/approvals"
    finally:
        srv.close()


def test_get_approvals_loopback_only(tmp_path):
    """GET /api/approvals with a non-loopback Host header is rejected 403."""
    srv = _Server(tmp_path)
    try:
        req = urllib.request.Request(srv.url("/api/approvals"))
        req.add_header("Host", "evil.example.com")
        try:
            _resilient_urlopen(req)
            raise AssertionError("expected 403")
        except urllib.error.HTTPError as exc:
            assert exc.code == 403
    finally:
        srv.close()


def test_resolve_blocked_without_env(tmp_path, monkeypatch):
    """POST /api/approvals/resolve is blocked when WERK_ALLOW_HUB_APPROVALS is unset."""
    monkeypatch.delenv("WERK_ALLOW_HUB_APPROVALS", raising=False)
    ledger = tmp_path / "l.jsonl"
    adir = tmp_path / "hub-approvals"
    rec = _make_approval(adir, ledger)

    srv = _Server(tmp_path)
    try:
        token = _scrape_token(srv)
        body = json.dumps({"request_id": rec.request_id, "decision": "approve"}).encode()
        req = urllib.request.Request(
            srv.url("/api/approvals/resolve"),
            data=body,
            method="POST",
            headers={"X-Werk-Token": token, "Content-Type": "application/json"},
        )
        try:
            _resilient_urlopen(req)
            raise AssertionError("expected 403")
        except urllib.error.HTTPError as exc:
            assert exc.code == 403
            assert "WERK_ALLOW_HUB_APPROVALS" in json.loads(exc.read())["error"]
    finally:
        srv.close()


def test_resolve_approve_flips_status_and_records_event(tmp_path, monkeypatch):
    """resolve approve flips status to approved, persists it, and ledgers the event."""
    monkeypatch.setenv("WERK_ALLOW_HUB_APPROVALS", "1")
    ledger = tmp_path / "l.jsonl"
    adir = tmp_path / "hub-approvals"
    rec = _make_approval(adir, ledger)

    srv = _Server(tmp_path)
    try:
        token = _scrape_token(srv)
        body = json.dumps({"request_id": rec.request_id, "decision": "approve"}).encode()
        req = urllib.request.Request(
            srv.url("/api/approvals/resolve"),
            data=body,
            method="POST",
            headers={"X-Werk-Token": token, "Content-Type": "application/json"},
        )
        with _resilient_urlopen(req) as r:
            assert r.status == 200
            resp = json.loads(r.read())
        assert resp["ok"] is True
        assert resp["status"] == "approved"
        from werktools.hub.approvals import load_record

        updated = load_record(adir, rec.request_id)
        assert updated.status == "approved"
        assert updated.resolved_by == "dashboard"
        types = [e["payload"]["type"] for e in recent_events(ledger, limit=20)]
        assert "approval.resolved" in types
    finally:
        srv.close()


def test_resolve_deny_blanks_token(tmp_path, monkeypatch):
    """resolve deny flips status to denied and blanks the token."""
    monkeypatch.setenv("WERK_ALLOW_HUB_APPROVALS", "1")
    ledger = tmp_path / "l.jsonl"
    adir = tmp_path / "hub-approvals"
    rec = _make_approval(adir, ledger)

    srv = _Server(tmp_path)
    try:
        token = _scrape_token(srv)
        body = json.dumps({"request_id": rec.request_id, "decision": "deny"}).encode()
        req = urllib.request.Request(
            srv.url("/api/approvals/resolve"),
            data=body,
            method="POST",
            headers={"X-Werk-Token": token, "Content-Type": "application/json"},
        )
        with _resilient_urlopen(req) as r:
            assert r.status == 200
            assert json.loads(r.read())["status"] == "denied"
        from werktools.hub.approvals import load_record

        updated = load_record(adir, rec.request_id)
        assert updated.status == "denied"
        assert updated.token == ""
    finally:
        srv.close()


def test_resolve_unknown_request_id_returns_404(tmp_path, monkeypatch):
    """resolve with an unknown request_id returns 404."""
    monkeypatch.setenv("WERK_ALLOW_HUB_APPROVALS", "1")
    srv = _Server(tmp_path)
    try:
        token = _scrape_token(srv)
        body = json.dumps({"request_id": "apr_000000000000", "decision": "approve"}).encode()
        req = urllib.request.Request(
            srv.url("/api/approvals/resolve"),
            data=body,
            method="POST",
            headers={"X-Werk-Token": token, "Content-Type": "application/json"},
        )
        try:
            _resilient_urlopen(req)
            raise AssertionError("expected 404")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
            assert "unknown request_id" in json.loads(exc.read())["error"]
    finally:
        srv.close()


def test_resolve_bad_decision_returns_400(tmp_path, monkeypatch):
    """resolve with a decision not in {approve,deny} returns 400."""
    monkeypatch.setenv("WERK_ALLOW_HUB_APPROVALS", "1")
    ledger = tmp_path / "l.jsonl"
    adir = tmp_path / "hub-approvals"
    rec = _make_approval(adir, ledger)

    srv = _Server(tmp_path)
    try:
        token = _scrape_token(srv)
        body = json.dumps({"request_id": rec.request_id, "decision": "YOLO"}).encode()
        req = urllib.request.Request(
            srv.url("/api/approvals/resolve"),
            data=body,
            method="POST",
            headers={"X-Werk-Token": token, "Content-Type": "application/json"},
        )
        try:
            _resilient_urlopen(req)
            raise AssertionError("expected 400")
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
            assert "invalid decision" in json.loads(exc.read())["error"]
    finally:
        srv.close()


def test_resolve_bad_request_id_format_returns_400(tmp_path, monkeypatch):
    """A malformed request_id (path-traversal shape) returns 400, not 409."""
    monkeypatch.setenv("WERK_ALLOW_HUB_APPROVALS", "1")
    srv = _Server(tmp_path)
    try:
        token = _scrape_token(srv)
        body = json.dumps({"request_id": "../../etc/passwd", "decision": "approve"}).encode()
        req = urllib.request.Request(
            srv.url("/api/approvals/resolve"),
            data=body,
            method="POST",
            headers={"X-Werk-Token": token, "Content-Type": "application/json"},
        )
        try:
            _resilient_urlopen(req)
            raise AssertionError("expected 400")
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
            assert "invalid request_id" in json.loads(exc.read())["error"]
    finally:
        srv.close()


def test_resolve_already_resolved_returns_409(tmp_path, monkeypatch):
    """Resolving an already-approved record returns 409 (not pending)."""
    monkeypatch.setenv("WERK_ALLOW_HUB_APPROVALS", "1")
    ledger = tmp_path / "l.jsonl"
    adir = tmp_path / "hub-approvals"
    rec = _make_approval(adir, ledger)
    from werktools.hub.approvals import approve_request

    approve_request(adir, ledger, rec.request_id, resolved_by="test")

    srv = _Server(tmp_path)
    try:
        token = _scrape_token(srv)
        body = json.dumps({"request_id": rec.request_id, "decision": "deny"}).encode()
        req = urllib.request.Request(
            srv.url("/api/approvals/resolve"),
            data=body,
            method="POST",
            headers={"X-Werk-Token": token, "Content-Type": "application/json"},
        )
        try:
            _resilient_urlopen(req)
            raise AssertionError("expected 409")
        except urllib.error.HTTPError as exc:
            assert exc.code == 409
            assert "not pending" in json.loads(exc.read())["error"]
    finally:
        srv.close()


def test_resolve_bad_session_token_returns_403(tmp_path, monkeypatch):
    """Resolve with a wrong session token is rejected by the state-change gate."""
    monkeypatch.setenv("WERK_ALLOW_HUB_APPROVALS", "1")
    ledger = tmp_path / "l.jsonl"
    adir = tmp_path / "hub-approvals"
    rec = _make_approval(adir, ledger)

    srv = _Server(tmp_path)
    try:
        body = json.dumps({"request_id": rec.request_id, "decision": "approve"}).encode()
        req = urllib.request.Request(
            srv.url("/api/approvals/resolve"),
            data=body,
            method="POST",
            headers={"X-Werk-Token": "wrong-token", "Content-Type": "application/json"},
        )
        try:
            _resilient_urlopen(req)
            raise AssertionError("expected 403")
        except urllib.error.HTTPError as exc:
            assert exc.code == 403
    finally:
        srv.close()


# ---------------------------------------------------------------------------
# Onboard endpoint (GET /api/onboard dry-run, POST /api/onboard/apply)
#
# The Doctor surfaced in the desktop UI: discover MCP servers from the
# operator's agent-host configs (Claude/Cursor/...) and optionally adopt them
# into hub.json.  PRESENCE-ONLY — env-var KEY names may appear, values never.
# ---------------------------------------------------------------------------

_LEAK_VALUE = "sk_live_DO_NOT_LEAK_THIS_VALUE_42"


def _fake_host_home(home, *, server_name="weather-mcp"):
    """Write a Claude host config (~/.claude.json) with one MCP server that
    carries a secret env value, and return the home dir.  onboard() reads
    KEY names only — the value must never surface."""
    (home / ".claude.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    server_name: {
                        "command": "npx",
                        "args": ["-y", "weather"],
                        "env": {"WEATHER_API_KEY": _LEAK_VALUE},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return home


def test_get_onboard_discovers_presence_only(tmp_path, monkeypatch):
    """GET /api/onboard returns discovered servers; key NAME appears, value never."""
    home = tmp_path / "home"
    home.mkdir()
    _fake_host_home(home)
    monkeypatch.setattr("pathlib.Path.home", lambda: home)

    srv = _Server(tmp_path, config_path=tmp_path / "hub.json")
    try:
        with _resilient_urlopen(srv.url("/api/onboard")) as r:
            assert r.status == 200
            raw = r.read()
        body = json.loads(raw)
        assert body["by_host"].get("claude") == 1
        names = [d["name"] for d in body["discovered"]]
        assert "weather-mcp" in names
        # Env KEY name may appear (metadata); the VALUE must never appear.
        text = raw.decode("utf-8")
        assert "WEATHER_API_KEY" in text
        assert _LEAK_VALUE not in text
        assert body["apply_allowed"] is False  # env unset by default
    finally:
        srv.close()


def test_get_onboard_loopback_only(tmp_path, monkeypatch):
    """GET /api/onboard with a non-loopback Host header is rejected 403."""
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
    (tmp_path / "home").mkdir(exist_ok=True)
    srv = _Server(tmp_path, config_path=tmp_path / "hub.json")
    try:
        req = urllib.request.Request(srv.url("/api/onboard"))
        req.add_header("Host", "evil.example.com")
        try:
            _resilient_urlopen(req)
            raise AssertionError("expected 403")
        except urllib.error.HTTPError as exc:
            assert exc.code == 403
    finally:
        srv.close()


def test_onboard_apply_blocked_without_env(tmp_path, monkeypatch):
    """POST /api/onboard/apply is blocked when WERK_ALLOW_HUB_ONBOARD is unset."""
    monkeypatch.delenv("WERK_ALLOW_HUB_ONBOARD", raising=False)
    home = tmp_path / "home"
    home.mkdir()
    _fake_host_home(home)
    monkeypatch.setattr("pathlib.Path.home", lambda: home)

    hub_json = tmp_path / "hub.json"
    srv = _Server(tmp_path, config_path=hub_json)
    try:
        token = _scrape_token(srv)
        req = urllib.request.Request(
            srv.url("/api/onboard/apply"),
            data=b"{}",
            method="POST",
            headers={"X-Werk-Token": token, "Content-Type": "application/json"},
        )
        try:
            _resilient_urlopen(req)
            raise AssertionError("expected 403")
        except urllib.error.HTTPError as exc:
            assert exc.code == 403
            assert "WERK_ALLOW_HUB_ONBOARD" in json.loads(exc.read())["error"]
        assert not hub_json.exists()  # nothing written
    finally:
        srv.close()


def test_onboard_apply_adopts_and_never_leaks_value(tmp_path, monkeypatch):
    """apply writes hub.json + ledgers the adoption; the secret VALUE never lands."""
    monkeypatch.setenv("WERK_ALLOW_HUB_ONBOARD", "1")
    home = tmp_path / "home"
    home.mkdir()
    _fake_host_home(home)
    monkeypatch.setattr("pathlib.Path.home", lambda: home)

    hub_json = tmp_path / "hub.json"
    srv = _Server(tmp_path, config_path=hub_json)
    try:
        token = _scrape_token(srv)
        req = urllib.request.Request(
            srv.url("/api/onboard/apply"),
            data=b"{}",
            method="POST",
            headers={"X-Werk-Token": token, "Content-Type": "application/json"},
        )
        with _resilient_urlopen(req) as r:
            assert r.status == 200
            resp = json.loads(r.read())
        assert resp["ok"] is True
        assert resp["added"], "expected at least one adopted connector"
        # hub.json was written and the secret value never touched disk.
        persisted = hub_json.read_text(encoding="utf-8")
        assert _LEAK_VALUE not in persisted
        # onboard() records config.connector.added into the hub config's own
        # ledger (config.ledger_path) — read that file, not the test server's.
        from pathlib import Path

        from werktools.hub.registry import load_config

        adopted_ledger = Path(load_config(hub_json).ledger_path)
        types = [e["payload"]["type"] for e in recent_events(adopted_ledger, limit=20)]
        assert "config.connector.added" in types
    finally:
        srv.close()


def test_get_onboard_no_config_path_returns_503(tmp_path, monkeypatch):
    """GET /api/onboard with config_path=None degrades honestly (503, no 500)."""
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path / "home")
    (tmp_path / "home").mkdir(exist_ok=True)
    srv = _Server(tmp_path)  # config_path defaults to None
    try:
        try:
            _resilient_urlopen(srv.url("/api/onboard"))
            raise AssertionError("expected 503")
        except urllib.error.HTTPError as exc:
            assert exc.code == 503
            assert "config_path" in json.loads(exc.read())["error"]
    finally:
        srv.close()


def test_onboard_apply_unknown_host_returns_400(tmp_path, monkeypatch):
    """apply with an unknown host filter is rejected 400, not a silent no-op."""
    monkeypatch.setenv("WERK_ALLOW_HUB_ONBOARD", "1")
    home = tmp_path / "home"
    home.mkdir()
    _fake_host_home(home)
    monkeypatch.setattr("pathlib.Path.home", lambda: home)

    hub_json = tmp_path / "hub.json"
    srv = _Server(tmp_path, config_path=hub_json)
    try:
        token = _scrape_token(srv)
        body = json.dumps({"host": "not-a-real-host"}).encode()
        req = urllib.request.Request(
            srv.url("/api/onboard/apply"),
            data=body,
            method="POST",
            headers={"X-Werk-Token": token, "Content-Type": "application/json"},
        )
        try:
            _resilient_urlopen(req)
            raise AssertionError("expected 400")
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
            assert "unknown host" in json.loads(exc.read())["error"]
        assert not hub_json.exists()  # nothing adopted
    finally:
        srv.close()
