"""Tests for werktools.profile -- offline, dep-free."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from werktools.profile import Profile, load_profile, to_card


class TestProfileDataclass:
    def test_minimal_fields(self):
        p = Profile(id="scout", role="planner")
        assert p.id == "scout"
        assert p.role == "planner"
        assert p.skills == ()
        assert p.tools_visible == ()
        assert p.tools_allowed == ()
        assert p.capabilities == ()
        assert p.instructions is None
        assert p.budget is None

    def test_full_fields(self):
        p = Profile(
            id="builder",
            role="builder",
            skills=("code", "test"),
            tools_visible=("bash", "edit"),
            tools_allowed=("bash",),
            capabilities=("file_edit",),
            instructions="Build things.",
            budget={"max_cost_usd": 1.0},
        )
        assert p.skills == ("code", "test")
        assert p.tools_visible == ("bash", "edit")
        assert p.tools_allowed == ("bash",)
        assert p.capabilities == ("file_edit",)
        assert p.instructions == "Build things."
        assert p.budget == {"max_cost_usd": 1.0}

    def test_frozen(self):
        p = Profile(id="x", role="y")
        with pytest.raises((AttributeError, TypeError)):
            p.id = "z"  # type: ignore[misc]

    def test_sequences_are_tuples(self):
        p = Profile(id="a", role="b", skills=("s1",))
        assert isinstance(p.skills, tuple)
        assert isinstance(p.tools_visible, tuple)
        assert isinstance(p.tools_allowed, tuple)
        assert isinstance(p.capabilities, tuple)


class TestLoadProfileFromDict:
    def test_minimal_dict(self):
        p = load_profile({"id": "scout", "role": "planner"})
        assert p.id == "scout"
        assert p.role == "planner"
        assert p.skills == ()

    def test_full_dict(self):
        p = load_profile(
            {
                "id": "builder",
                "role": "builder",
                "skills": ["code", "test"],
                "tools_visible": ["bash"],
                "tools_allowed": ["bash"],
                "capabilities": ["file_edit"],
                "instructions": "Do it.",
                "budget": {"max_cost_usd": 2.0},
            }
        )
        assert p.skills == ("code", "test")
        assert p.tools_visible == ("bash",)
        assert p.tools_allowed == ("bash",)
        assert p.capabilities == ("file_edit",)
        assert p.instructions == "Do it."
        assert p.budget == {"max_cost_usd": 2.0}

    def test_extra_keys_ignored(self):
        p = load_profile({"id": "x", "role": "y", "unknown_field": "ignored"})
        assert p.id == "x"

    def test_missing_id_raises(self):
        with pytest.raises((KeyError, ValueError)):
            load_profile({"role": "planner"})

    def test_missing_role_raises(self):
        with pytest.raises((KeyError, ValueError)):
            load_profile({"id": "x"})


class TestLoadProfileFromJson:
    def test_json_file(self, tmp_path: Path):
        body = {"id": "archivist", "role": "doc_writer", "skills": ["docs"]}
        f = tmp_path / "body.json"
        f.write_text(json.dumps(body), encoding="utf-8")
        p = load_profile(f)
        assert p.id == "archivist"
        assert p.role == "doc_writer"
        assert p.skills == ("docs",)

    def test_json_string_path(self, tmp_path: Path):
        body = {"id": "archivist", "role": "doc_writer"}
        f = tmp_path / "body.json"
        f.write_text(json.dumps(body), encoding="utf-8")
        p = load_profile(str(f))
        assert p.id == "archivist"

    def test_missing_file_raises(self):
        with pytest.raises((FileNotFoundError, OSError, ValueError)):
            load_profile(Path("/nonexistent/body.json"))


class TestLoadProfileFromYaml:
    def test_yaml_without_pyyaml_raises_import_or_value(
        self, tmp_path: Path, monkeypatch
    ):
        import builtins

        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "yaml":
                raise ImportError("No module named 'yaml'")
            return real_import(name, *args, **kwargs)

        body_yaml = tmp_path / "body.yaml"
        body_yaml.write_text("id: archivist\nrole: doc_writer\n", encoding="utf-8")

        monkeypatch.setattr(builtins, "__import__", mock_import)
        with pytest.raises((ImportError, ValueError)):
            load_profile(body_yaml)

    def test_yaml_with_pyyaml_if_available(self, tmp_path: Path):
        pytest.importorskip("yaml")
        body_yaml = tmp_path / "body.yaml"
        body_yaml.write_text(
            textwrap.dedent(
                """\
                id: archivist
                role: doc_writer
                skills:
                  - docs
                  - summarize
                """
            ),
            encoding="utf-8",
        )
        p = load_profile(body_yaml)
        assert p.id == "archivist"
        assert "docs" in p.skills


class TestToCard:
    def _profile(self, **kwargs) -> Profile:
        base = {"id": "scout", "role": "planner"}
        base.update(kwargs)
        return load_profile(base)

    def test_card_schema_version(self):
        card = to_card(self._profile())
        assert card["schema"] == "werk.agent-card/1"

    def test_card_contains_id_and_role(self):
        card = to_card(self._profile())
        assert card["id"] == "scout"
        assert card["role"] == "planner"

    def test_card_lists_are_plain_lists(self):
        p = self._profile(
            skills=("a", "b"),
            tools_visible=("t1",),
            tools_allowed=("t1",),
            capabilities=("cap",),
        )
        card = to_card(p)
        assert isinstance(card["skills"], list)
        assert isinstance(card["tools_visible"], list)
        assert isinstance(card["tools_allowed"], list)
        assert isinstance(card["capabilities"], list)

    def test_card_instructions_and_budget(self):
        p = self._profile(instructions="Do it.", budget={"max_cost_usd": 1.0})
        card = to_card(p)
        assert card["instructions"] == "Do it."
        assert card["budget"] == {"max_cost_usd": 1.0}

    def test_card_is_json_serialisable(self):
        p = self._profile(skills=("x",), budget={"max_cost_usd": 0.5})
        card = to_card(p)
        dumped = json.dumps(card)
        loaded = json.loads(dumped)
        assert loaded["id"] == "scout"

    def test_card_no_none_instructions_when_absent(self):
        card = to_card(self._profile())
        json.dumps(card)

    def test_card_from_profile_round_trip(self):
        original = {
            "id": "builder",
            "role": "builder",
            "skills": ["code"],
            "capabilities": ["file_edit"],
            "instructions": "Build it.",
            "budget": {"max_cost_usd": 2.0},
        }
        p = load_profile(original)
        card = to_card(p)
        assert card["id"] == original["id"]
        assert card["role"] == original["role"]
        assert card["skills"] == original["skills"]
