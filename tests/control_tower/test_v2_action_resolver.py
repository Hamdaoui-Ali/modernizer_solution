"""Tests for V2 sandbox action resolver (F06).

Tests that the resolver:
1. Binds to the latest failed command for a job/stage
2. Resolves sandbox from command result_json
3. Rejects legacy source paths in sandbox
4. Rejects stale proposals (command_id mismatch)
5. Computes binding checksum
6. Returns BindingResult with all required fields (no execution fields)
7. Fails closed on missing job, commands, or sandbox
8. Fails closed on unknown proposal
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest

from migration_factory.control_tower.application.v2_action_resolver import (
    V2AssistantActionResolver,
    ActionBindingRequest,
    ActionResolverProtocol,
    SandboxBinding,
    BindingResult,
    FailedCommandInfo,
    is_legacy_source_path,
)


# ── Test fixtures ──────────────────────────────────────────────────


@dataclass
class FakeCommand:
    """Minimal command stub for testing the resolver."""
    command_id: str
    job_id: str
    stage_index: int
    status: str
    result_json: str | None = None
    created_at: str = "2026-06-15T12:00:00"


@dataclass
class FakeJob:
    job_id: str
    status: str = "active"


@dataclass
class FakeProposal:
    proposal_id: str
    command_id: str
    status: str = "draft"
    approval_checksum: str | None = "abc123"


def make_fake_resolver(
    commands: list | None = None,
    job: FakeJob | None = None,
    proposals: dict[str, FakeProposal] | None = None,
) -> ActionResolverProtocol:
    """Build a resolver protocol with fake data."""
    _commands = commands or []
    _job = job
    if _job is None and _commands:
        _job = FakeJob(job_id=_commands[0].job_id, status="active")
    _proposals = proposals or {}

    def _get_job(job_id: str) -> FakeJob | None:
        if _job and _job.job_id == job_id:
            return _job
        return None

    def _list_commands(job_id: str) -> tuple[FakeCommand, ...]:
        return tuple(c for c in _commands if c.job_id == job_id)

    def _list_by_stage(job_id: str, stage_index: int) -> tuple[FakeCommand, ...]:
        return tuple(
            c for c in _commands
            if c.job_id == job_id and c.stage_index == stage_index
        )

    def _get_proposal(proposal_id: str) -> FakeProposal | None:
        return _proposals.get(proposal_id)

    return ActionResolverProtocol(
        get_job=_get_job,
        list_commands=_list_commands,
        list_commands_by_stage=_list_by_stage,
        list_events=lambda _: (),
        get_proposal=_get_proposal,
    )


# ── Basic binding tests ────────────────────────────────────────────


class TestBasicBinding:

    def test_binds_latest_failed_command(self) -> None:
        """Resolver binds to the latest failed command."""
        commands = [
            FakeCommand("cmd-1", "job-1", 1, "completed", created_at="2026-01-01T12:00:00"),
            FakeCommand("cmd-2", "job-1", 1, "failed", result_json=json.dumps({"sandbox_path": "/sandbox/stage-1"}), created_at="2026-01-01T13:00:00"),
        ]
        resolver = V2AssistantActionResolver(
            resolver=make_fake_resolver(commands=commands)
        )
        result = resolver.resolve(
            ActionBindingRequest(job_id="job-1", action_type="repair_apply", stage_index=1)
        )
        assert result.binding.command_id == "cmd-2"
        assert result.binding.stage_index == 1
        assert result.failed_command.status == "failed"
        assert result.binding.sandbox_path == "/sandbox/stage-1"

    def test_resolves_sandbox_from_result_json(self) -> None:
        """Sandbox path is extracted from the failed command's result_json."""
        commands = [
            FakeCommand(
                "cmd-fail", "job-1", 2, "failed",
                result_json=json.dumps({"sandbox_path": "/sandbox/stage-2/output"}),
            ),
        ]
        resolver = V2AssistantActionResolver(
            resolver=make_fake_resolver(commands=commands)
        )
        result = resolver.resolve(
            ActionBindingRequest(job_id="job-1", action_type="repair_apply", stage_index=2)
        )
        assert result.binding.sandbox_path == "/sandbox/stage-2/output"

    def test_returns_binding_with_checksum(self) -> None:
        """Binding includes a checksum over resolved state."""
        commands = [
            FakeCommand(
                "cmd-1", "job-1", 1, "failed",
                result_json=json.dumps({"sandbox_path": "/sandbox/s1"}),
            ),
        ]
        resolver = V2AssistantActionResolver(
            resolver=make_fake_resolver(commands=commands)
        )
        result = resolver.resolve(
            ActionBindingRequest(job_id="job-1", action_type="repair_apply")
        )
        assert result.binding.binding_checksum
        assert len(result.binding.binding_checksum) > 0
        assert result.binding.job_id == "job-1"
        assert result.binding.stage_index == 1

    def test_binding_includes_sandbox_checksum(self) -> None:
        """Sandbox checksum is computed from the resolved path."""
        commands = [
            FakeCommand(
                "cmd-1", "job-1", 1, "failed",
                result_json=json.dumps({"sandbox_path": "/sandbox/s1"}),
            ),
        ]
        resolver = V2AssistantActionResolver(
            resolver=make_fake_resolver(commands=commands)
        )
        result = resolver.resolve(
            ActionBindingRequest(job_id="job-1", action_type="repair_apply")
        )
        assert result.binding.sandbook_checksum


# ── Fail-closed tests ──────────────────────────────────────────────


class TestFailClosed:

    def test_rejects_no_commands(self) -> None:
        """No commands for a job raises ValueError."""
        # Job exists but has no commands
        job = FakeJob(job_id="job-empty", status="active")
        resolver = V2AssistantActionResolver(
            resolver=make_fake_resolver(commands=[], job=job)
        )
        with pytest.raises(ValueError, match="No commands found"):
            resolver.resolve(
                ActionBindingRequest(job_id="job-empty", action_type="repair_apply")
            )

    def test_rejects_no_job(self) -> None:
        """Non-existent job raises ValueError."""
        # Resolver with no job function returns None for any job_id
        resolver = V2AssistantActionResolver(
            resolver=ActionResolverProtocol()
        )
        with pytest.raises(ValueError, match="not found"):
            resolver.resolve(
                ActionBindingRequest(job_id="nonexistent", action_type="repair_apply")
            )

    def test_rejects_inactive_job(self) -> None:
        """Completed/cancelled job raises ValueError."""
        commands = [
            FakeCommand(
                "cmd-1", "job-done", 1, "failed",
                result_json=json.dumps({"sandbox_path": "/sandbox/s1"}),
            ),
        ]
        job = FakeJob(job_id="job-done", status="completed")
        resolver = V2AssistantActionResolver(
            resolver=make_fake_resolver(commands=commands, job=job)
        )
        with pytest.raises(ValueError, match="not active"):
            resolver.resolve(
                ActionBindingRequest(job_id="job-done", action_type="repair_apply")
            )

    def test_rejects_no_failed_command(self) -> None:
        """No failed command raises ValueError."""
        commands = [
            FakeCommand("cmd-1", "job-1", 1, "completed"),
            FakeCommand("cmd-2", "job-1", 1, "completed"),
        ]
        resolver = V2AssistantActionResolver(
            resolver=make_fake_resolver(commands=commands)
        )
        with pytest.raises(ValueError, match="No failed command"):
            resolver.resolve(
                ActionBindingRequest(job_id="job-1", action_type="repair_apply")
            )

    def test_rejects_no_sandbox_path(self) -> None:
        """Command with no sandbox raises ValueError."""
        commands = [
            FakeCommand("cmd-1", "job-1", 1, "failed", result_json="{}"),
        ]
        resolver = V2AssistantActionResolver(
            resolver=make_fake_resolver(commands=commands)
        )
        with pytest.raises(ValueError, match="Cannot resolve sandbox"):
            resolver.resolve(
                ActionBindingRequest(job_id="job-1", action_type="repair_apply")
            )

    def test_rejects_legacy_source_sandbox(self) -> None:
        """Sandbox path under legacy source is rejected."""
        commands = [
            FakeCommand(
                "cmd-1", "job-1", 1, "failed",
                result_json=json.dumps({"sandbox_path": "/legacy/source/pom.xml"}),
            ),
        ]
        resolver = V2AssistantActionResolver(
            resolver=make_fake_resolver(commands=commands)
        )
        with pytest.raises(ValueError, match="legacy source"):
            resolver.resolve(
                ActionBindingRequest(job_id="job-1", action_type="repair_apply")
            )

    def test_rejects_unknown_proposal(self) -> None:
        """Non-existent proposal raises ValueError."""
        commands = [
            FakeCommand(
                "cmd-1", "job-1", 1, "failed",
                result_json=json.dumps({"sandbox_path": "/sandbox/s1"}),
            ),
        ]
        resolver = V2AssistantActionResolver(
            resolver=make_fake_resolver(commands=commands)
        )
        with pytest.raises(ValueError, match="not found"):
            resolver.resolve(
                ActionBindingRequest(
                    job_id="job-1", action_type="repair_apply",
                    proposal_id="nonexistent-proposal",
                )
            )

    def test_rejects_stale_proposal_command_mismatch(self) -> None:
        """Proposal bound to a different command than the latest failed is rejected."""
        commands = [
            FakeCommand(
                "cmd-fail", "job-1", 1, "failed",
                result_json=json.dumps({"sandbox_path": "/sandbox/s1"}),
            ),
        ]
        proposals = {
            "prop-1": FakeProposal("prop-1", command_id="cmd-old"),
        }
        resolver = V2AssistantActionResolver(
            resolver=make_fake_resolver(commands=commands, proposals=proposals)
        )
        with pytest.raises(ValueError, match="bound to command"):
            resolver.resolve(
                ActionBindingRequest(
                    job_id="job-1", action_type="repair_apply",
                    proposal_id="prop-1",
                )
            )


# ─── No-execution tests ────────────────────────────────────────────


class TestNoExecution:

    def test_binding_has_no_execution_fields(self) -> None:
        """Binding result must not contain execution-related fields."""
        commands = [
            FakeCommand(
                "cmd-1", "job-1", 1, "failed",
                result_json=json.dumps({"sandbox_path": "/sandbox/s1"}),
            ),
        ]
        resolver = V2AssistantActionResolver(
            resolver=make_fake_resolver(commands=commands)
        )
        result = resolver.resolve(
            ActionBindingRequest(job_id="job-1", action_type="repair_apply")
        )
        binding_dict = resolver.binding_to_dict(result.binding)
        # Must not contain execution fields
        assert "command" not in binding_dict
        assert "exec" not in binding_dict
        assert "approve" not in binding_dict
        assert "write" not in binding_dict

    def test_binding_path_is_redacted_in_dict(self) -> None:
        """binding_to_dict redacts sandbox path."""
        commands = [
            FakeCommand(
                "cmd-1", "job-1", 1, "failed",
                result_json=json.dumps({"sandbox_path": "/home/user/sandbox/s1"}),
            ),
        ]
        resolver = V2AssistantActionResolver(
            resolver=make_fake_resolver(commands=commands)
        )
        result = resolver.resolve(
            ActionBindingRequest(job_id="job-1", action_type="repair_apply")
        )
        d = resolver.binding_to_dict(result.binding)
        assert "[redacted" in d["sandbox_path"] or "/home" not in d["sandbox_path"]


# ── Legacy source detection tests ──────────────────────────────────


class TestLegacySourceDetection:

    def test_detects_legacy_prefix(self) -> None:
        assert is_legacy_source_path("/legacy/source/pom.xml")
        assert is_legacy_source_path("/src/main/legacy/App.java")

    def test_detects_backup_path(self) -> None:
        assert is_legacy_source_path("/backup/original/App.java")

    def test_accepts_sandbox_path(self) -> None:
        assert not is_legacy_source_path("/sandbox/stage-1/output")
        assert not is_legacy_source_path("/tmp/migration-sandbox/s1")

    def test_accepts_normal_path(self) -> None:
        assert not is_legacy_source_path("/home/user/project/src/main/java/App.java")


# ── Multi-stage tests ──────────────────────────────────────────────


class TestMultiStageBinding:

    def test_selects_correct_stage(self) -> None:
        """Resolver selects the command from the correct stage."""
        commands = [
            FakeCommand("cmd-s1", "job-1", 1, "completed"),
            FakeCommand("cmd-s2", "job-1", 2, "failed",
                        result_json=json.dumps({"sandbox_path": "/sandbox/s2"}),
                        created_at="2026-01-01T14:00:00"),
            FakeCommand("cmd-s3", "job-1", 3, "pending"),
        ]
        resolver = V2AssistantActionResolver(
            resolver=make_fake_resolver(commands=commands)
        )
        result = resolver.resolve(
            ActionBindingRequest(job_id="job-1", action_type="repair_apply", stage_index=2)
        )
        assert result.binding.stage_index == 2
        assert result.binding.command_id == "cmd-s2"

    def test_selects_latest_failed_without_stage(self) -> None:
        """Without stage_index, finds latest failed across all stages."""
        commands = [
            FakeCommand("cmd-s1", "job-1", 1, "completed",
                        created_at="2026-01-01T12:00:00"),
            FakeCommand("cmd-s2", "job-1", 2, "failed",
                        result_json=json.dumps({"sandbox_path": "/sandbox/s2"}),
                        created_at="2026-01-01T13:00:00"),
            FakeCommand("cmd-s3", "job-1", 3, "failed",
                        result_json=json.dumps({"sandbox_path": "/sandbox/s3"}),
                        created_at="2026-01-01T14:00:00"),
        ]
        resolver = V2AssistantActionResolver(
            resolver=make_fake_resolver(commands=commands)
        )
        result = resolver.resolve(
            ActionBindingRequest(job_id="job-1", action_type="repair_apply")
        )
        # Should pick latest (cmd-s3, stage 3)
        assert result.binding.stage_index == 3
        assert result.binding.command_id == "cmd-s3"


# ── Proposal binding tests ─────────────────────────────────────────


class TestProposalBinding:

    def test_binds_proposal_to_failed_command(self) -> None:
        """When proposal matches the failed command, binding succeeds."""
        commands = [
            FakeCommand(
                "cmd-fail", "job-1", 1, "failed",
                result_json=json.dumps({"sandbox_path": "/sandbox/s1"}),
            ),
        ]
        proposals = {
            "prop-1": FakeProposal("prop-1", command_id="cmd-fail"),
        }
        resolver = V2AssistantActionResolver(
            resolver=make_fake_resolver(commands=commands, proposals=proposals)
        )
        result = resolver.resolve(
            ActionBindingRequest(
                job_id="job-1", action_type="repair_apply",
                proposal_id="prop-1",
            )
        )
        assert result.binding.proposal_id == "prop-1"
        assert result.binding.command_id == "cmd-fail"
        assert result.binding.proposal_checksum == "abc123"


# ── Helper method tests ────────────────────────────────────────────


class TestFindLatestFailed:

    def test_skips_completed_commands(self) -> None:
        """Only actual failure statuses are considered."""
        commands = [
            FakeCommand("c1", "j1", 1, "completed"),
            FakeCommand("c2", "j1", 1, "running"),
            FakeCommand("c3", "j1", 1, "pending"),
        ]
        result = V2AssistantActionResolver._find_latest_failed(commands)
        assert result is None

    def test_picks_latest_by_time(self) -> None:
        """Among multiple failures, picks the most recent."""
        commands = [
            FakeCommand("c1", "j1", 1, "failed", created_at="2026-01-01T10:00:00"),
            FakeCommand("c2", "j1", 1, "failed", created_at="2026-01-01T11:00:00"),
        ]
        result = V2AssistantActionResolver._find_latest_failed(commands)
        assert result is not None
        assert result.command_id == "c2"
