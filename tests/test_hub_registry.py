import json

from werktools.hub.registry import (
    default_config,
    get_profile,
    get_tool,
    load_config,
    save_default_config,
    visible_tools,
)


def test_default_config_has_profiles_and_tools():
    config = default_config()

    assert get_profile(config, "codex-builder").permission_profile == "balanced"
    assert get_tool(config, "docs.search").risk == "read"


def test_community_default_config():
    from werktools.hub.registry import community_default_config

    config = community_default_config()
    assert config.default_profile == "community-reader"
    assert {p.id for p in config.profiles} == {"community-reader", "community-builder", "community-admin"}
    # round-trip
    from werktools.hub.registry import load_config

    assert load_config(config.to_dict()).default_profile == "community-reader"


def test_load_config_from_json_file(tmp_path):
    path = tmp_path / "hub.json"
    path.write_text(json.dumps(default_config().to_dict()), encoding="utf-8")

    config = load_config(path)

    assert config.name == "werk-hub"


def test_save_default_config_creates_parent_directory(tmp_path):
    path = tmp_path / ".werktools" / "hub.json"

    config = save_default_config(path)

    assert path.exists()
    assert load_config(path).name == config.name


def test_visible_tools_are_profile_filtered():
    config = default_config()

    reviewer_tools = [tool.id for tool in visible_tools(config, "claude-reviewer")]
    builder_tools = [tool.id for tool in visible_tools(config, "codex-builder")]

    assert "docs.search" in reviewer_tools
    assert "github.create_pr" not in reviewer_tools
    assert "github.create_pr" in builder_tools


def test_hidden_tools_override_visible_tags():
    config = default_config()

    admin_tools = [tool.id for tool in visible_tools(config, "human-admin")]

    assert "shell.run" not in admin_tools


def test_unknown_profile_raises_key_error():
    config = default_config()

    try:
        get_profile(config, "ghost")
    except KeyError as exc:
        assert "ghost" in str(exc)
    else:
        raise AssertionError("expected KeyError")
