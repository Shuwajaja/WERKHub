import json

from werktools.__main__ import main as swarm_entry
from werktools.cli import main
from werktools.tools.swarm import (
    WorkPacket,
    collect_reports,
    load_plan,
    plan_from_goal,
    render_packet,
    review_reports,
    write_plan,
)


def test_work_packet_rejects_path_traversal_agent_id():
    raw = {
        "packet_id": "packet-1",
        "agent_id": "../evil",
        "title": "t",
        "repo": ".",
        "allowed_scope": "s",
        "instructions": "i",
        "done_criteria": "d",
        "stop_condition": "x",
    }

    try:
        WorkPacket.from_dict(raw)
    except ValueError as exc:
        assert "agent_id" in str(exc)
    else:
        raise AssertionError("expected traversal agent_id to be rejected")


def test_load_plan_reports_malformed_packet(tmp_path):
    plan_dir = tmp_path / "swarm"
    plan_dir.mkdir()
    (plan_dir / "swarm_plan.json").write_text(
        json.dumps({"goal_file": "g.md", "repo": ".", "packets": [{"packet_id": "p-1"}]}),
        encoding="utf-8",
    )

    try:
        load_plan(plan_dir)
    except ValueError as exc:
        assert "packet" in str(exc).lower()
    else:
        raise AssertionError("expected malformed packet to raise ValueError")


def test_plan_from_goal_creates_bounded_packets(tmp_path):
    goal = tmp_path / "goal.md"
    goal.write_text("# Goal\n\n## Docs\nWrite docs.\n\n## Tests\nRun tests.\n", encoding="utf-8")

    plan = plan_from_goal(goal, repo="C:/Workplace/werktools", agents=2)

    assert len(plan.packets) == 2
    assert plan.packets[0].agent_id == "agent-1"
    assert plan.packets[0].repo == "C:/Workplace/werktools"
    assert plan.packets[0].allowed_scope
    assert plan.packets[0].done_criteria
    assert plan.packets[0].stop_condition


def test_write_plan_and_render_packet_include_required_boundaries(tmp_path):
    goal = tmp_path / "goal.md"
    goal.write_text("# Goal\n\n## Docs\nWrite docs.\n", encoding="utf-8")
    plan = plan_from_goal(goal, repo="C:/Workplace/werktools", agents=1)
    out_dir = tmp_path / "swarm"

    write_plan(plan, out_dir)
    packet = render_packet(plan, "agent-1")

    assert (out_dir / "swarm_plan.json").exists()
    assert (out_dir / "agent-1.md").exists()
    assert "Allowed Scope" in packet
    assert "Stop Condition" in packet


def test_collect_reports_extracts_status_and_evidence(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "agent-1.md").write_text("Status: done\nEvidence: tests passed\n", encoding="utf-8")

    summary = collect_reports(reports)

    assert summary.report_count == 1
    assert summary.statuses == ("done",)
    assert summary.evidence == ("tests passed",)


def test_review_reports_requires_done_status_and_evidence(tmp_path):
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "agent-1.md").write_text("Status: blocked\n", encoding="utf-8")

    review = review_reports(reports)

    assert review.ok is False
    assert "missing evidence" in review.findings[0]


def test_swarm_cli_plan_packet_collect_and_review(tmp_path, capsys):
    goal = tmp_path / "goal.md"
    goal.write_text("# Goal\n\n## Docs\nWrite docs.\n", encoding="utf-8")
    out_dir = tmp_path / "swarm"

    assert (
        main(
            [
                "swarm",
                "plan",
                str(goal),
                "--out",
                str(out_dir),
                "--repo",
                "C:/Workplace/werktools",
                "--agents",
                "1",
            ]
        )
        == 0
    )
    assert main(["swarm", "packet", "agent-1", "--dir", str(out_dir)]) == 0
    assert "Allowed Scope" in capsys.readouterr().out

    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "agent-1.md").write_text("Status: done\nEvidence: docs updated\n", encoding="utf-8")
    assert main(["swarm", "collect", str(reports)]) == 0
    assert main(["swarm", "review", str(reports)]) == 0
    assert "OK: True" in capsys.readouterr().out


def test_werktools_swarm_entrypoint_routes_to_swarm_cli(tmp_path, capsys):
    reports = tmp_path / "reports"
    reports.mkdir()
    (reports / "agent-1.md").write_text("Status: done\nEvidence: tests passed\n", encoding="utf-8")

    assert swarm_entry(["review", str(reports)]) == 0
    assert "OK: True" in capsys.readouterr().out
