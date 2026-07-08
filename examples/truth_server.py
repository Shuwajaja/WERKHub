"""Real-tool MCP demo: Truth Auditor + Skill Library over FastMCP.

Unlike examples/echo_server.py (a synthetic demo), this wires two shipped
werktools tools as MCP tools and exercises them in-memory:

- truth_scan: local repo facts via werktools.tools.truth.scan_repo
- skills_match: profile-visible skill matching via werktools.tools.skills

Run: python examples/truth_server.py  (requires `pip install -e .[server]`)
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import tempfile

from fastmcp import Client

from werktools.envelope import ok
from werktools.server import make_server, register
from werktools.tools.skills import match_skills
from werktools.tools.truth import scan_repo

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _content_items(result):
    return getattr(result, "content", result)


def build_truth_server(skills_dir: pathlib.Path):
    server = make_server("werktools-truth-server", version="0.1")

    def truth_scan_handler(args: dict) -> dict:
        facts = scan_repo(args.get("repo") or str(_REPO_ROOT))
        return ok(
            "truth.scan",
            data={
                "root": facts.root,
                "project_name": facts.project_name,
                "markdown_files": len(facts.markdown_files),
                "python_files": len(facts.python_files),
                "test_files": len(facts.test_files),
            },
        )

    def skills_match_handler(args: dict) -> dict:
        cards = match_skills(skills_dir, args.get("task", ""), profile=args.get("profile") or "default")
        return ok(
            "skills.match",
            data={"matches": [{"skill": card.card_id, "title": card.title} for card in cards]},
        )

    register(
        server,
        "truth_scan",
        "Scan local repo facts without executing project code.",
        {"type": "object", "properties": {"repo": {"type": "string"}}, "required": []},
        truth_scan_handler,
    )
    register(
        server,
        "skills_match",
        "Match local skills to a task description.",
        {
            "type": "object",
            "properties": {"task": {"type": "string"}, "profile": {"type": "string"}},
            "required": ["task"],
        },
        skills_match_handler,
    )
    return server


async def run_demo() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        skills_dir = pathlib.Path(tmp) / "skills"
        skills_dir.mkdir()
        (skills_dir / "review-policy.md").write_text(
            "# Review Policy Modules\n\nTags: review, policy\nProfiles: *\nRisk: read\n\n"
            "Adversarially review policy modules.\n",
            encoding="utf-8",
        )

        server = build_truth_server(skills_dir)
        async with Client(server) as client:
            result = await client.call_tool("truth_scan", {})
            envelope = json.loads(_content_items(result)[0].text)
            print("[demo] truth_scan:", envelope["data"])
            assert envelope["ok"] is True
            assert envelope["data"]["project_name"] == "werktools"
            assert envelope["data"]["python_files"] > 0

            result = await client.call_tool("skills_match", {"task": "review the policy module"})
            envelope = json.loads(_content_items(result)[0].text)
            print("[demo] skills_match:", envelope["data"])
            assert envelope["ok"] is True
            assert envelope["data"]["matches"][0]["skill"] == "review-policy"

    print("[demo] all assertions passed")


if __name__ == "__main__":
    asyncio.run(run_demo())
