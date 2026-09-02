from __future__ import annotations

from pathlib import Path

from migration_factory.control_tower.infrastructure.paths import (
    resolve_control_tower_db_path,
)


def test_windows_default_path_uses_localappdata(tmp_path: Path) -> None:
    local_app_data = tmp_path / "LocalAppData"

    resolved = resolve_control_tower_db_path(
        environ={"LOCALAPPDATA": str(local_app_data)},
        platform="win32",
        home=tmp_path / "ignored-home",
    )

    assert resolved == (
        local_app_data / "AI-Migration-Control-Tower" / "control_tower.sqlite3"
    ).resolve()
    assert resolved.parent.is_dir()


def test_explicit_db_path_override_wins(tmp_path: Path) -> None:
    explicit = tmp_path / "custom" / "control.db"

    resolved = resolve_control_tower_db_path(
        explicit,
        environ={
            "CONTROL_TOWER_DB_PATH": str(tmp_path / "env" / "db.sqlite3"),
            "LOCALAPPDATA": str(tmp_path / "LocalAppData"),
            "XDG_STATE_HOME": str(tmp_path / "xdg"),
        },
        platform="win32",
        home=tmp_path / "home",
    )

    assert resolved == explicit.resolve()
    assert resolved.parent.is_dir()


def test_control_tower_db_path_env_override_wins(tmp_path: Path) -> None:
    env_db_path = tmp_path / "env" / "state.db"

    resolved = resolve_control_tower_db_path(
        environ={
            "CONTROL_TOWER_DB_PATH": str(env_db_path),
            "LOCALAPPDATA": str(tmp_path / "LocalAppData"),
            "XDG_STATE_HOME": str(tmp_path / "xdg"),
        },
        platform="win32",
        home=tmp_path / "home",
    )

    assert resolved == env_db_path.resolve()
    assert resolved.parent.is_dir()
