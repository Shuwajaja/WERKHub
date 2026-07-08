"""Unit tests for hub/runtime_row.py (merged probe+descriptor dict)."""

from __future__ import annotations

import json

from werktools.hub.runtime_row import runtime_row
from werktools.hub.runtimes import DESCRIPTORS, RuntimeDescriptor, RuntimeProbe


def _make_probe(host_id: str = "claude", **kwargs) -> RuntimeProbe:
    defaults = dict(
        host_id=host_id,
        binary_found=True,
        binary_path="/usr/local/bin/claude",
        gui_path_found=None,
        config_path_found=None,
        version_str="1.2.3",
        version_error=None,
        token_env_present=True,
        token_file_present=False,
        token_file_mtime=None,
        detected=True,
    )
    defaults.update(kwargs)
    return RuntimeProbe(**defaults)


def _make_descriptor(host_id: str = "claude", **kwargs) -> RuntimeDescriptor:
    defaults = dict(
        host_id=host_id,
        display_name="Claude Code",
        binary_names=("claude",),
        gui_install_paths_windows=(),
        gui_install_paths_posix=(),
        config_paths=(),
        version_cmd=("claude", "--version"),
        token_env_vars=("ANTHROPIC_API_KEY",),
        token_file_paths=(),
        monogram="CC",
        at_risk=False,
        at_risk_reason="",
    )
    defaults.update(kwargs)
    return RuntimeDescriptor(**defaults)


class TestRuntimeRowKeys:
    def test_all_probe_to_dict_keys_present(self):
        probe = _make_probe()
        desc = _make_descriptor()
        row = runtime_row(probe, desc)
        for key in probe.to_dict():
            assert key in row, f"probe key {key!r} missing from runtime_row output"

    def test_display_name_added_from_descriptor(self):
        probe = _make_probe()
        desc = _make_descriptor(display_name="Claude Code")
        row = runtime_row(probe, desc)
        assert row["display_name"] == "Claude Code"

    def test_monogram_added_from_descriptor(self):
        probe = _make_probe()
        desc = _make_descriptor(monogram="CC")
        row = runtime_row(probe, desc)
        assert row["monogram"] == "CC"

    def test_at_risk_added_from_descriptor(self):
        probe = _make_probe()
        desc = _make_descriptor(at_risk=True, at_risk_reason="deprecated")
        row = runtime_row(probe, desc)
        assert row["at_risk"] is True
        assert row["at_risk_reason"] == "deprecated"

    def test_at_risk_reason_always_present_even_when_empty(self):
        probe = _make_probe()
        desc = _make_descriptor(at_risk=False, at_risk_reason="")
        row = runtime_row(probe, desc)
        assert "at_risk_reason" in row
        assert row["at_risk_reason"] == ""


class TestRuntimeRowNoOverwrite:
    def test_descriptor_fields_do_not_overwrite_probe_fields(self):
        """Descriptor fields (display_name, monogram, at_risk, at_risk_reason)
        are added alongside probe fields and must not silently overwrite them.
        All probe keys from to_dict() must retain their original values."""
        probe = _make_probe(host_id="claude", binary_found=True, version_str="2.0.0")
        desc = _make_descriptor(host_id="claude", display_name="Override Attempt")
        row = runtime_row(probe, desc)
        # Probe keys remain at probe values
        assert row["host_id"] == "claude"
        assert row["binary_found"] is True
        assert row["version_str"] == "2.0.0"
        # Display fields added correctly
        assert row["display_name"] == "Override Attempt"


class TestRuntimeRowNoTokenValue:
    def test_token_value_not_in_json_output(self):
        """No actual token value (only presence bool) should appear in the output."""
        probe = _make_probe(token_env_present=True, token_file_present=True)
        desc = _make_descriptor(token_env_vars=("ANTHROPIC_API_KEY",))
        row = runtime_row(probe, desc)
        serialized = json.dumps(row)
        # These should be absence-safe: presence booleans, not values
        assert "ANTHROPIC_API_KEY" not in serialized  # env var name is in descriptor, not row
        # The presence flags must be boolean
        assert isinstance(row["token_env_present"], bool)
        assert isinstance(row["token_file_present"], bool)


class TestRuntimeRowRealDescriptors:
    def test_runtime_row_for_all_descriptors(self):
        """Smoke test: runtime_row must work for every descriptor in DESCRIPTORS
        without raising, and produce a JSON-serializable dict."""
        for desc in DESCRIPTORS:
            probe = _make_probe(
                host_id=desc.host_id,
                binary_found=False,
                binary_path=None,
                gui_path_found=None,
                config_path_found=None,
                version_str=None,
                version_error=None,
                token_env_present=False,
                token_file_present=False,
                token_file_mtime=None,
                detected=False,
            )
            row = runtime_row(probe, desc)
            assert isinstance(row, dict), f"runtime_row returned non-dict for {desc.host_id}"
            # Must be JSON-serializable
            json.dumps(row)
            # Stable required keys
            for key in ("host_id", "detected", "display_name", "monogram", "at_risk", "at_risk_reason"):
                assert key in row, f"key {key!r} missing for host {desc.host_id!r}"
