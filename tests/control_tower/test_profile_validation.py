"""Tests for profile pair validation (AMF-263 / F3-T2)."""

from __future__ import annotations

import pytest

from migration_factory.control_tower.schemas.profile_validation import (
    ProfilePairErrorType,
    ProfilePairValidation,
    validate_profile_pair,
)


# ── valid pairs ────────────────────────────────────────────────────

def test_valid_pair_springboot_2_to_3_17() -> None:
    result = validate_profile_pair("springboot-2.7-java11", "springboot-3.5-java17")
    assert result.valid is True
    assert result.error_type == ProfilePairErrorType.VALID


def test_valid_pair_springboot_2_to_4() -> None:
    result = validate_profile_pair("springboot-2.7-java11", "springboot-4.0-java21")
    assert result.valid is True


def test_valid_pair_springboot_3_17_to_3_21() -> None:
    result = validate_profile_pair("springboot-3.5-java17", "springboot-3.5-java21")
    assert result.valid is True


def test_valid_pair_springboot_3_21_to_4() -> None:
    result = validate_profile_pair("springboot-3.5-java21", "springboot-4.0-java21")
    assert result.valid is True


def test_both_none_is_valid() -> None:
    result = validate_profile_pair(None, None)
    assert result.valid is True
    assert result.error_type == ProfilePairErrorType.VALID


def test_source_only_is_valid() -> None:
    """Setting only source without target is valid (no target constraint)."""
    result = validate_profile_pair("springboot-2.7-java11", None)
    assert result.valid is True


def test_target_only_is_valid() -> None:
    """Setting only target without source is valid."""
    result = validate_profile_pair(None, "springboot-3.5-java17")
    assert result.valid is True


# ── invalid source ─────────────────────────────────────────────────

def test_unknown_source_rejected() -> None:
    result = validate_profile_pair("unknown-profile", "springboot-3.5-java17")
    assert result.valid is False
    assert result.error_type == ProfilePairErrorType.SOURCE_UNKNOWN
    assert "not recognized" in result.reason.lower()


def test_non_selectable_source_rejected() -> None:
    """springboot-4.0-java21 is not selectable as source."""
    result = validate_profile_pair("springboot-4.0-java21", "springboot-4.0-java21")
    # source_not_selectable takes priority over same_profile
    assert result.valid is False
    assert result.error_type == ProfilePairErrorType.SOURCE_NOT_SELECTABLE


# ── invalid target ─────────────────────────────────────────────────

def test_unknown_target_rejected() -> None:
    result = validate_profile_pair("springboot-2.7-java11", "unknown-target")
    assert result.valid is False
    assert result.error_type == ProfilePairErrorType.TARGET_UNKNOWN
    assert "not recognized" in result.reason.lower()


def test_non_selectable_target_rejected() -> None:
    """springboot-2.1-java11 is not selectable as target."""
    result = validate_profile_pair("springboot-3.5-java17", "springboot-2.1-java11")
    assert result.valid is False
    assert result.error_type == ProfilePairErrorType.TARGET_NOT_SELECTABLE
    assert "cannot be used as a migration target" in result.reason.lower()


# ── reversed / same-profile ────────────────────────────────────────

def test_reversed_pair_rejected() -> None:
    """Higher source, lower target should be rejected."""
    result = validate_profile_pair("springboot-3.5-java21", "springboot-3.5-java17")
    assert result.valid is False
    assert result.error_type == ProfilePairErrorType.REVERSED
    assert "higher" in result.reason.lower() or "must be" in result.reason.lower()


def test_same_profile_rejected() -> None:
    result = validate_profile_pair("springboot-3.5-java17", "springboot-3.5-java17")
    assert result.valid is False
    assert result.error_type == ProfilePairErrorType.SAME_PROFILE
    assert "different" in result.reason.lower() or "must be different" in result.reason.lower()


# ── serialization ──────────────────────────────────────────────────

def test_validation_to_dict() -> None:
    result = validate_profile_pair("springboot-2.7-java11", "springboot-3.5-java17")
    d = result.to_dict()
    assert d["source_profile"] == "springboot-2.7-java11"
    assert d["target_profile"] == "springboot-3.5-java17"
    assert d["valid"] is True
    assert d["error_type"] == "valid"
    assert isinstance(d["reason"], str)


def test_invalid_validation_to_dict() -> None:
    result = validate_profile_pair("springboot-3.5-java21", "springboot-3.5-java17")
    d = result.to_dict()
    assert d["valid"] is False
    assert d["error_type"] == "reversed"
    assert d["source_profile"] == "springboot-3.5-java21"
    assert d["target_profile"] == "springboot-3.5-java17"


# ── error type completeness ────────────────────────────────────────

def test_all_error_types_have_messages() -> None:
    from migration_factory.control_tower.schemas.profile_validation import (
        _USER_VISIBLE_MESSAGES,
    )
    for error_type in ProfilePairErrorType:
        assert error_type in _USER_VISIBLE_MESSAGES, (
            f"Missing user-visible message for {error_type}"
        )


# ── frozen / immutability ──────────────────────────────────────────

def test_validation_is_immutable() -> None:
    result = validate_profile_pair("springboot-2.7-java11", "springboot-3.5-java17")
    with pytest.raises(Exception):
        result.valid = False  # type: ignore[misc]
