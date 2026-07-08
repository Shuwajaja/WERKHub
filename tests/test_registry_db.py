"""Tests for the SQLite capability-registry read-model (hub/registry_db.py)."""

from __future__ import annotations

from werktools.hub import registry_db as rdb

FIXT = [
    {
        "server_id": "a-cloud", "category": "cloud", "trust_tier": "Official",
        "what_it_is": "cloud thing", "maintainer": "acme", "maintenance": "active",
        "popularity": "high", "security_note": "", "deluxe_base": True,
        "deluxe_reason": "broad", "verified": True,
    },
    {
        "server_id": "b-data", "category": "data", "trust_tier": "Security-Scanned",
        "what_it_is": "data thing", "maintainer": "dataco", "maintenance": "active",
        "popularity": "medium", "security_note": "needs token", "deluxe_base": False,
        "deluxe_reason": "", "verified": True,
    },
    {
        "server_id": "c-cloud", "category": "cloud", "trust_tier": "Community-Unverified",
        "what_it_is": "another cloud helper", "maintainer": "", "maintenance": "unknown",
        "popularity": "unverified", "security_note": "", "deluxe_base": False,
        "deluxe_reason": "", "verified": False,
    },
]


def test_build_and_query_by_category(tmp_path):
    db = tmp_path / "r.db"
    assert rdb.build_registry(db, FIXT) == 3
    cloud = rdb.query_capabilities(db, category="cloud")
    assert {c.id for c in cloud} == {"a-cloud", "c-cloud"}


def test_query_deluxe_only(tmp_path):
    db = tmp_path / "r.db"
    rdb.build_registry(db, FIXT)
    deluxe = rdb.query_capabilities(db, deluxe_only=True)
    assert [c.id for c in deluxe] == ["a-cloud"]
    assert deluxe[0].deluxe_base is True


def test_query_by_trust(tmp_path):
    db = tmp_path / "r.db"
    rdb.build_registry(db, FIXT)
    assert [c.id for c in rdb.query_capabilities(db, trust_tier="Official")] == ["a-cloud"]


def test_get_capability(tmp_path):
    db = tmp_path / "r.db"
    rdb.build_registry(db, FIXT)
    cap = rdb.get_capability(db, "b-data")
    assert cap is not None
    assert cap.category == "data" and cap.verified is True and cap.kind == "tool"
    assert rdb.get_capability(db, "missing") is None


def test_build_idempotent_and_dedup(tmp_path):
    db = tmp_path / "r.db"
    rdb.build_registry(db, FIXT)
    # rebuild with a duplicate id -> still 3, no error
    assert rdb.build_registry(db, FIXT + [FIXT[0]]) == 3
    assert len(rdb.query_capabilities(db)) == 3


def test_search_matches_id_and_text(tmp_path):
    db = tmp_path / "r.db"
    rdb.build_registry(db, FIXT)
    assert {c.id for c in rdb.query_capabilities(db, search="cloud")} == {"a-cloud", "c-cloud"}
    assert {c.id for c in rdb.query_capabilities(db, search="dataco")} == {"b-data"}


def test_category_counts(tmp_path):
    db = tmp_path / "r.db"
    rdb.build_registry(db, FIXT)
    assert rdb.category_counts(db) == {"cloud": 2, "data": 1}


def test_failclosed_missing_db(tmp_path):
    db = tmp_path / "nope.db"
    assert rdb.query_capabilities(db) == []
    assert rdb.get_capability(db, "x") is None
    assert rdb.category_counts(db) == {}


def test_failclosed_corrupt_db(tmp_path):
    import pytest

    db = tmp_path / "corrupt.db"
    db.write_bytes(b"this is not a sqlite database \x00\x01\x02")
    # read paths must not raise on a corrupt file, and must emit a warning (Fix 2)
    with pytest.warns(UserWarning, match="registry _read failed"):
        assert rdb.query_capabilities(db) == []
    with pytest.warns(UserWarning, match="registry _read failed"):
        assert rdb.get_capability(db, "x") is None
    with pytest.warns(UserWarning, match="registry _category_counts failed"):
        assert rdb.category_counts(db) == {}


def test_unknown_trust_fails_closed(tmp_path):
    db = tmp_path / "r.db"
    rdb.build_registry(db, [{"server_id": "weird", "trust_tier": "Totally-Made-Up"}])
    cap = rdb.get_capability(db, "weird")
    assert cap is not None and cap.trust_tier == "Community-Unverified"


def test_load_seed_and_build_real_snapshot(tmp_path):
    rows = rdb.load_seed()
    assert len(rows) >= 60  # the committed point-in-time snapshot (~69)
    db = tmp_path / "seed.db"
    assert rdb.build_registry(db, rows) == len(rows)
    # the curated deluxe-base subset is present and non-trivial
    assert len(rdb.query_capabilities(db, deluxe_only=True, limit=500)) >= 20
    # a known real entry survives the round-trip
    grafana = rdb.get_capability(db, "mcp-grafana")
    assert grafana is not None and grafana.category == "observability"


def test_load_seed_missing_path(tmp_path):
    assert rdb.load_seed(tmp_path / "nope.json") == []


def test_build_registry_raises_oserror_not_name_error(tmp_path, monkeypatch):
    """build_registry must raise the original OSError when sqlite3.connect fails,
    not a secondary NameError from the finally clause (Fix 1)."""
    import sqlite3

    import pytest

    def boom(path):
        raise OSError("simulated connect failure")

    monkeypatch.setattr(sqlite3, "connect", boom)

    with pytest.raises(OSError, match="simulated connect failure"):
        rdb.build_registry(tmp_path / "r.db", FIXT)


def test_needs_keys_and_risk_roundtrip(tmp_path):
    db = tmp_path / "r.db"
    rdb.build_registry(db, [{
        "server_id": "x", "category": "data", "trust_tier": "Official",
        "needs_keys": ["X_API_KEY", "X_TOKEN"], "risk": "write",
    }])
    cap = rdb.get_capability(db, "x")
    assert cap is not None
    assert cap.needs_keys == ("X_API_KEY", "X_TOKEN")
    assert cap.risk == "write"


def test_needs_keys_derived_from_security_note(tmp_path):
    db = tmp_path / "r.db"
    rdb.build_registry(db, [{
        "server_id": "stripe", "trust_tier": "Security-Scanned",
        "security_note": "requires STRIPE_SECRET_KEY; never use a live key in dev",
    }])
    cap = rdb.get_capability(db, "stripe")
    assert cap is not None and "STRIPE_SECRET_KEY" in cap.needs_keys


def test_failed_rebuild_preserves_prior_state(tmp_path):
    """A mid-build exception must NOT destroy the existing data (transaction rollback)."""
    import pytest

    db = tmp_path / "r.db"
    # Build a good initial state.
    assert rdb.build_registry(db, FIXT) == 3

    # Attempt a rebuild that includes a non-dict row that will trigger a warning
    # but should not raise.  Then test with a row that genuinely causes an error.
    bad_rows = [
        {"server_id": "good-one", "trust_tier": "Official"},
        "not-a-dict",  # non-dict — triggers warnings.warn + skip, no raise
        {"server_id": "good-two", "trust_tier": "Security-Scanned"},
    ]
    import warnings as _w
    with _w.catch_warnings(record=True):
        count = rdb.build_registry(db, bad_rows)
    # Both good rows inserted, non-dict skipped — commit succeeds.
    assert count == 2

    # Simulate a catastrophic mid-build error via a patched _norm.
    import unittest.mock as mock

    call_count = 0
    original_norm = rdb._norm

    def _bad_norm(row):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise RuntimeError("simulated mid-build error")
        return original_norm(row)

    with mock.patch.object(rdb, "_norm", side_effect=_bad_norm):
        with pytest.raises(RuntimeError, match="simulated mid-build"):
            rdb.build_registry(db, FIXT)

    # The prior 2-row state from the successful rebuild above must survive.
    survivors = rdb.query_capabilities(db)
    assert len(survivors) == 2


def test_query_order_higher_trust_first(tmp_path):
    """Official rows must appear before Security-Scanned before Community-Unverified."""
    db = tmp_path / "r.db"
    rows = [
        {"server_id": "unverified-tool", "trust_tier": "Community-Unverified", "deluxe_base": False},
        {"server_id": "official-tool", "trust_tier": "Official", "deluxe_base": False},
        {"server_id": "scanned-tool", "trust_tier": "Security-Scanned", "deluxe_base": False},
    ]
    assert rdb.build_registry(db, rows) == 3
    result = rdb.query_capabilities(db)
    tiers = [c.trust_tier for c in result]
    assert tiers.index("Official") < tiers.index("Security-Scanned") < tiers.index("Community-Unverified")


def test_none_fields_coerced_to_defaults_not_string_None(tmp_path):
    """Explicit None for category/risk must yield defaults, not the string 'None'."""
    import warnings as _w

    db = tmp_path / "r.db"
    row = {"server_id": "nullrow", "category": None, "risk": None}
    with _w.catch_warnings(record=True):
        rdb.build_registry(db, [row])
    cap = rdb.get_capability(db, "nullrow")
    assert cap is not None
    assert cap.category == "other", f"expected 'other', got {cap.category!r}"
    assert cap.risk == "unknown", f"expected 'unknown', got {cap.risk!r}"
    assert cap.category != "None"
    assert cap.risk != "None"


def test_duplicate_id_emits_warning(tmp_path):
    """build_registry must warn when two rows share the same server_id."""
    import warnings as _w

    db = tmp_path / "r.db"
    rows = [
        {"server_id": "dup", "trust_tier": "Official"},
        {"server_id": "dup", "trust_tier": "Security-Scanned"},
    ]
    with _w.catch_warnings(record=True) as caught:
        _w.simplefilter("always")
        count = rdb.build_registry(db, rows)
    assert count == 1, "only one row should be inserted for duplicate ids"
    messages = [str(w.message) for w in caught]
    assert any("duplicate" in m and "dup" in m for m in messages), (
        f"expected duplicate warning, got: {messages}"
    )


def test_unknown_trust_emits_warning(tmp_path):
    """_norm must warn when trust_tier is unrecognised and fall back to Community-Unverified."""
    import warnings as _w

    db = tmp_path / "r.db"
    with _w.catch_warnings(record=True) as caught:
        _w.simplefilter("always")
        rdb.build_registry(db, [{"server_id": "x", "trust_tier": "FakeTier"}])
    cap = rdb.get_capability(db, "x")
    assert cap is not None and cap.trust_tier == "Community-Unverified"
    messages = [str(w.message) for w in caught]
    assert any("unrecognised trust_tier" in m and "FakeTier" in m for m in messages), (
        f"expected trust_tier warning, got: {messages}"
    )
