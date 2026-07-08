import pytest

from werktools.hub.ledger import recent_events, record_event


def test_frozen_clock_patches_the_ledger_timestamp(tmp_path, frozen_clock):
    path = tmp_path / "ledger.jsonl"
    record_event(path, "tool.search", {"q": "x"})

    events = recent_events(path, limit=1)

    assert events[0]["ts"] == frozen_clock


def test_fake_lifecycle_child_is_alive_then_dead(fake_server_process):
    proc = fake_server_process()

    assert proc.poll() is None

    proc.terminate()
    proc.wait(timeout=5)
    assert proc.poll() is not None


def test_tmp_hub_config_is_valid(tmp_hub_config):
    path, config = tmp_hub_config

    assert path.exists()
    assert config.name == "werk-hub"
    assert config.profiles


def test_skip_guards_are_marks():
    from conftest import skip_if_not_windows, skip_if_windows

    assert isinstance(skip_if_not_windows, type(pytest.mark.skipif(False, reason="x")))
    assert isinstance(skip_if_windows, type(pytest.mark.skipif(False, reason="x")))
