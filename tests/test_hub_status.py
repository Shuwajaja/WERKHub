import json

from werktools.hub.pool import PoolEntry, WarmPool
from werktools.hub.registry import load_config
from werktools.hub.status import hub_status


def _config():
    return load_config(
        {
            "name": "werk-hub",
            "default_profile": "p",
            "profiles": [
                {"id": "p", "permission_profile": "balanced", "visible_tags": ["read"], "allowed_servers": ["docs", "db"]}
            ],
            "tools": [],
        }
    )


def test_no_pool_all_unconfigured():
    status = hub_status(_config(), "p", pool=None)
    states = {s.server_id: s.state for s in status.servers}
    assert states == {"docs": "unconfigured", "db": "unconfigured"}


def test_warm_entry_reported():
    pool = WarmPool()
    pool.put(
        PoolEntry(
            server_id="docs", pid=4321, name="docs", started_at="2020-01-01T00:00:00Z",
            last_used_at="2020-01-01T00:00:00Z", tool_count=5, state="warm",
        )
    )
    status = hub_status(_config(), "p", pool=pool)
    docs = next(s for s in status.servers if s.server_id == "docs")
    assert docs.state == "warm"
    assert docs.pid == 4321
    assert docs.tool_count == 5
    assert docs.uptime_s > 0


def test_only_allowed_servers():
    cfg = load_config(
        {
            "name": "werk-hub",
            "default_profile": "p",
            "profiles": [{"id": "p", "permission_profile": "balanced", "visible_tags": ["read"], "allowed_servers": ["docs"]}],
            "tools": [],
        }
    )
    pool = WarmPool()
    pool.put(PoolEntry(server_id="secret", pid=1, name="secret", started_at="t", last_used_at="t"))
    status = hub_status(cfg, "p", pool=pool)
    assert [s.server_id for s in status.servers] == ["docs"]


def test_to_dict_json_serializable():
    status = hub_status(_config(), "p", pool=None)
    text = json.dumps(status.to_dict())
    assert "generated_at" in text
    assert status.generated_at.endswith("Z")


def test_hub_status_warns_on_malformed_pool_timestamps():
    """Malformed started_at/last_used_at must emit UserWarning and report uptime/idle as 0."""
    import pytest

    pool = WarmPool()
    pool.put(
        PoolEntry(
            server_id="docs",
            pid=9999,
            name="docs",
            started_at="not-a-date",
            last_used_at="also-not-a-date",
            tool_count=0,
            state="warm",
        )
    )
    with pytest.warns(UserWarning, match="unparseable"):
        status = hub_status(_config(), "p", pool=pool)

    docs = next(s for s in status.servers if s.server_id == "docs")
    assert docs.uptime_s == 0
    assert docs.idle_for_s == 0


def test_hub_status_unknown_profile_raises_key_error():
    """hub_status must raise KeyError for an unknown profile_id (Fix 22 — pin contract)."""
    import pytest

    with pytest.raises(KeyError):
        hub_status(_config(), "nonexistent", pool=None)
