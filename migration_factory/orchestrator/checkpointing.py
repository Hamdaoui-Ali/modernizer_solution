from __future__ import annotations

from collections import defaultdict
import pickle
import sqlite3
from pathlib import Path
from typing import Any

from langgraph.checkpoint.memory import InMemorySaver

from migration_factory.orchestrator.preflight import PreflightError


class SQLiteBackedInMemorySaver(InMemorySaver):
    """Durable local saver that preserves LangGraph checkpoints across CLI runs."""

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path).expanduser().resolve()
        super().__init__()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._load()

    def put(self, config, checkpoint, metadata, new_versions):
        saved_config = super().put(config, checkpoint, metadata, new_versions)
        self._persist()
        return saved_config

    def put_writes(self, config, writes, task_id, task_path=""):
        result = super().put_writes(config, writes, task_id, task_path)
        self._persist()
        return result

    def delete_thread(self, thread_id: str) -> None:
        super().delete_thread(thread_id)
        self._persist()

    def _persist(self) -> None:
        payload = pickle.dumps(
            {
                "storage": _plain(self.storage),
                "writes": _plain(self.writes),
                "blobs": _plain(self.blobs),
            }
        )
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                "create table if not exists checkpoints (id text primary key, payload blob not null)"
            )
            connection.execute(
                "insert or replace into checkpoints (id, payload) values (?, ?)",
                ("langgraph", payload),
            )

    def _load(self) -> None:
        if not self.db_path.is_file():
            return
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                "create table if not exists checkpoints (id text primary key, payload blob not null)"
            )
            row = connection.execute(
                "select payload from checkpoints where id = ?",
                ("langgraph",),
            ).fetchone()
        if row is None:
            return

        payload: dict[str, Any] = pickle.loads(row[0])
        self.storage = defaultdict(lambda: defaultdict(dict))
        for thread_id, namespaces in payload.get("storage", {}).items():
            self.storage[thread_id] = defaultdict(dict, namespaces)
        self.writes = defaultdict(dict, payload.get("writes", {}))
        self.blobs = defaultdict(None, payload.get("blobs", {}))


def default_checkpointer(run_dir: str | Path | None = None) -> InMemorySaver:
    if run_dir is None:
        return InMemorySaver()
    return SQLiteBackedInMemorySaver(
        Path(run_dir) / "orchestration" / "langgraph_checkpoints.sqlite"
    )


def in_memory_checkpointer() -> InMemorySaver:
    return InMemorySaver()


def require_thread_id(config: dict, run_id: str) -> None:
    thread_id = config.get("configurable", {}).get("thread_id")
    if thread_id != run_id:
        raise PreflightError(f"thread_id must match run_id: {run_id}")


def _plain(value: Any) -> Any:
    if isinstance(value, defaultdict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    return value
