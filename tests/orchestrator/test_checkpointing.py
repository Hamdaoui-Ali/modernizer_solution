import pytest
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.memory import InMemorySaver

from migration_factory.orchestrator.checkpointing import (
    SQLiteBackedInMemorySaver,
    default_checkpointer,
    require_thread_id,
)
from migration_factory.orchestrator.preflight import PreflightError, build_langgraph_config


def test_default_checkpointer_returns_in_memory_saver() -> None:
    checkpointer = default_checkpointer()

    assert isinstance(checkpointer, InMemorySaver)


def test_default_checkpointer_with_run_dir_returns_persistent_saver(tmp_path) -> None:
    checkpointer = default_checkpointer(tmp_path / "run")

    assert isinstance(checkpointer, SQLiteBackedInMemorySaver)


def test_default_checkpointer_keeps_checkpoint_in_same_process() -> None:
    checkpointer = default_checkpointer()
    config = {"configurable": {"thread_id": "run-001", "checkpoint_ns": ""}}
    checkpoint = empty_checkpoint()
    checkpoint["channel_values"]["phase"] = "analysis"
    checkpoint["channel_versions"]["phase"] = "1"

    saved_config = checkpointer.put(
        checkpoint=checkpoint,
        config=config,
        metadata={},
        new_versions={"phase": "1"},
    )

    saved_checkpoint = checkpointer.get(saved_config)
    assert saved_checkpoint is not None
    assert saved_checkpoint["channel_values"]["phase"] == "analysis"


def test_persistent_checkpointer_loads_checkpoint_in_new_instance(tmp_path) -> None:
    run_dir = tmp_path / "run"
    config = {"configurable": {"thread_id": "run-001", "checkpoint_ns": ""}}
    checkpoint = empty_checkpoint()
    checkpoint["channel_values"]["phase"] = "approval"
    checkpoint["channel_versions"]["phase"] = "1"

    saved_config = default_checkpointer(run_dir).put(
        checkpoint=checkpoint,
        config=config,
        metadata={},
        new_versions={"phase": "1"},
    )

    restored = default_checkpointer(run_dir).get(saved_config)
    assert restored is not None
    assert restored["channel_values"]["phase"] == "approval"


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"configurable": {}},
    ],
)
def test_require_thread_id_rejects_missing_thread_id(config: dict) -> None:
    with pytest.raises(PreflightError, match="thread_id must match run_id: run-001"):
        require_thread_id(config, "run-001")


def test_require_thread_id_rejects_wrong_thread_id() -> None:
    config = build_langgraph_config("other-run")

    with pytest.raises(PreflightError, match="thread_id must match run_id: run-001"):
        require_thread_id(config, "run-001")


def test_require_thread_id_accepts_matching_thread_id() -> None:
    require_thread_id(build_langgraph_config("run-001"), "run-001")
