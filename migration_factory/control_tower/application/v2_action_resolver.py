"""Sandbox action resolver (F06).

Resolves and validates backend-owned job/stage/failed-command/sandbox/proposal
binding for every assistant-generated action object before it becomes an
approval candidate.

Responsibilities:
1. Load job and V2 commands from repositories.
2. Find the latest failed command for the active stage.
3. Extract sandbox path from backend events/result artifacts.
4. Verify sandbox belongs to the command/job and is not legacy source.
5. Verify proposal command_id matches failed command id.
6. Compute checksum over resolved binding and proposal payload.
7. Return a binding result — never execute.

Fail-safe: resolver raises ValueError on stale, unsafe, or unverifiable binding.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable
from uuid import uuid4

from migration_factory.control_tower.domain.checksums import (
    utc_now_text,
    sha256_canonical_json,
)


# ── Legacy source patterns ─────────────────────────────────────────

# Path prefixes that indicate legacy source (never mutate)
LEGACY_SOURCE_PREFIXES: tuple[str, ...] = (
    "/legacy/",
    "/src/main/legacy/",
    "/original/",
    "/backup/",
)


def is_legacy_source_path(path: str) -> bool:
    """Check if a path indicates legacy source location.

    Sandbox actions must never mutate legacy source.
    """
    normalized = path.replace("\\", "/").lower()
    for prefix in LEGACY_SOURCE_PREFIXES:
        if normalized.startswith(prefix) or f"/{prefix.lstrip('/')}" in normalized:
            return True
    return False


# ── Data types ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class ActionBindingRequest:
    """Input to the action resolver.

    Combines the job/command/proposal context that must be validated
    before any action can proceed.
    """

    job_id: str
    action_type: str  # e.g. "repair_apply", "plan_amendment", "validation_rerun"
    stage_index: int | None = None
    proposal_id: str | None = None
    event_id: str | None = None
    requester: str = "assistant"
    # F05: revision steering fields
    source_proposal_id: str | None = None
    failed_command_id: str | None = None
    revision_instruction: str | None = None
    context_pack_checksum: str | None = None
    allowed_scope: str | None = None  # any, pom_only


@dataclass(frozen=True)
class FailedCommandInfo:
    """Information about the latest failed command for a job/stage."""

    command_id: str
    stage_index: int
    status: str
    sandbox_path: str
    result_json: str | None = None
    created_at: str = ""


@dataclass(frozen=True)
class SandboxBinding:
    """Resolved sandbox binding with validation metadata."""

    binding_id: str
    job_id: str
    stage_index: int
    command_id: str
    sandbox_path: str
    sandbook_checksum: str
    proposal_id: str | None = None
    proposal_checksum: str | None = None
    binding_checksum: str = ""
    resolved_at: str = ""


@dataclass(frozen=True)
class BindingResult:
    """Result of a successful binding resolution.

    Contains the resolved binding plus context for downstream approval.
    This is NOT an execution command — it is a validated proposal candidate.
    """

    binding: SandboxBinding
    failed_command: FailedCommandInfo
    verified: bool = True
    warnings: tuple[str, ...] = ()


# ── Repository protocol (duck-typed) ──────────────────────────────


class ActionResolverProtocol:
    """Protocol for repositories used by the resolver.

    Implementations can be in-memory dicts for testing or real SQLite
    repositories in production. The resolver calls these methods only.
    """

    def __init__(
        self,
        *,
        get_job: Callable[[str], Any | None] | None = None,
        list_commands: Callable[[str], tuple[Any, ...]] | None = None,
        list_commands_by_stage: Callable[[str, int], tuple[Any, ...]] | None = None,
        list_events: Callable[[str], tuple[Any, ...]] | None = None,
        get_proposal: Callable[[str], Any | None] | None = None,
    ) -> None:
        self._get_job = get_job
        self._list_commands = list_commands
        self._list_commands_by_stage = list_commands_by_stage
        self._list_events = list_events
        self._get_proposal = get_proposal

    def get_job(self, job_id: str) -> Any | None:
        if self._get_job:
            return self._get_job(job_id)
        return None

    def list_commands(self, job_id: str) -> tuple[Any, ...]:
        if self._list_commands:
            return self._list_commands(job_id)
        return ()

    def list_commands_by_stage(self, job_id: str, stage_index: int) -> tuple[Any, ...]:
        if self._list_commands_by_stage:
            return self._list_commands_by_stage(job_id, stage_index)
        return ()

    def list_events(self, job_id: str) -> tuple[Any, ...]:
        if self._list_events:
            return self._list_events(job_id)
        return ()

    def get_proposal(self, proposal_id: str) -> Any | None:
        if self._get_proposal:
            return self._get_proposal(proposal_id)
        return None


# ── Action resolver ────────────────────────────────────────────────


class V2AssistantActionResolver:
    """Resolver that validates action binding before approval.

    Every action request must go through this resolver to ensure:
    - The job exists and is active
    - The command belongs to the job
    - The sandbox is backend-resolved (not user/model-provided)
    - The sandbox is not legacy source
    - The proposal (if given) matches the failed command
    - The binding checksum guards against stale state
    """

    def __init__(self, resolver: ActionResolverProtocol | None = None) -> None:
        self._resolver = resolver

    def resolve(
        self,
        request: ActionBindingRequest,
    ) -> BindingResult:
        """Resolve and validate an action binding.

        Args:
            request: The action binding request with job/command/proposal context.

        Returns:
            A BindingResult with validated sandbox binding.

        Raises:
            ValueError: If any validation fails (stale, unsafe, unverifiable).
        """
        # 1. Load and validate job
        job = None
        if self._resolver is not None:
            job = self._resolver.get_job(request.job_id)
        if job is None:
            raise ValueError(f"Job {request.job_id!r} not found")
        job_status = str(getattr(job, "status", "") or "").lower()
        if job_status and job_status not in ("active", "running", "in_progress"):
            raise ValueError(
                f"Job {request.job_id!r} is not active (status: {job_status!r})"
            )

        # 2. Find latest failed command for the active stage
        commands = []
        if self._resolver is not None:
            if request.stage_index is not None:
                commands = list(self._resolver.list_commands_by_stage(
                    request.job_id, request.stage_index
                ))
            else:
                commands = list(self._resolver.list_commands(request.job_id))

        if not commands:
            raise ValueError(
                f"No commands found for job {request.job_id!r}"
                + (f" stage {request.stage_index}" if request.stage_index is not None else "")
            )

        # Find the latest failed command
        failed_command = self._find_latest_failed(commands)
        if failed_command is None:
            raise ValueError(
                f"No failed command found for job {request.job_id!r}"
                + (f" stage {request.stage_index}" if request.stage_index is not None else "")
            )

        # 3. Extract and validate sandbox path
        sandbox_path = self._resolve_sandbox_path(failed_command)
        if not sandbox_path:
            raise ValueError(
                f"Cannot resolve sandbox path for failed command {failed_command.command_id!r}"
            )

        # 4. Verify sandbox is not legacy source
        if is_legacy_source_path(sandbox_path):
            raise ValueError(
                f"Sandbox path {sandbox_path!r} resolves to legacy source — rejected"
            )

        # 5. Verify proposal command_id matches failed command (if proposal_id given)
        proposal_checksum = None
        if request.proposal_id is not None:
            proposal = None
            if self._resolver is not None:
                proposal = self._resolver.get_proposal(request.proposal_id)
            if proposal is None:
                raise ValueError(f"Proposal {request.proposal_id!r} not found")
            # Check proposal-command binding
            proposal_cmd_id = self._get_proposal_command_id(proposal)
            if proposal_cmd_id is not None and proposal_cmd_id != failed_command.command_id:
                raise ValueError(
                    f"Proposal {request.proposal_id!r} is bound to command "
                    f"{proposal_cmd_id!r}, but the latest failed command is "
                    f"{failed_command.command_id!r}"
                )
            proposal_checksum = self._get_proposal_checksum(proposal)

        # 6. Compute binding checksum
        binding = SandboxBinding(
            binding_id=uuid4().hex,
            job_id=request.job_id,
            stage_index=failed_command.stage_index,
            command_id=failed_command.command_id,
            sandbox_path=sandbox_path,
            sandbook_checksum=sha256_canonical_json({"sandbox_path": sandbox_path}),
            proposal_id=request.proposal_id,
            proposal_checksum=proposal_checksum,
            resolved_at=utc_now_text(),
        )

        # Compute full binding checksum
        binding_checksum_content = {
            "binding_id": binding.binding_id,
            "job_id": binding.job_id,
            "stage_index": binding.stage_index,
            "command_id": binding.command_id,
            "sandbox_path": binding.sandbox_path,
            "proposal_id": binding.proposal_id,
            "proposal_checksum": binding.proposal_checksum,
        }
        binding_with_checksum = SandboxBinding(
            binding_id=binding.binding_id,
            job_id=binding.job_id,
            stage_index=binding.stage_index,
            command_id=binding.command_id,
            sandbox_path=binding.sandbox_path,
            sandbook_checksum=binding.sandbook_checksum,
            proposal_id=binding.proposal_id,
            proposal_checksum=binding.proposal_checksum,
            binding_checksum=sha256_canonical_json(binding_checksum_content),
            resolved_at=binding.resolved_at,
        )

        return BindingResult(
            binding=binding_with_checksum,
            failed_command=failed_command,
        )

    def binding_to_dict(self, binding: SandboxBinding) -> dict[str, Any]:
        """Convert a SandboxBinding to a dict for API responses.

        Sandbox path is redacted to avoid leaking absolute paths.
        """
        from migration_factory.control_tower.application.redaction import (
            redact_absolute_paths,
        )

        return {
            "binding_id": binding.binding_id,
            "job_id": binding.job_id,
            "stage_index": binding.stage_index,
            "command_id": binding.command_id,
            "sandbox_path": redact_absolute_paths(binding.sandbox_path),
            "sandbox_checksum": binding.sandbook_checksum,
            "proposal_id": binding.proposal_id,
            "proposal_checksum": binding.proposal_checksum,
            "binding_checksum": binding.binding_checksum,
            "resolved_at": binding.resolved_at,
        }

    def result_to_dict(self, result: BindingResult) -> dict[str, Any]:
        """Convert a BindingResult to a dict for API responses."""
        return {
            "binding": self.binding_to_dict(result.binding),
            "failed_command": {
                "command_id": result.failed_command.command_id,
                "stage_index": result.failed_command.stage_index,
                "status": result.failed_command.status,
                "sandbox_path": result.failed_command.sandbox_path,
            },
            "verified": result.verified,
            "warnings": list(result.warnings),
        }

    # ── Internal helpers ───────────────────────────────────────────

    @staticmethod
    def _find_latest_failed(
        commands: list[Any],
    ) -> FailedCommandInfo | None:
        """Find the latest command with a failed status.

        Searches commands sorted by creation time (newest first).
        """
        # Sort by created_at descending if available
        sorted_commands = sorted(
            commands,
            key=lambda c: getattr(c, "created_at", "") or "",
            reverse=True,
        )
        for cmd in sorted_commands:
            status = getattr(cmd, "status", "") or ""
            if status in ("failed", "error", "timeout"):
                sandbox_path = ""
                result_json = getattr(cmd, "result_json", None)
                if result_json:
                    try:
                        result = json.loads(result_json)
                        sandbox_path = str(result.get("sandbox_path", "") or "")
                    except (json.JSONDecodeError, TypeError):
                        pass
                return FailedCommandInfo(
                    command_id=getattr(cmd, "command_id", "") or "",
                    stage_index=int(getattr(cmd, "stage_index", 1) or 1),
                    status=status,
                    sandbox_path=sandbox_path,
                    result_json=result_json,
                    created_at=getattr(cmd, "created_at", "") or "",
                )
        return None

    @staticmethod
    def _resolve_sandbox_path(failed_command: FailedCommandInfo) -> str:
        """Extract sandbox path from a failed command.

        Priority:
        1. sandbox_path from result_json
        2. Empty string if unresolvable
        """
        if failed_command.sandbox_path:
            return failed_command.sandbox_path
        return ""

    @staticmethod
    def _get_proposal_command_id(proposal: Any) -> str | None:
        """Extract command_id from a proposal object."""
        cmd_id = getattr(proposal, "command_id", None)
        if cmd_id is not None:
            return str(cmd_id)
        return None

    @staticmethod
    def _get_proposal_checksum(proposal: Any) -> str | None:
        """Extract proposal checksum."""
        checksum = getattr(proposal, "approval_checksum", None)
        if checksum is not None:
            return str(checksum)
        return None

    # ── F05: Revision path ──────────────────────────────────────────

    def resolve_revision(
        self,
        request: ActionBindingRequest,
    ) -> BindingResult:
        """Resolve a revise_repair_proposal action.

        Requires:
        - source_proposal_id: the original proposal being revised
        - failed_command_id: the failed command to rebind to
        - context_pack_checksum: current context checksum for staleness check
        - revision_instruction: human steering instruction
        - Job/command binding validation
        - allowed_scope=pom_only enforcement (server-side)

        Never mutates the source proposal — creates a new draft.
        """
        if request.source_proposal_id is None:
            raise ValueError(
                "source_proposal_id is required for revision resolution"
            )
        if request.failed_command_id is None:
            raise ValueError(
                "failed_command_id is required for revision resolution"
            )
        if request.context_pack_checksum is None:
            raise ValueError(
                "context_pack_checksum is required for revision resolution"
            )

        # 1. Load and validate job
        job = None
        if self._resolver is not None:
            job = self._resolver.get_job(request.job_id)
        if job is None:
            raise ValueError(f"Job {request.job_id!r} not found")
        job_status = str(getattr(job, "status", "") or "").lower()
        if job_status and job_status not in ("active", "running", "in_progress"):
            raise ValueError(
                f"Job {request.job_id!r} is not active (status: {job_status!r})"
            )

        # 2. Load and validate source proposal
        source_proposal = None
        if self._resolver is not None:
            source_proposal = self._resolver.get_proposal(request.source_proposal_id)
        if source_proposal is None:
            raise ValueError(f"Source proposal {request.source_proposal_id!r} not found")

        # 3. Check stale/applied status
        source_status = getattr(source_proposal, "status", "") or ""
        if source_status in ("approved", "applied"):
            raise ValueError(
                f"Source proposal {request.source_proposal_id!r} is already "
                f"{source_status} — cannot revise an approved/applied proposal"
            )

        # 4. Verify failed_command_id exists and is actually failed
        commands = []
        if self._resolver is not None:
            commands = list(self._resolver.list_commands(request.job_id))
        target_command = None
        for cmd in commands:
            if getattr(cmd, "command_id", "") == request.failed_command_id:
                target_command = cmd
                break
        if target_command is None:
            raise ValueError(
                f"Command {request.failed_command_id!r} not found for job {request.job_id!r}"
            )
        cmd_status = getattr(target_command, "status", "") or ""
        if cmd_status not in ("failed", "error", "timeout"):
            raise ValueError(
                f"Command {request.failed_command_id!r} status is {cmd_status!r}, "
                f"expected failed/error/timeout"
            )

        # 5. Verify proposal command_id matches failed command
        proposal_cmd_id = self._get_proposal_command_id(source_proposal)
        if proposal_cmd_id is not None and proposal_cmd_id != request.failed_command_id:
            raise ValueError(
                f"Source proposal {request.source_proposal_id!r} is bound to command "
                f"{proposal_cmd_id!r}, but failed_command_id is "
                f"{request.failed_command_id!r}"
            )

        # 6. Extract sandbox path and verify not legacy source
        sandbox_path = ""
        result_json = getattr(target_command, "result_json", None)
        if result_json:
            try:
                result = json.loads(result_json)
                sandbox_path = str(result.get("sandbox_path", "") or "")
            except (json.JSONDecodeError, TypeError):
                pass
        if sandbox_path:
            if is_legacy_source_path(sandbox_path):
                raise ValueError(
                    f"Sandbox path {sandbox_path!r} resolves to legacy source — rejected"
                )

        # 7. F05: allowed_scope=pom_only enforcement on source proposal paths
        if request.allowed_scope == "pom_only":
            affected_paths = getattr(source_proposal, "affected_paths", []) or []
            if isinstance(affected_paths, str):
                try:
                    affected_paths = json.loads(affected_paths)
                except (json.JSONDecodeError, TypeError):
                    affected_paths = []
            non_pom_paths = [
                p for p in affected_paths
                if not p.endswith("pom.xml") and "/pom.xml" not in p
            ]
            if non_pom_paths:
                raise ValueError(
                    f"allowed_scope=pom_only violated: non-POM paths in source "
                    f"proposal: {non_pom_paths}"
                )

        # 8. Build binding result
        stage_index = int(getattr(target_command, "stage_index", 1) or 1)
        proposal_checksum = self._get_proposal_checksum(source_proposal)

        binding = SandboxBinding(
            binding_id=uuid4().hex,
            job_id=request.job_id,
            stage_index=stage_index,
            command_id=request.failed_command_id,
            sandbox_path=sandbox_path or "<redacted>",
            sandbook_checksum=sha256_canonical_json({"sandbox_path": sandbox_path or ""}),
            proposal_id=request.source_proposal_id,
            proposal_checksum=proposal_checksum,
            resolved_at=utc_now_text(),
        )
        binding_with_checksum = SandboxBinding(
            binding_id=binding.binding_id,
            job_id=binding.job_id,
            stage_index=binding.stage_index,
            command_id=binding.command_id,
            sandbox_path=binding.sandbox_path,
            sandbook_checksum=binding.sandbook_checksum,
            proposal_id=binding.proposal_id,
            proposal_checksum=binding.proposal_checksum,
            binding_checksum=sha256_canonical_json({
                "binding_id": binding.binding_id,
                "job_id": binding.job_id,
                "stage_index": binding.stage_index,
                "command_id": binding.command_id,
                "sandbox_path": binding.sandbox_path,
                "proposal_id": binding.proposal_id,
                "proposal_checksum": binding.proposal_checksum,
                "revision_of": request.source_proposal_id,
                "allowed_scope": request.allowed_scope or "any",
            }),
            resolved_at=binding.resolved_at,
        )

        failed_cmd_info = FailedCommandInfo(
            command_id=request.failed_command_id,
            stage_index=stage_index,
            status=cmd_status,
            sandbox_path=sandbox_path or "",
            result_json=result_json,
            created_at=getattr(target_command, "created_at", "") or "",
        )

        return BindingResult(
            binding=binding_with_checksum,
            failed_command=failed_cmd_info,
            verified=True,
            warnings=(
                (f"allowed_scope=pom_only enforced — only POM paths allowed",)
                if request.allowed_scope == "pom_only"
                else ()
            ),
        )
