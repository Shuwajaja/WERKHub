from werktools.cli import main
from werktools.tools.skills import (
    export_skills,
    list_skills,
    match_skills,
    show_skill,
)


def _skills_dir(tmp_path):
    skills = tmp_path / "skills"
    skills.mkdir()
    (skills / "review-policy.md").write_text(
        "# Review Policy Modules\n\nTags: review, policy\nProfiles: *\nRisk: read\n\n"
        "Adversarially review policy modules for fail-closed behavior.\n",
        encoding="utf-8",
    )
    (skills / "admin-deploy.md").write_text(
        "# Deploy Helper\n\nTags: deploy\nProfiles: admin\nRisk: write\n\nGuides deployments.\n",
        encoding="utf-8",
    )
    return skills


def test_list_skills_returns_cards_with_tracked_source(tmp_path):
    skills = _skills_dir(tmp_path)

    cards = list_skills(skills)

    assert [card.card_id for card in cards] == ["admin-deploy", "review-policy"]
    assert all(card.kind == "skill" for card in cards)
    assert all(card.source for card in cards)


def test_show_skill_enforces_profile_visibility(tmp_path):
    skills = _skills_dir(tmp_path)

    card = show_skill(skills, "review-policy", profile="default")
    assert card.title == "Review Policy Modules"

    try:
        show_skill(skills, "admin-deploy", profile="default")
    except PermissionError as exc:
        assert "not visible" in str(exc)
    else:
        raise AssertionError("expected hidden skill to be denied")


def test_match_skills_only_returns_visible_skills(tmp_path):
    skills = _skills_dir(tmp_path)

    matched = match_skills(skills, "review the policy module", profile="default")

    assert [card.card_id for card in matched] == ["review-policy"]


def test_export_skills_is_explicit_and_profile_scoped(tmp_path):
    skills = _skills_dir(tmp_path)
    out = tmp_path / "bundle" / "skills.json"

    exported = export_skills(skills, out, profile="default")

    assert out.exists()
    assert [item["card_id"] for item in exported] == ["review-policy"]


def test_skills_cli_list_show_match_export(tmp_path, capsys):
    skills = _skills_dir(tmp_path)
    out = tmp_path / "bundle.json"

    assert main(["skills", "list", "--dir", str(skills)]) == 0
    assert "review-policy" in capsys.readouterr().out

    assert main(["skills", "show", "review-policy", "--dir", str(skills)]) == 0
    assert "Review Policy Modules" in capsys.readouterr().out

    assert main(["skills", "match", "review the policy module", "--dir", str(skills)]) == 0
    assert "review-policy" in capsys.readouterr().out

    assert main(["skills", "export", "--dir", str(skills), "--out", str(out)]) == 0
    assert out.exists()


def test_skills_cli_list_is_profile_filtered(tmp_path, capsys):
    skills = _skills_dir(tmp_path)

    assert main(["skills", "list", "--dir", str(skills)]) == 0
    default_out = capsys.readouterr().out
    assert "review-policy" in default_out
    assert "admin-deploy" not in default_out

    assert main(["skills", "list", "--dir", str(skills), "--all"]) == 0
    all_out = capsys.readouterr().out
    assert "admin-deploy" in all_out


def test_skills_cli_show_denied_returns_nonzero(tmp_path, capsys):
    skills = _skills_dir(tmp_path)

    code = main(["skills", "show", "admin-deploy", "--dir", str(skills), "--profile", "default"])

    assert code == 1
    assert "not visible" in capsys.readouterr().err.lower()
