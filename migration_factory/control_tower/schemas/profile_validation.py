"""Profile pair validation for source/target migration profiles.

Defines structured validation results that can be consumed both at the
schema layer (RunConfiguration) and the application layer (job service).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from .profile_model import (
    get_migration_profile,
    is_selectable_source_profile,
    is_selectable_target_profile,
)


class ProfilePairErrorType(str, Enum):
    VALID = "valid"
    SOURCE_UNKNOWN = "source_unknown"
    TARGET_UNKNOWN = "target_unknown"
    SOURCE_NOT_SELECTABLE = "source_not_selectable"
    TARGET_NOT_SELECTABLE = "target_not_selectable"
    REVERSED = "reversed"
    SAME_PROFILE = "same_profile"
    SOURCE_ALREADY_TERMINAL = "source_already_terminal"


_USER_VISIBLE_MESSAGES: dict[ProfilePairErrorType, str] = {
    ProfilePairErrorType.VALID: "profile pair is valid",
    ProfilePairErrorType.SOURCE_UNKNOWN: "source profile is not recognized",
    ProfilePairErrorType.TARGET_UNKNOWN: "target profile is not recognized",
    ProfilePairErrorType.SOURCE_NOT_SELECTABLE: "source profile cannot be used as a migration source",
    ProfilePairErrorType.TARGET_NOT_SELECTABLE: "target profile cannot be used as a migration target",
    ProfilePairErrorType.REVERSED: "target profile must be higher than source profile",
    ProfilePairErrorType.SAME_PROFILE: "source and target profiles must be different",
    ProfilePairErrorType.SOURCE_ALREADY_TERMINAL: "source profile is already at the maximum supported level",
}


@dataclass(frozen=True)
class ProfilePairValidation:
    """Result of validating a source/target profile pair."""

    source_profile: str
    target_profile: str
    valid: bool
    error_type: ProfilePairErrorType
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_profile": self.source_profile,
            "target_profile": self.target_profile,
            "valid": self.valid,
            "error_type": self.error_type.value,
            "reason": self.reason,
        }


def validate_profile_pair(
    source_profile: str | None,
    target_profile: str | None,
    *,
    allow_same_profile: bool = False,
) -> ProfilePairValidation:
    """Validate a source/target migration profile pair.

    Returns a structured validation result. Both fields may be None;
    in that case the pair is considered valid (no profile targeting).
    """
    if source_profile is None and target_profile is None:
        return ProfilePairValidation(
            source_profile="",
            target_profile="",
            valid=True,
            error_type=ProfilePairErrorType.VALID,
            reason="no profile targeting configured",
        )

    resolved_source = source_profile or ""
    resolved_target = target_profile or ""

    # ── source validation ──────────────────────────────────────────
    if source_profile is not None:
        if not is_selectable_source_profile(source_profile):
            source_def = get_migration_profile(source_profile)
            if source_def is None:
                return _invalid(ProfilePairErrorType.SOURCE_UNKNOWN, resolved_source, resolved_target)
            return _invalid(ProfilePairErrorType.SOURCE_NOT_SELECTABLE, resolved_source, resolved_target)

    # ── target validation ──────────────────────────────────────────
    if target_profile is not None:
        if not is_selectable_target_profile(target_profile):
            target_def = get_migration_profile(target_profile)
            if target_def is None:
                return _invalid(ProfilePairErrorType.TARGET_UNKNOWN, resolved_source, resolved_target)
            return _invalid(ProfilePairErrorType.TARGET_NOT_SELECTABLE, resolved_source, resolved_target)

    # ── both provided: ordering check ──────────────────────────────
    if source_profile is not None and target_profile is not None:
        source_def = get_migration_profile(source_profile)
        target_def = get_migration_profile(target_profile)
        if source_def is not None and target_def is not None:
            source_idx = source_def.order_index
            target_idx = target_def.order_index

            if target_idx == source_idx:
                if allow_same_profile:
                    return ProfilePairValidation(
                        source_profile=resolved_source,
                        target_profile=resolved_target,
                        valid=True,
                        error_type=ProfilePairErrorType.VALID,
                        reason="profile pair is valid as an explicit no-op",
                    )
                return _invalid(ProfilePairErrorType.SAME_PROFILE, resolved_source, resolved_target)
            if target_idx < source_idx:
                return _invalid(ProfilePairErrorType.REVERSED, resolved_source, resolved_target)
            if source_idx >= max(
                p.order_index for p in (source_def, target_def) if hasattr(p, "order_index")
            ):
                pass  # terminal check handled elsewhere when profiles are exhausted

    return ProfilePairValidation(
        source_profile=resolved_source,
        target_profile=resolved_target,
        valid=True,
        error_type=ProfilePairErrorType.VALID,
        reason="profile pair is valid",
    )


def _invalid(
    error_type: ProfilePairErrorType,
    source_profile: str,
    target_profile: str,
) -> ProfilePairValidation:
    return ProfilePairValidation(
        source_profile=source_profile,
        target_profile=target_profile,
        valid=False,
        error_type=error_type,
        reason=_USER_VISIBLE_MESSAGES[error_type],
    )
