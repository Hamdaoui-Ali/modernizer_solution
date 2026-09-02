"""Tests for V2 approval mapping service."""

import pytest

from migration_factory.control_tower.application.v2_approval_mapping import (
    V2ApprovalMappingService,
)


def _card(service: V2ApprovalMappingService):
    return service.create_decision_card(
        job_id="job-1",
        interrupt_id="int-1",
        request_checksum="abc123",
        stage_index=1,
    )


def test_create_decision_card() -> None:
    service = V2ApprovalMappingService()
    card = _card(service)
    assert card.card_id
    assert card.job_id == "job-1"
    assert card.interrupt_id == "int-1"
    assert card.request_checksum == "abc123"
    assert card.status == "pending"


def test_approve_with_correct_checksum() -> None:
    service = V2ApprovalMappingService()
    card = _card(service)
    resume = service.approve(
        card_id=card.card_id,
        expected_checksum="abc123",
        job_id="job-1",
        run_dir="C:/out/.migration/runs/int-1",
    )
    assert resume.decision == "approved"
    assert resume.job_id == "job-1"
    assert "--decision" in resume.command
    assert "approved" in resume.command
    assert "--run-dir" in resume.command

    updated = service.get_card(card.card_id)
    assert updated is not None
    assert updated.status == "approved"


def test_approve_with_wrong_checksum() -> None:
    service = V2ApprovalMappingService()
    card = _card(service)
    with pytest.raises(ValueError, match="Checksum mismatch"):
        service.approve(
            card_id=card.card_id,
            expected_checksum="wrong-checksum",
            job_id="job-1",
        )


def test_reject_card() -> None:
    service = V2ApprovalMappingService()
    card = _card(service)
    rejected = service.reject(card_id=card.card_id, job_id="job-1")
    assert rejected.status == "rejected"


def test_cannot_approve_wrong_job() -> None:
    service = V2ApprovalMappingService()
    card = _card(service)
    with pytest.raises(ValueError, match="does not belong"):
        service.approve(card_id=card.card_id, expected_checksum="abc123", job_id="job-2")


def test_can_approve_twice_idempotent() -> None:
    service = V2ApprovalMappingService()
    card = _card(service)
    r1 = service.approve(card_id=card.card_id, expected_checksum="abc123", job_id="job-1")
    assert r1.decision == "approved"
    # Duplicate approve must succeed (idempotent)
    r2 = service.approve(card_id=card.card_id, expected_checksum="abc123", job_id="job-1")
    assert r2.decision == "approved"


def test_can_reject_twice_idempotent() -> None:
    service = V2ApprovalMappingService()
    card = _card(service)
    r1 = service.reject(card_id=card.card_id, job_id="job-1")
    assert r1.status == "rejected"
    # Duplicate reject must succeed (idempotent)
    r2 = service.reject(card_id=card.card_id, job_id="job-1")
    assert r2.status == "rejected"


def test_cannot_approve_after_reject() -> None:
    service = V2ApprovalMappingService()
    card = _card(service)
    service.reject(card_id=card.card_id, job_id="job-1")
    with pytest.raises(ValueError, match="already rejected"):
        service.approve(card_id=card.card_id, expected_checksum="abc123", job_id="job-1")


def test_cannot_reject_after_approve() -> None:
    service = V2ApprovalMappingService()
    card = _card(service)
    service.approve(card_id=card.card_id, expected_checksum="abc123", job_id="job-1")
    with pytest.raises(ValueError, match="already approved"):
        service.reject(card_id=card.card_id, job_id="job-1")


def test_unknown_card() -> None:
    service = V2ApprovalMappingService()
    assert service.get_card("nonexistent") is None


def test_card_to_dict() -> None:
    service = V2ApprovalMappingService()
    card = _card(service)
    d = service.card_to_dict(card)
    assert d["card_id"] == card.card_id
    assert d["job_id"] == "job-1"
    assert d["status"] == "pending"


def test_resume_to_dict() -> None:
    service = V2ApprovalMappingService()
    card = _card(service)
    resume = service.approve(card_id=card.card_id, expected_checksum="abc123", job_id="job-1")
    d = service.resume_to_dict(resume)
    assert d["decision"] == "approved"
    assert isinstance(d["command"], list)
