"""Tests for the centralized V2 assistant response composer."""

from __future__ import annotations

from types import SimpleNamespace

from migration_factory.control_tower.application.v2_assistant_response_composer import (
    AssistantResponseCard,
    AssistantResponseSection,
    V2AssistantResponseComposer,
)
from migration_factory.control_tower.adapters.fastapi.app import _build_status_answer


def test_render_applied_change_card_formats_change_validation_and_next() -> None:
    composer = V2AssistantResponseComposer()
    answer = composer.render(
        AssistantResponseCard(
            headline="POM change applied",
            status="done",
            summary="The change was written to the Stage 3 sandbox.",
            sections=(
                AssistantResponseSection(
                    title="Change",
                    lines=(
                        "Operation: update_property_version",
                        "Target: org.modelmapper.version",
                        "Before: 2.3.2",
                        "After: 3.2.0",
                    ),
                ),
                AssistantResponseSection(
                    title="Validation",
                    lines=(
                        "Status: running",
                        "Validation ID: val-123",
                        "Rollback: available",
                    ),
                ),
            ),
            next_step="Open Stage 3 Dependency Review to inspect validation results.",
        )
    )

    assert isinstance(answer, str)
    assert answer.startswith("POM change applied")
    assert "Change" in answer
    assert "Validation" in answer
    assert "Next" in answer
    assert "org.modelmapper.version" in answer
    assert "val-123" in answer


def test_render_blocked_change_card_includes_reason_and_safe_next_step() -> None:
    composer = V2AssistantResponseComposer()
    answer = composer.render(
        AssistantResponseCard(
            headline="Change blocked safely",
            status="blocked",
            summary="The backend refused to apply this change.",
            sections=(
                AssistantResponseSection(
                    title="Reason",
                    lines=("Tomcat is not directly declared in the current POM.",),
                ),
            ),
            safety_note='Ask for a proposal instead of a direct apply: "Explain how Tomcat is managed."',
        )
    )

    assert isinstance(answer, str)
    assert answer.startswith("Change blocked safely")
    assert "Reason" in answer
    assert "Safe next step" in answer
    assert "Tomcat is not directly declared" in answer


def test_render_redacts_paths_and_secrets() -> None:
    composer = V2AssistantResponseComposer()
    answer = composer.render(
        AssistantResponseCard(
            headline="Model status",
            status="info",
            summary="Open C:\\Users\\abdelilah.mortaki\\Desktop\\modernizer-solution\\pom.xml and AZURE_OPENAI_KEY=secret.",
            sections=(
                AssistantResponseSection(
                    title="Status",
                    lines=(
                        "Path: C:\\Users\\abdelilah.mortaki\\Desktop\\modernizer-solution\\pom.xml",
                        "Secret: AZURE_OPENAI_KEY=secret",
                    ),
                ),
            ),
        )
    )

    assert isinstance(answer, str)
    assert "C:\\Users\\abdelilah.mortaki" not in answer
    assert "AZURE_OPENAI_KEY=secret" not in answer
    assert "[redacted" in answer


def test_build_status_answer_starts_with_migration_state() -> None:
    events = (
        SimpleNamespace(type="stage_completed", stage=1, status="completed", message="Stage 1 done", payload_json="{}"),
        SimpleNamespace(type="stage_completed", stage=2, status="completed", message="Stage 2 done", payload_json="{}"),
        SimpleNamespace(type="stage_completed", stage=3, status="completed", message="Stage 3 done", payload_json="{}"),
        SimpleNamespace(type="build_completed", stage=3, status="completed", message="Build done", payload_json="{}"),
        SimpleNamespace(type="test_completed", stage=3, status="completed", message="Tests done", payload_json="{}"),
    )

    answer = _build_status_answer(
        question="what happened?",
        events=events,
        approvals=(),
        commands=(),
        artifact_previews=(),
    )

    assert isinstance(answer, str)
    assert answer.startswith("Migration completed")
    assert "Stage Status:" in answer
    assert "Artifacts" in answer
    assert "Next operator action:" in answer
