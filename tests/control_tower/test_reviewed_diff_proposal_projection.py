"""Focused tests for read-only reviewed diff proposal projection."""

from __future__ import annotations

import hashlib
from pathlib import Path

from migration_factory.control_tower.application.v2_repair_projection import (
    READ_ONLY_REPAIR_ACTIONS,
    build_reviewed_diff_proposal_projection,
    reviewed_diff_proposal_to_safe_dict,
)


def _write_diff(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_projection_uses_reviewed_final_diff_not_draft_diff(tmp_path: Path) -> None:
    reviewed_diff = _write_diff(
        tmp_path / "final_reviewed_repair.diff",
        "\n".join(
            [
                "diff --git a/src/App.java b/src/App.java",
                "--- a/src/App.java",
                "+++ b/src/App.java",
                "@@ -1,3 +1,3 @@",
                " class App {",
                "-    String mode = \"draft\";",
                "+    String mode = \"reviewed\";",
                " }",
            ]
        )
        + "\n",
    )
    draft_diff = _write_diff(
        tmp_path / "main_proposed.diff",
        "\n".join(
            [
                "diff --git a/src/App.java b/src/App.java",
                "--- a/src/App.java",
                "+++ b/src/App.java",
                "@@ -1,3 +1,3 @@",
                " class App {",
                "-    String mode = \"draft\";",
                "+    String mode = \"draft-but-not-final\";",
                " }",
            ]
        )
        + "\n",
    )

    projection = build_reviewed_diff_proposal_projection(
        proposal_id="proposal-42",
        status="user_review_required",
        failure_summary="Build failed in App.java",
        review_chain={
            "job_id": "job-42",
            "command_id": "cmd-42",
            "gate_id": "gate-42",
            "route_step_index": 2,
            "stage_index": 3,
            "attempt_number": 1,
            "revision_number": 2,
            "final_diff_ref": str(reviewed_diff),
            "primary_output_ref": str(draft_diff),
            "proposed_diff_checksum": "draft-checksum",
            "reviewer_output_checksum": "reviewer-output-checksum",
            "risk": "MEDIUM",
            "sandbox_path": "/tmp/sandbox/should-not-leak",
            "argv": ["mvn", "test"],
            "env": {"AZURE_OPENAI_API_KEY": "secret"},
            "raw_command": "rm -rf /",
            "target_path": "src/App.java",
            "patch_content": "draft patch text",
            "reviewer_notes": ["Looks good in /tmp/sandbox and AZURE_OPENAI_API_KEY=secret"],
            "missing_evidence": ["need more logs"],
            "unsafe_assumptions": ["assumes /tmp/sandbox is real"],
        },
        reviewer_verdict={
            "reviewer_verdict_id": "verdict-42",
            "decision": "accept",
            "reasoning": "Looks good in /tmp/sandbox and AZURE_OPENAI_API_KEY=secret",
            "missing_evidence": ["need more logs"],
            "unsafe_assumptions": ["assumes /tmp/sandbox is real"],
            "model_invocation_id": "inv-42",
            "output_checksum": "verdict-checksum",
        },
        required_validation=("build", "test"),
    )

    safe = reviewed_diff_proposal_to_safe_dict(projection)

    assert projection.diff_ref == "final_reviewed_repair.diff"
    assert projection.diff_checksum == hashlib.sha256(reviewed_diff.read_bytes()).hexdigest()
    assert "reviewed" in projection.safe_diff_preview.files[0].hunks[0].lines[2].text
    assert "draft-but-not-final" not in projection.safe_diff_preview.files[0].hunks[0].lines[2].text
    assert safe["diff_ref"] == "final_reviewed_repair.diff"
    assert safe["diff_checksum"] == projection.diff_checksum
    assert safe["files_changed"] == [
        {
            "path": "src/App.java",
            "change_type": "modified",
            "additions": 1,
            "deletions": 1,
        }
    ]
    assert safe["reviewer_verdict"]["decision"] == "accept"
    assert safe["reviewer_verdict"]["reviewer_verdict_id"] == "verdict-42"
    assert safe["reviewer_verdict"]["output_checksum"] == "verdict-checksum"
    assert "/tmp/sandbox" not in (safe["reviewer_verdict"]["reasoning"] or "")
    assert "AZURE_OPENAI_API_KEY" not in (safe["reviewer_verdict"]["reasoning"] or "")


def test_projection_is_read_only_and_conservative(tmp_path: Path) -> None:
    reviewed_diff = _write_diff(
        tmp_path / "final_reviewed_repair.diff",
        "diff --git a/pom.xml b/pom.xml\n--- a/pom.xml\n+++ b/pom.xml\n@@ -1,1 +1,1 @@\n-old\n+new\n",
    )

    projection = build_reviewed_diff_proposal_projection(
        proposal_id="proposal-99",
        status="user_review_required",
        failure_summary="Need user review",
        review_chain={
            "job_id": "job-99",
            "command_id": "cmd-99",
            "gate_id": "gate-99",
            "final_diff_ref": str(reviewed_diff),
            "sandbox_path": "/tmp/sandbox/job-99",
            "argv": ["mvn", "test"],
            "env": {"AZURE_OPENAI_API_KEY": "secret"},
            "raw_command": "mvn test",
            "target_path": "pom.xml",
            "patch_content": "raw patch text",
            "model_invocation_id": "inv-99",
            "reviewer_output_checksum": "checksum-99",
        },
        reviewer_verdict={
            "decision": "reject",
            "reasoning": "reject for safety",
            "model_invocation_id": "inv-99",
            "output_checksum": "checksum-99",
        },
    )

    safe = reviewed_diff_proposal_to_safe_dict(projection)

    assert projection.allowed_actions == READ_ONLY_REPAIR_ACTIONS
    assert safe["allowed_actions"] == list(READ_ONLY_REPAIR_ACTIONS)
    assert "target_path" not in safe
    assert "patch_content" not in safe
    assert "sandbox_path" not in safe
    assert "argv" not in safe
    assert "env" not in safe
    assert "raw_command" not in safe
    assert "command" not in safe
    assert all(key not in safe for key in ("approve_sandbox_apply", "apply_diff", "revise_diff"))
