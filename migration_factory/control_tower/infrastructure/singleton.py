"""Local singleton/controller ownership primitives for Control Tower."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from typing import Protocol

from migration_factory.control_tower.domain.errors import (
    ControllerOwnershipConflictError,
    ControllerOwnershipReleaseError,
    ControllerOwnershipUnavailableError,
)


@dataclass(frozen=True, slots=True)
class ControllerOwnershipStatus:
    ready: bool
    status: str


class ControllerOwnership(Protocol):
    def acquire(self) -> None: ...
    def release(self) -> None: ...
    def snapshot(self) -> ControllerOwnershipStatus: ...

    @property
    def is_owned(self) -> bool: ...


class WindowsMutexControllerOwnership:
    def __init__(self, resource_path: Path | str) -> None:
        self._mutex_name = _mutex_name_for(resource_path)
        self._handle: int | None = None
        self._status = "not_acquired"

    @property
    def is_owned(self) -> bool:
        return self._handle is not None

    def acquire(self) -> None:
        if self._handle is not None:
            self._status = "owned"
            return

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.GetLastError.argtypes = []
        kernel32.GetLastError.restype = wintypes.DWORD

        ERROR_ALREADY_EXISTS = 183

        handle = kernel32.CreateMutexW(None, True, self._mutex_name)
        if not handle:
            self._status = "unavailable"
            raise ControllerOwnershipUnavailableError()

        if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            self._status = "conflict"
            raise ControllerOwnershipConflictError()

        self._handle = int(handle)
        self._status = "owned"

    def release(self) -> None:
        if self._handle is None:
            self._status = "released"
            return

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.ReleaseMutex.argtypes = [wintypes.HANDLE]
        kernel32.ReleaseMutex.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = self._handle
        self._handle = None
        release_ok = bool(kernel32.ReleaseMutex(handle))
        close_ok = bool(kernel32.CloseHandle(handle))
        self._status = "released"
        if not release_ok or not close_ok:
            self._status = "release_failed"
            raise ControllerOwnershipReleaseError()

    def snapshot(self) -> ControllerOwnershipStatus:
        return ControllerOwnershipStatus(ready=self.is_owned, status=self._status)


class LockFileControllerOwnership:
    def __init__(self, resource_path: Path | str) -> None:
        digest = _ownership_digest(resource_path)
        self._lock_path = Path(tempfile.gettempdir()) / f"control-tower-{digest}.lock"
        self._fd: int | None = None
        self._status = "not_acquired"

    @property
    def is_owned(self) -> bool:
        return self._fd is not None

    def acquire(self) -> None:
        if self._fd is not None:
            self._status = "owned"
            return

        flags = os.O_CREAT | os.O_EXCL | os.O_RDWR
        try:
            self._fd = os.open(str(self._lock_path), flags)
        except FileExistsError as exc:
            self._status = "conflict"
            raise ControllerOwnershipConflictError() from exc
        except OSError as exc:
            self._status = "unavailable"
            raise ControllerOwnershipUnavailableError() from exc
        self._status = "owned"

    def release(self) -> None:
        if self._fd is None:
            self._status = "released"
            return

        fd = self._fd
        self._fd = None
        try:
            os.close(fd)
            if self._lock_path.exists():
                self._lock_path.unlink()
        except OSError as exc:
            self._status = "release_failed"
            raise ControllerOwnershipReleaseError() from exc
        self._status = "released"

    def snapshot(self) -> ControllerOwnershipStatus:
        return ControllerOwnershipStatus(ready=self.is_owned, status=self._status)


class FakeControllerOwnership:
    def __init__(
        self,
        *,
        initially_owned: bool = False,
        raise_conflict: bool = False,
        raise_unavailable: bool = False,
    ) -> None:
        self._owned = initially_owned
        self._raise_conflict = raise_conflict
        self._raise_unavailable = raise_unavailable
        self._status = "owned" if initially_owned else "not_acquired"
        self.acquire_calls = 0
        self.release_calls = 0

    @property
    def is_owned(self) -> bool:
        return self._owned

    def acquire(self) -> None:
        self.acquire_calls += 1
        if self._owned:
            self._status = "owned"
            return
        if self._raise_unavailable:
            self._status = "unavailable"
            raise ControllerOwnershipUnavailableError()
        if self._raise_conflict:
            self._status = "conflict"
            raise ControllerOwnershipConflictError()
        self._owned = True
        self._status = "owned"

    def release(self) -> None:
        self.release_calls += 1
        self._owned = False
        self._status = "released"

    def snapshot(self) -> ControllerOwnershipStatus:
        return ControllerOwnershipStatus(ready=self._owned, status=self._status)


def create_controller_ownership(
    resource_path: Path | str,
    *,
    platform: str | None = None,
) -> ControllerOwnership:
    current_platform = platform or sys.platform
    if current_platform.startswith("win"):
        return WindowsMutexControllerOwnership(resource_path)
    return LockFileControllerOwnership(resource_path)


def controller_resource_path_from_unit_of_work_factory(unit_of_work_factory) -> Path | str:
    uow = unit_of_work_factory()
    connection = getattr(uow, "connection", None)
    if not isinstance(connection, sqlite3.Connection):
        return "control-tower-default"
    row = connection.execute("PRAGMA database_list").fetchone()
    if row is None:
        return "control-tower-default"
    file_name = row[2] if not isinstance(row, sqlite3.Row) else row["file"]
    if not file_name:
        return f"sqlite-memory-{id(connection)}"
    return Path(str(file_name))


def _normalized_resource_string(resource_path: Path | str) -> str:
    path = Path(resource_path).expanduser() if not isinstance(resource_path, Path) else resource_path.expanduser()
    normalized = str(path.resolve(strict=False))
    if sys.platform.startswith("win"):
        normalized = os.path.normcase(normalized)
    return normalized.replace("\\", "/")


def _ownership_digest(resource_path: Path | str) -> str:
    normalized = _normalized_resource_string(resource_path)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _mutex_name_for(resource_path: Path | str) -> str:
    return f"Local\\AI_Migration_Control_Tower_{_ownership_digest(resource_path)}"
