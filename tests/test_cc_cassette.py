import pytest
from cassette import (
    CassetteEntry,
    CassetteReplayer,
    RecordingRequired,
    load_cassette,
    save_cassette,
)


def test_save_and_load_round_trip(tmp_path):
    path = tmp_path / "c.json"
    entries = [
        CassetteEntry(call_kwargs={"q": "a"}, response={"ok": 1}, recorded_at="2026-01-01T00:00:00Z"),
        CassetteEntry(call_kwargs={"q": "b"}, response=[1, 2], recorded_at="2026-01-01T00:00:01Z"),
    ]
    save_cassette(path, entries)

    loaded = load_cassette(path)

    assert [e.to_dict() for e in loaded] == [e.to_dict() for e in entries]


def test_replayer_returns_recorded_in_fifo_order(tmp_path):
    path = tmp_path / "c.json"
    save_cassette(
        path,
        [
            CassetteEntry(call_kwargs={}, response="first", recorded_at="t"),
            CassetteEntry(call_kwargs={}, response="second", recorded_at="t"),
        ],
    )
    replayer = CassetteReplayer(path)

    assert replayer.call() == "first"
    assert replayer.call() == "second"


def test_replayer_raises_on_miss(tmp_path):
    path = tmp_path / "c.json"
    save_cassette(path, [CassetteEntry(call_kwargs={}, response="only", recorded_at="t")])
    replayer = CassetteReplayer(path)

    replayer.call()
    with pytest.raises(RecordingRequired):
        replayer.call()


def test_load_missing_file_is_empty(tmp_path):
    assert load_cassette(tmp_path / "nope.json") == []
