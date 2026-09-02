"""V1 model profile repository implementation for SQLite."""

from __future__ import annotations

import sqlite3
from uuid import uuid4

from migration_factory.control_tower.domain.model_profiles import V1ModelProfileRecord
from migration_factory.control_tower.domain.checksums import utc_now_text


class SqliteV1ModelProfileRepository:
    """SQLite repository for v1_model_profiles table."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def insert(self, profile: V1ModelProfileRecord) -> None:
        self._connection.execute(
            """INSERT INTO v1_model_profiles (
                profile_id, display_name, provider_kind,
                model_env_ref, endpoint_env_ref, deployment_env_ref,
                is_active, created_at, created_by
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                profile.profile_id,
                profile.display_name,
                profile.provider_kind,
                profile.model_env_ref,
                profile.endpoint_env_ref,
                profile.deployment_env_ref,
                1 if profile.is_active else 0,
                profile.created_at,
                profile.created_by,
            ),
        )

    def get(self, profile_id: str) -> V1ModelProfileRecord | None:
        row = self._connection.execute(
            """SELECT profile_id, display_name, provider_kind,
                      model_env_ref, endpoint_env_ref, deployment_env_ref,
                      is_active, created_at, created_by
               FROM v1_model_profiles WHERE profile_id = ?""",
            (profile_id,),
        ).fetchone()
        if row is None:
            return None
        return V1ModelProfileRecord(
            profile_id=str(row[0]),
            display_name=str(row[1]),
            provider_kind=str(row[2]),
            model_env_ref=str(row[3]),
            endpoint_env_ref=str(row[4]),
            deployment_env_ref=str(row[5]),
            is_active=bool(row[6]),
            created_at=str(row[7]),
            created_by=str(row[8]),
        )

    def list(self) -> tuple[V1ModelProfileRecord, ...]:
        rows = self._connection.execute(
            """SELECT profile_id, display_name, provider_kind,
                      model_env_ref, endpoint_env_ref, deployment_env_ref,
                      is_active, created_at, created_by
               FROM v1_model_profiles ORDER BY profile_id"""
        ).fetchall()
        return tuple(
            V1ModelProfileRecord(
                profile_id=str(r[0]),
                display_name=str(r[1]),
                provider_kind=str(r[2]),
                model_env_ref=str(r[3]),
                endpoint_env_ref=str(r[4]),
                deployment_env_ref=str(r[5]),
                is_active=bool(r[6]),
                created_at=str(r[7]),
                created_by=str(r[8]),
            )
            for r in rows
        )


class SqliteV1ModelProfileEventRepository:
    """SQLite repository for model profile registration events."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def insert_event(
        self,
        *,
        event_id: str,
        profile_id: str,
        event_type: str,
        provider_kind: str,
        actor_type: str,
        actor_id: str,
        payload_json: str,
        payload_checksum: str,
        created_at: str,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> None:
        self._connection.execute(
            """INSERT INTO v1_model_profile_events (
                event_id, profile_id, event_type, provider_kind,
                actor_type, actor_id, payload_json, payload_checksum,
                created_at, correlation_id, causation_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event_id,
                profile_id,
                event_type,
                provider_kind,
                actor_type,
                actor_id,
                payload_json,
                payload_checksum,
                created_at,
                correlation_id,
                causation_id,
            ),
        )
