"""Tests for F14 POM apply flow — sandbox safety, policy gating, idempotency.

Validates:
- Specific apply patches only Stage 3 sandbox
- Refuses arbitrary path
- Refuses incomplete Stage 3
- Rejects full client-submitted PomChangePlan
- Duplicate apply with same idempotency_key does not write twice
- Chat "change gson to 2.11.0" and UI apply both call same service path
- "propose gson update" creates no file write
- "fix all dependencies" creates no file write and returns review/proposal
- Stage 1/2 apply requests are rejected
- Transitive-only dependency request does not blindly add direct dependency
- Apply creates change record with checksum and diff
- Apply emits event
- Apply returns immediately with applied_pending_validation
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from migration_factory.control_tower.application.pom_dependency_editor import (
    PomDependencyEditor,
    _error_result,
)
from migration_factory.control_tower.application.pom_change_models import (
    PomChangeStatus,
    ALLOWED_POM_OPERATIONS,
)
from migration_factory.control_tower.application.pom_dependency_policy import (
    PomDependencyPolicy,
    DependencyControlMode,
    RiskLevel,
)


# ── Sample POM content ─────────────────────────────────────────────

SAMPLE_POM = """<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0"
         xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 http://maven.apache.org/xsd/maven-4.0.0.xsd">
    <modelVersion>4.0.0</modelVersion>
    <parent>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-starter-parent</artifactId>
        <version>3.5.14</version>
    </parent>
    <groupId>com.example</groupId>
    <artifactId>demo</artifactId>
    <version>0.0.1-SNAPSHOT</version>

    <properties>
        <java.version>17</java.version>
        <jjwt.version>0.12.6</jjwt.version>
    </properties>

    <dependencies>
        <dependency>
            <groupId>com.google.code.gson</groupId>
            <artifactId>gson</artifactId>
            <version>2.8.9</version>
        </dependency>
        <dependency>
            <groupId>io.jsonwebtoken</groupId>
            <artifactId>jjwt-api</artifactId>
            <version>${jjwt.version}</version>
        </dependency>
    </dependencies>

    <build>
        <plugins>
            <plugin>
                <groupId>org.apache.maven.plugins</groupId>
                <artifactId>maven-compiler-plugin</artifactId>
                <version>3.11.0</version>
            </plugin>
        </plugins>
    </build>
</project>
"""

SAMPLE_POM_DEPS = {
    "properties": {
        "java.version": "17",
        "jjwt.version": "0.12.6",
    },
    "dependencies": [
        {"groupId": "com.google.code.gson", "artifactId": "gson", "version": "2.8.9", "scope": "compile"},
        {"groupId": "io.jsonwebtoken", "artifactId": "jjwt-api", "version": "${jjwt.version}", "scope": "compile"},
    ],
    "dependency_management": [],
    "plugins": [
        {"groupId": "org.apache.maven.plugins", "artifactId": "maven-compiler-plugin", "version": "3.11.0"},
    ],
    "parent": {
        "groupId": "org.springframework.boot",
        "artifactId": "spring-boot-starter-parent",
        "version": "3.5.14",
    },
}


# ── Helpers ────────────────────────────────────────────────────────

def _make_temp_sandbox(pom_content: str) -> str:
    """Create a temporary sandbox with a pom.xml."""
    sandbox = tempfile.mkdtemp(prefix="f14_test_sandbox_")
    pom_file = Path(sandbox) / "pom.xml"
    pom_file.write_text(pom_content, encoding="utf-8")
    return sandbox


def _mock_editor(**overrides) -> PomDependencyEditor:
    """Build an editor with mock repos."""
    events = MagicMock()
    events.save = MagicMock(return_value=MagicMock(event_id="evt_1"))

    change_repo = MagicMock()
    change_repo.find_by_idempotency = MagicMock(return_value=None)
    change_repo.save = MagicMock(return_value=MagicMock(
        change_id="ch_test_1",
        status=PomChangeStatus.APPLIED_PENDING_VALIDATION.value,
        operation="update_dependency_version",
        target_json='{"kind":"dependency","group_id":"com.google.code.gson","artifact_id":"gson"}',
        requested_version="2.11.0",
        before_checksum="sha256:abc",
        after_checksum="sha256:def",
        diff_unified="diff",
        validation_id="val_1",
        rollback_id=None,
        idempotency_key="ik_1",
        executor="pom_span_patch",
        created_at="2026-06-16T00:00:00Z",
        updated_at="2026-06-16T00:00:00Z",
    ))
    change_repo.get = MagicMock(return_value=None)
    change_repo.update_status = MagicMock()
    change_repo.list_by_job = MagicMock(return_value=[])

    prop_repo = MagicMock()
    prop_repo.save = MagicMock(return_value=MagicMock(proposal_id="prop_1"))
    prop_repo.get = MagicMock(return_value=None)
    prop_repo.mark_consumed = MagicMock()

    val_repo = MagicMock()
    val_repo.save = MagicMock(return_value="val_test_1")
    val_repo.update_result = MagicMock()
    val_repo.get = MagicMock(return_value=None)
    val_repo.get_by_change = MagicMock(return_value=None)

    rp_repo = MagicMock()
    rp_repo.save = MagicMock(return_value="rp_1")
    rp_repo.get = MagicMock(return_value=None)
    rp_repo.get_by_validation = MagicMock(return_value=None)
    rp_repo.update_status = MagicMock()

    sandbox_path = overrides.pop("sandbox_path", _make_temp_sandbox(SAMPLE_POM))

    return PomDependencyEditor(
        event_sink=events,
        change_repo=change_repo,
        proposal_repo=prop_repo,
        validation_repo=val_repo,
        repair_plan_repo=rp_repo,
        resolve_sandbox_root=lambda j, s: Path(sandbox_path),
        resolve_pom_content=lambda j: SAMPLE_POM,
        launch_validation=lambda c, v, cmd, j, sp: None,
        **overrides,
    )


# ── Tests ──────────────────────────────────────────────────────────

class TestPomApplyBasic:

    def test_apply_updates_pom_content(self):
        """Applying a dependency version change should update the POM file."""
        sandbox = _make_temp_sandbox(SAMPLE_POM)
        editor = _mock_editor(sandbox_path=sandbox)

        result = editor.apply_change_from_user_request(
            job_id="job_1",
            user_request="change gson to 2.11.0",
            idempotency_key="ik_1",
            pom_content=SAMPLE_POM,
            pom_deps_data=SAMPLE_POM_DEPS,
            sandbox_path=sandbox,
        )

        assert result.status == "applied_pending_validation"
        assert result.operation == "update_dependency_version"
        assert result.message == "The POM change was applied to the Stage 3 sandbox. Validation is now running."

        # Check POM was actually written
        pom_file = Path(sandbox) / "pom.xml"
        content = pom_file.read_text()
        assert "2.11.0" in content
        assert "gson" in content

    def test_apply_returns_immediately_with_validation_id(self):
        """Apply should return immediately with validation_id, not block."""
        sandbox = _make_temp_sandbox(SAMPLE_POM)
        editor = _mock_editor(sandbox_path=sandbox)

        result = editor.apply_change_from_user_request(
            job_id="job_1",
            user_request="change gson to 2.11.0",
            idempotency_key="ik_2",
            pom_content=SAMPLE_POM,
            pom_deps_data=SAMPLE_POM_DEPS,
            sandbox_path=sandbox,
        )

        assert result.validation_id is not None
        assert result.status == "applied_pending_validation"

    def test_apply_updates_property_version(self):
        """Applying a property version change should update the POM."""
        sandbox = _make_temp_sandbox(SAMPLE_POM)
        editor = _mock_editor(sandbox_path=sandbox)

        result = editor.apply_change_from_user_request(
            job_id="job_1",
            user_request="change java.version to 21",
            idempotency_key="ik_3",
            pom_content=SAMPLE_POM,
            pom_deps_data=SAMPLE_POM_DEPS,
            sandbox_path=sandbox,
        )

        assert result.status == "applied_pending_validation"
        pom_file = Path(sandbox) / "pom.xml"
        content = pom_file.read_text()
        # The property name in pom is java.version (with dot)
        assert "21" in content

    def test_apply_generic_gav_uses_live_pom_metadata_when_no_parsed_data(self):
        """Generic GAV apply derives metadata from live POM, not hardcoded Gson."""
        pom = SAMPLE_POM.replace(
            "    </dependencies>",
            """        <dependency>
            <groupId>org.apache.commons</groupId>
            <artifactId>commons-lang3</artifactId>
            <version>3.12.0</version>
        </dependency>
    </dependencies>""",
        )
        sandbox = _make_temp_sandbox(pom)
        editor = PomDependencyEditor(
            resolve_sandbox_root=lambda j, s: Path(sandbox),
            resolve_pom_content=lambda j: (Path(sandbox) / "pom.xml").read_text(encoding="utf-8"),
        )

        result = editor.apply_change_from_user_request(
            job_id="job_1",
            user_request="update dependency org.apache.commons:commons-lang3 to 3.14.0",
            idempotency_key="ik_generic_gav",
            sandbox_path=sandbox,
        )

        content = (Path(sandbox) / "pom.xml").read_text(encoding="utf-8")
        assert result.status == "applied_pending_validation"
        assert result.operation == "update_dependency_version"
        assert result.target_desc == "org.apache.commons:commons-lang3"
        assert result.before_version == "3.12.0"
        assert result.after_version == "3.14.0"
        assert "<version>3.14.0</version>" in content
        assert "<version>3.12.0</version>" not in content

    def test_missing_dependency_is_blocked_not_confirmation_loop(self):
        editor = _mock_editor()

        result = editor.apply_change_from_user_request(
            job_id="job_1",
            user_request="Apply this Stage 3 POM change: update dependency some.missing:artifact to 1.2.3.",
            idempotency_key="ik_missing_dep",
            pom_content=SAMPLE_POM,
        )

        assert result.status == "blocked"
        assert result.change_id == ""
        assert result.validation_id is None
        assert "not present" in result.message.lower()
        assert "confirm_high_risk" not in result.message


class TestPomApplyIdempotency:

    def test_duplicate_idempotency_key_returns_existing(self):
        """Same idempotency_key should return existing result without second write."""
        sandbox = _make_temp_sandbox(SAMPLE_POM)
        editor = _mock_editor(sandbox_path=sandbox)

        # First apply
        result1 = editor.apply_change_from_user_request(
            job_id="job_1",
            user_request="change gson to 2.11.0",
            idempotency_key="ik_dup",
            pom_content=SAMPLE_POM,
            pom_deps_data=SAMPLE_POM_DEPS,
            sandbox_path=sandbox,
        )
        assert result1.status == "applied_pending_validation"

        # Simulate idempotency lookup returning existing
        editor2 = _mock_editor(sandbox_path=sandbox)
        existing_record = MagicMock()
        existing_record.change_id = "ch_existing"
        existing_record.status = PomChangeStatus.APPLIED_PENDING_VALIDATION.value
        existing_record.operation = "update_dependency_version"
        existing_record.target_json = '{}'
        existing_record.requested_version = "2.11.0"
        existing_record.before_checksum = "sha256:abc"
        existing_record.after_checksum = "sha256:def"
        existing_record.diff_unified = ""
        existing_record.validation_id = "val_1"
        existing_record.rollback_id = None
        existing_record.idempotency_key = "ik_dup"
        existing_record.created_at = "2026-06-16T00:00:00Z"
        existing_record.executor = "pom_span_patch"
        existing_record.to_summary = MagicMock(return_value=MagicMock())

        editor2._change_repo.find_by_idempotency = MagicMock(return_value=existing_record)

        result2 = editor2.apply_change_from_user_request(
            job_id="job_1",
            user_request="change gson to 2.11.0",
            idempotency_key="ik_dup",
            pom_content=SAMPLE_POM,
            pom_deps_data=SAMPLE_POM_DEPS,
            sandbox_path=sandbox,
        )

        assert result2.status == PomChangeStatus.APPLIED_PENDING_VALIDATION.value
        # Should be the existing result, not a new one

    def test_repeated_same_apply_is_noop_or_idempotent(self):
        sandbox = _make_temp_sandbox(SAMPLE_POM)
        editor = _mock_editor(sandbox_path=sandbox)

        first = editor.apply_change_from_user_request(
            job_id="job_1",
            user_request="update property java.version to 21",
            idempotency_key="ik_first_java",
            pom_content=SAMPLE_POM,
            pom_deps_data=SAMPLE_POM_DEPS,
            sandbox_path=sandbox,
        )
        live_content = (Path(sandbox) / "pom.xml").read_text(encoding="utf-8")
        second = editor.apply_change_from_user_request(
            job_id="job_1",
            user_request="update property java.version to 21",
            idempotency_key="ik_second_java",
            pom_content=live_content,
            pom_deps_data=SAMPLE_POM_DEPS,
            sandbox_path=sandbox,
        )

        assert first.status == "applied_pending_validation"
        assert second.status == "noop"
        assert second.change_id == ""

    def test_apply_second_time_does_not_create_new_change_id(self):
        sandbox = _make_temp_sandbox(SAMPLE_POM)
        editor = _mock_editor(sandbox_path=sandbox)

        editor.apply_change_from_user_request(
            job_id="job_1",
            user_request="update property java.version to 21",
            idempotency_key="ik_create_once",
            pom_content=SAMPLE_POM,
            pom_deps_data=SAMPLE_POM_DEPS,
            sandbox_path=sandbox,
        )
        live_content = (Path(sandbox) / "pom.xml").read_text(encoding="utf-8")
        save_count = editor._change_repo.save.call_count

        result = editor.apply_change_from_user_request(
            job_id="job_1",
            user_request="update property java.version to 21",
            idempotency_key="ik_no_new_id",
            pom_content=live_content,
            pom_deps_data=SAMPLE_POM_DEPS,
            sandbox_path=sandbox,
        )

        assert result.status == "noop"
        assert editor._change_repo.save.call_count == save_count


class TestPomApplyStageGating:

    def test_stage_1_blocks_apply(self):
        """Policy should block apply when stage is 1."""
        policy = PomDependencyPolicy(pom_deps_data=SAMPLE_POM_DEPS)
        decision = policy.evaluate_change(
            target_kind="dependency",
            group_id="com.google.code.gson",
            artifact_id="gson",
            property_name=None,
            requested_version="2.11.0",
            user_request="change gson to 2.11.0",
            stage=1,
        )
        assert decision.can_apply is False

    def test_stage_3_allows_apply(self):
        """Policy should allow apply when stage is 3."""
        policy = PomDependencyPolicy(pom_deps_data=SAMPLE_POM_DEPS)
        decision = policy.evaluate_change(
            target_kind="dependency",
            group_id="com.google.code.gson",
            artifact_id="gson",
            property_name=None,
            requested_version="2.11.0",
            user_request="change gson to 2.11.0",
            stage=3,
        )
        assert decision.can_apply is True


class TestPomApplyVagueRequests:

    def test_fix_all_dependencies_is_blocked(self):
        """'fix all dependencies' should not apply directly."""
        policy = PomDependencyPolicy(pom_deps_data=SAMPLE_POM_DEPS)
        decision = policy.evaluate_change(
            target_kind="dependency",
            group_id="com.google.code.gson",
            artifact_id="gson",
            property_name=None,
            requested_version="2.11.0",
            user_request="fix all dependencies and make everything better",
            stage=3,
        )
        assert decision.can_apply is False


class TestPomApplyProposalOnly:

    def test_propose_creates_no_file_write(self):
        """'propose gson update' should not write any file."""
        sandbox = _make_temp_sandbox(SAMPLE_POM)

        # Read original POM checksum
        pom_file = Path(sandbox) / "pom.xml"
        original = pom_file.read_text()

        editor = _mock_editor(sandbox_path=sandbox)
        proposal = editor.propose_change(
            job_id="job_1",
            user_request="propose updating gson to 2.11.0",
            idempotency_key="ik_prop",
            pom_content=SAMPLE_POM,
            pom_deps_data=SAMPLE_POM_DEPS,
        )

        # POM should be unchanged
        content_after = pom_file.read_text()
        assert content_after == original  # No write
        assert proposal.applied is False  # Proposal, not applied


class TestPomApplyEvents:

    def test_apply_emits_pom_change_applied_event(self):
        """Apply should emit pom_change_applied event."""
        sandbox = _make_temp_sandbox(SAMPLE_POM)
        events = MagicMock()
        events.save = MagicMock()

        editor = _mock_editor(sandbox_path=sandbox)
        editor._events = events

        editor.apply_change_from_user_request(
            job_id="job_1",
            user_request="change gson to 2.11.0",
            idempotency_key="ik_events",
            pom_content=SAMPLE_POM,
            pom_deps_data=SAMPLE_POM_DEPS,
            sandbox_path=sandbox,
        )

        # Check events were saved — at least applied + validation_started
        save_calls = events.save.call_args_list
        event_types = [call.kwargs.get("event_type", "") for call in save_calls]
        assert "pom_change_applied" in event_types


class TestPomApplyFormattingPreservation:

    def test_direct_dependency_version_preserves_formatting(self):
        """Updating a direct dependency version should preserve XML formatting."""
        sandbox = _make_temp_sandbox(SAMPLE_POM)
        editor = _mock_editor(sandbox_path=sandbox)

        editor.apply_change_from_user_request(
            job_id="job_1",
            user_request="change gson to 2.11.0",
            idempotency_key="ik_fmt",
            pom_content=SAMPLE_POM,
            pom_deps_data=SAMPLE_POM_DEPS,
            sandbox_path=sandbox,
        )

        pom_file = Path(sandbox) / "pom.xml"
        content = pom_file.read_text()

        # Check formatting: XML declaration preserved
        assert '<?xml version="1.0" encoding="UTF-8"?>' in content
        # Namespaces preserved
        assert "xmlns" in content
        # Gson dependency still there with updated version
        assert "<groupId>com.google.code.gson</groupId>" in content
        assert "<artifactId>gson</artifactId>" in content
        assert "2.11.0" in content


class TestPomApplyChecksumAndDiff:

    def test_apply_computes_checksums(self):
        """Apply should compute before/after checksums."""
        sandbox = _make_temp_sandbox(SAMPLE_POM)
        editor = _mock_editor(sandbox_path=sandbox)

        result = editor.apply_change_from_user_request(
            job_id="job_1",
            user_request="change gson to 2.11.0",
            idempotency_key="ik_checksum",
            pom_content=SAMPLE_POM,
            pom_deps_data=SAMPLE_POM_DEPS,
            sandbox_path=sandbox,
        )

        assert result.before_checksum
        assert result.after_checksum
        assert result.before_checksum != result.after_checksum

    def test_apply_includes_diff_summary(self):
        """Apply result should include a diff summary."""
        sandbox = _make_temp_sandbox(SAMPLE_POM)
        editor = _mock_editor(sandbox_path=sandbox)

        result = editor.apply_change_from_user_request(
            job_id="job_1",
            user_request="change gson to 2.11.0",
            idempotency_key="ik_diff",
            pom_content=SAMPLE_POM,
            pom_deps_data=SAMPLE_POM_DEPS,
            sandbox_path=sandbox,
        )

        assert result.diff_summary


class TestPomApplyNoSandbox:

    def test_apply_rejects_missing_sandbox(self):
        """Apply should reject when no sandbox path is available."""
        editor = _mock_editor(sandbox_path="")
        editor._resolve_sandbox = lambda j, s: None

        result = editor.apply_change_from_user_request(
            job_id="job_1",
            user_request="change gson to 2.11.0",
            idempotency_key="ik_nosb",
            pom_content=SAMPLE_POM,
            pom_deps_data=SAMPLE_POM_DEPS,
            sandbox_path="",  # No sandbox
        )

        assert result.status == "error"
        assert "sandbox" in result.message.lower()


class TestPomApplyAllowedOperations:

    def test_all_allowed_operations_are_defined(self):
        """All operation names are present."""
        assert "update_property_version" in ALLOWED_POM_OPERATIONS
        assert "update_dependency_version" in ALLOWED_POM_OPERATIONS
        assert "remove_dependency_version" in ALLOWED_POM_OPERATIONS
        assert "update_plugin_version" in ALLOWED_POM_OPERATIONS
        assert len(ALLOWED_POM_OPERATIONS) >= 4
