from werktools.hub.ledger import recent_events, record_event, redact_payload


def test_redact_payload_masks_secret_like_keys():
    payload = {"api_key": "abc", "nested": {"token": "secret"}, "safe": "ok"}

    redacted = redact_payload(payload)

    assert redacted["api_key"] == "[redacted]"
    assert redacted["nested"]["token"] == "[redacted]"
    assert redacted["safe"] == "ok"


def test_record_event_appends_jsonl(tmp_path):
    path = tmp_path / "hub.jsonl"

    record = record_event(path, "policy.explained", {"tool_id": "docs.search"})

    assert record["payload"]["type"] == "policy.explained"
    assert recent_events(path, limit=1)[0]["event_id"] == record["event_id"]


def test_recent_events_respects_limit(tmp_path):
    path = tmp_path / "hub.jsonl"
    for index in range(3):
        record_event(path, "tool.search", {"index": index})

    assert len(recent_events(path, limit=2)) == 2


def _forge_first_line(path):
    """Mutate the first record's payload while leaving its stored hash stale."""
    import json

    lines = path.read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[0])
    rec["payload"]["forged"] = True
    lines[0] = json.dumps(rec, ensure_ascii=False)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_recent_events_verified_flags_tampered_chain(tmp_path):
    # MF12: a forged ledger must be surfaced WITH an integrity flag, never
    # presented silently as clean evidence.
    from werktools.hub.ledger import recent_events_verified

    path = tmp_path / "hub.jsonl"
    record_event(path, "policy.explained", {"a": 1})
    record_event(path, "tool.search", {"b": 2})

    events, verified, errors = recent_events_verified(path)
    assert verified is True
    assert errors == 0
    assert len(events) == 2

    _forge_first_line(path)

    events2, verified2, errors2 = recent_events_verified(path)
    assert verified2 is False
    assert errors2 >= 1
    assert len(events2) == 2  # events still surfaced, not hidden
    # recent_events keeps its list[dict] contract (backward-compat)
    assert isinstance(recent_events(path), list)
