"""docs/agent-workflow.md is produced by running the worked example, so it can only show what the tools really return."""

import asyncio
from pathlib import Path

from unicad.mcp.workflow_doc import render_workflow_markdown, steps

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "agent-workflow.md"


def test_workflow_doc_is_current():
    expected = asyncio.run(render_workflow_markdown())
    assert DOC.read_text(encoding="utf-8") == expected, "docs/agent-workflow.md is stale: run `uv run unicad-mcp --write-workflow docs/agent-workflow.md`"


def test_example_covers_the_workflow_and_shows_a_fix_loop():
    used = [s.tool for s in steps()]
    for tool in ("project_create", "site_import", "layout_place", "network_autobuild", "validate", "explain_finding", "network_update", "earthing_design", "lps_design", "cable_schedule", "bom", "project_snapshot", "project_diff"):
        assert tool in used, tool
    assert used.index("explain_finding") > used.index("validate") and used.index("network_update", used.index("explain_finding")) > used.index("explain_finding")
    text = DOC.read_text(encoding="utf-8")
    assert '"error": 1' in text and '"blocking": true' in text and '"blocking": false' in text
    assert str(ROOT) not in text and "/tmp/" not in text
