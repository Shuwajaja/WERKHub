import json

from werktools.cli import main


def _init_in(tmp_path, monkeypatch):
    """Init a hub config inside an isolated CWD so the relative ledger path
    (.werktools/hub-ledger.jsonl) and all event writes stay in tmp_path."""
    monkeypatch.chdir(tmp_path)
    path = tmp_path / ".werktools" / "hub.json"
    assert main(["--config", str(path), "hub", "init"]) == 0
    return path


def test_hub_doctor_shows_runtimes_panel(tmp_path, capsys, monkeypatch):
    path = _init_in(tmp_path, monkeypatch)
    capsys.readouterr()
    code = main(["--config", str(path), "hub", "doctor"])
    out = capsys.readouterr().out
    assert "Runtimes" in out
    assert "Claude Code" in out
    assert "[at-risk]" in out  # goose/gemini are flagged
    assert code == 0


def test_hub_doctor_json_shape(tmp_path, capsys, monkeypatch):
    path = _init_in(tmp_path, monkeypatch)
    capsys.readouterr()
    code = main(["--config", str(path), "hub", "doctor", "--json"])
    out = capsys.readouterr().out
    data = json.loads(out)
    assert set(data) >= {"invariants", "config_ok", "runtimes", "total_violations"}
    assert data["config_ok"] is True
    assert data["runtimes"]["total"] == 9
    assert isinstance(data["runtimes"]["probes"], list)
    assert len(data["runtimes"]["probes"]) == 9
    assert code == 0


def test_hub_doctor_host_filter_limits_panel(tmp_path, capsys, monkeypatch):
    path = _init_in(tmp_path, monkeypatch)
    capsys.readouterr()
    main(["--config", str(path), "hub", "doctor", "--host", "claude"])
    out = capsys.readouterr().out
    assert "Claude Code" in out
    assert "Windsurf" not in out


def test_hub_doctor_emits_runtime_probed_event(tmp_path, monkeypatch):
    path = _init_in(tmp_path, monkeypatch)
    calls: list[tuple[str, dict]] = []
    from werktools.hub import ledger as hl

    monkeypatch.setattr(hl, "record_event", lambda p, t, payload=None: calls.append((t, payload)))
    main(["--config", str(path), "hub", "doctor"])
    probed = [payload for t, payload in calls if t == "runtime.probed"]
    assert probed, "doctor must emit a runtime.probed ledger event"
    assert "detected" in probed[0]
    assert probed[0]["probe_versions"] is False


def test_hub_doctor_detected_only_filters_panel(tmp_path, capsys, monkeypatch):
    path = _init_in(tmp_path, monkeypatch)
    from werktools.hub import runtimes as rt
    from werktools.hub.runtimes import RuntimeProbe, RuntimeReport

    def _probe(host, detected):
        return RuntimeProbe(
            host_id=host, binary_found=detected, binary_path=None, gui_path_found=None,
            config_path_found=None, version_str=None, version_error=None,
            token_env_present=False, token_file_present=False, token_file_mtime=None, detected=detected,
        )

    report = RuntimeReport(
        probes=(_probe("claude", True), _probe("windsurf", False)),
        generated_at="2026-06-19T00:00:00Z", probe_versions=False,
    )
    monkeypatch.setattr(rt, "probe_all", lambda *, probe_versions=False: report)
    capsys.readouterr()
    main(["--config", str(path), "hub", "doctor", "--detected-only"])
    out = capsys.readouterr().out
    assert "Claude Code" in out
    assert "Windsurf" not in out


def test_hub_doctor_json_reports_unreadable_config(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = tmp_path / "hub.json"
    path.write_text("{ broken json", encoding="utf-8")
    code = main(["--config", str(path), "hub", "doctor", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert data["config_ok"] is False
    assert data["config_initialized"] is True
    assert data["config_error"]
    assert data["total_violations"] >= 1
    assert code == 1


def test_hub_doctor_unknown_host_warns_on_stderr(tmp_path, capsys, monkeypatch):
    path = _init_in(tmp_path, monkeypatch)
    capsys.readouterr()
    main(["--config", str(path), "hub", "doctor", "--host", "nonexistent-host"])
    assert "unknown host" in capsys.readouterr().err.lower()


def test_hub_doctor_without_config_emits_no_event(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    calls: list = []
    from werktools.hub import ledger as hl

    monkeypatch.setattr(hl, "record_event", lambda *a, **k: calls.append(a))
    code = main(["--config", str(tmp_path / "nope.json"), "hub", "doctor"])
    assert code == 0
    assert calls == []  # no config -> no ledger path -> no runtime.probed event


def test_cli_registry_approve_warns_when_unvetted(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from werktools.hub.contracts import RegistryCandidate
    from werktools.hub.discovery import stage_install

    config = tmp_path / "hub.json"
    main(["--config", str(config), "hub", "init"])
    capsys.readouterr()
    gate = tmp_path / "gate"
    cand = RegistryCandidate.from_dict(
        {"id": "rando-x", "name": "rando", "description": "d",
         "packages": [{"registryType": "oci", "identifier": "mcp/bar"}]}
    )
    request = stage_install(gate, cand)
    code = main([
        "--config", str(config), "hub", "registry", "approve",
        "--request-id", request.request_id, "--gate-root", str(gate),
        "--hub-config", str(tmp_path / "target.json"),
    ])
    streams = capsys.readouterr()
    assert code == 0
    assert "Connected:" in streams.out
    assert "UNVETTED" in streams.err


def test_hub_doctor_probe_versions_flag_is_forwarded(tmp_path, capsys, monkeypatch):
    path = _init_in(tmp_path, monkeypatch)
    capsys.readouterr()
    seen: dict[str, bool] = {}
    from werktools.hub import runtimes as rt

    real = rt.probe_all

    def fake(*, probe_versions=False):
        seen["pv"] = probe_versions
        return real(probe_versions=False)  # never spawn a real subprocess in tests

    monkeypatch.setattr(rt, "probe_all", fake)
    main(["--config", str(path), "hub", "doctor", "--probe-versions"])
    assert seen.get("pv") is True


def test_hub_init_writes_config(tmp_path, capsys):
    path = tmp_path / ".werktools" / "hub.json"

    code = main(["--config", str(path), "hub", "init"])

    assert code == 0
    assert path.exists()
    assert "initialized" in capsys.readouterr().out.lower()


def test_hub_init_community_writes_community_profiles(tmp_path):
    path = tmp_path / "hub.json"
    assert main(["--config", str(path), "hub", "init", "--community"]) == 0
    body = json.loads(path.read_text(encoding="utf-8"))
    assert body["default_profile"] == "community-reader"
    assert {p["id"] for p in body["profiles"]} == {"community-reader", "community-builder", "community-admin"}


def test_hub_init_without_community_is_operator_default(tmp_path):
    path = tmp_path / "hub.json"
    assert main(["--config", str(path), "hub", "init"]) == 0
    body = json.loads(path.read_text(encoding="utf-8"))
    assert body["default_profile"] == "codex-builder"


def test_hub_init_is_idempotent(tmp_path):
    path = tmp_path / "hub.json"
    assert main(["--config", str(path), "hub", "init"]) == 0

    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["name"] = "custom-hub"
    path.write_text(json.dumps(raw), encoding="utf-8")

    assert main(["--config", str(path), "hub", "init"]) == 0
    assert json.loads(path.read_text(encoding="utf-8"))["name"] == "custom-hub"


def test_hub_status_prints_counts(tmp_path, capsys):
    path = tmp_path / "hub.json"
    main(["--config", str(path), "hub", "init"])

    code = main(["--config", str(path), "hub", "status"])

    out = capsys.readouterr().out
    assert code == 0
    assert "Hub:" in out
    assert "Tools:" in out
    assert "Profiles:" in out


def test_hub_tools_is_profile_filtered(tmp_path, capsys):
    path = tmp_path / "hub.json"
    main(["--config", str(path), "hub", "init"])

    code = main(["--config", str(path), "hub", "tools", "--profile", "claude-reviewer"])

    out = capsys.readouterr().out
    assert code == 0
    assert "docs.search" in out
    assert "github.create_pr" not in out


def test_hub_policy_explain_prints_decision(tmp_path, capsys):
    path = tmp_path / "hub.json"
    main(["--config", str(path), "hub", "init"])

    code = main(
        [
            "--config",
            str(path),
            "hub",
            "policy",
            "explain",
            "github.create_pr",
            "--profile",
            "codex-builder",
        ]
    )

    out = capsys.readouterr().out
    assert code == 0
    assert "Decision: approval_required" in out


def test_hub_init_json_is_plain_config(tmp_path):
    path = tmp_path / "hub.json"
    main(["--config", str(path), "hub", "init"])

    body = json.loads(path.read_text(encoding="utf-8"))

    assert body["name"] == "werk-hub"


def test_hub_serve_builds_server_and_runs_stdio(tmp_path, monkeypatch):
    import werktools.hub.server as hub_server

    calls = {}

    class FakeServer:
        def run(self):
            calls["ran"] = True

    def fake_build(config, profile_id=None, **kwargs):
        calls["profile"] = profile_id
        return FakeServer()

    monkeypatch.setattr(hub_server, "build_hub_server", fake_build)
    path = tmp_path / "hub.json"

    code = main(["--config", str(path), "hub", "serve", "--profile", "claude-reviewer"])

    assert code == 0
    assert calls == {"profile": "claude-reviewer", "ran": True}


def test_hub_serve_uses_env_profile_fallback(tmp_path, monkeypatch):
    import werktools.hub.server as hub_server

    calls = {}

    class FakeServer:
        def run(self):
            calls["ran"] = True

    def fake_build(config, profile_id=None, **kwargs):
        calls["profile"] = profile_id
        return FakeServer()

    monkeypatch.setattr(hub_server, "build_hub_server", fake_build)
    monkeypatch.setenv("WERKTOOLS_HUB_PROFILE", "codex-builder")
    path = tmp_path / "hub.json"

    code = main(["--config", str(path), "hub", "serve"])

    assert code == 0
    assert calls["profile"] == "codex-builder"


def test_hub_serve_rejects_unknown_profile(tmp_path, capsys):
    path = tmp_path / "hub.json"
    main(["--config", str(path), "hub", "init"])

    code = main(["--config", str(path), "hub", "serve", "--profile", "nope"])

    assert code == 1
    assert "profile" in capsys.readouterr().err.lower()


def test_hub_serve_forwards_status_port(tmp_path, monkeypatch):
    import werktools.hub.server as hub_server

    seen = {}

    class FakeServer:
        def run(self):
            pass

    def fake_build(config, profile_id=None, status_port=None, **kwargs):
        seen["status_port"] = status_port
        return FakeServer()

    monkeypatch.setattr(hub_server, "build_hub_server", fake_build)

    code = main(["--config", str(tmp_path / "hub.json"), "hub", "serve", "--status-port", "9371"])

    assert code == 0
    assert seen["status_port"] == 9371


def test_hub_pool_status_prints_json(tmp_path, capsys):
    path = tmp_path / "hub.json"
    # pool-status lazily creates the default config; no init output to drain
    code = main(["--config", str(path), "hub", "pool-status", "--profile", "codex-builder"])

    out = capsys.readouterr().out
    assert code == 0
    assert json.loads(out)["profile_id"] == "codex-builder"


def test_serve_accepts_config_after_subcommand_readme_snippet():
    # The exact README/host-registration snippet that exited 2 at HEAD (MF1):
    # `werktools hub serve --profile community-builder --config .werktools/hub.json`.
    from werktools.cli import _parser

    ns = _parser().parse_args(
        ["hub", "serve", "--profile", "community-builder", "--config", ".werktools/hub.json"]
    )
    assert ns.command == "hub"
    assert ns.hub_command == "serve"
    assert ns.profile == "community-builder"
    assert ns.config == ".werktools/hub.json"


def test_serve_config_after_subcommand_does_not_clobber_toplevel_default():
    # argparse.SUPPRESS: --config on the subparser only sets args.config when
    # explicitly given after `serve`; it must never overwrite the top-level
    # value or the global default.
    from werktools.cli import _parser

    # no --config on the subcommand -> top-level default survives
    ns = _parser().parse_args(["hub", "serve", "--profile", "x"])
    assert ns.config == ".werktools/hub.json"

    # top-level placement is still honored
    ns2 = _parser().parse_args(["--config", "top.json", "hub", "serve", "--profile", "x"])
    assert ns2.config == "top.json"
