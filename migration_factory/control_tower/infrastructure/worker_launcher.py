"""Worker launcher implementations for controlled diagnostic worker launch."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any
from uuid import uuid4

from migration_factory.control_tower.application.dto import WorkerLaunchResult
from migration_factory.control_tower.domain.checksums import utc_now_text
from migration_factory.control_tower.domain.errors import UnsupportedPlatformError
from migration_factory.control_tower.domain.manifests import CommandManifest, verify_manifest_checksum


class WindowsWorkerLauncher:
    def launch(
        self,
        *,
        working_dir: Path,
        manifest: CommandManifest,
        manifest_bytes: bytes,
        python_executable: str,
    ) -> WorkerLaunchResult:
        verify_manifest_checksum(manifest)

        process_control_id = str(uuid4())
        process_started_at = utc_now_text()

        manifest_path = (
            working_dir / "control" / "commands" / manifest.command_id / "command_manifest.json"
        )

        python_executable_path = Path(python_executable).expanduser()
        if not python_executable_path.is_file():
            python_executable_path = Path(python_executable)
        if not python_executable_path.is_file():
            raise FileNotFoundError(f"Python executable not found: {python_executable}")

        diagnostic_script = str(
            working_dir / "control" / "commands" / manifest.command_id / "diagnostic_worker.py"
        )

        env = {
            "COMMAND_MANIFEST_PATH": str(manifest_path),
            "PROCESS_CONTROL_ID": process_control_id,
            "PATH": os.environ.get("PATH", ""),
        }

        args = [str(python_executable_path), "-c", _DIAGNOSTIC_WORKER_SOURCE]

        CREATE_SUSPENDED = 0x00000004
        CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        creation_flags = CREATE_SUSPENDED | CREATE_NO_WINDOW
        startup_info = subprocess.STARTUPINFO()
        startup_info.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startup_info.wShowWindow = subprocess.SW_HIDE

        import _winapi
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.ResumeThread.argtypes = [wintypes.HANDLE]
        kernel32.ResumeThread.restype = wintypes.DWORD
        kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.TerminateProcess.restype = wintypes.BOOL

        process_handle = None
        thread_handle = None
        job_handle = None
        worker_pid = 0
        try:
            process_handle, thread_handle, worker_pid, _thread_id = _winapi.CreateProcess(
                str(python_executable_path),
                subprocess.list2cmdline(args),
                None,
                None,
                False,
                creation_flags,
                env,
                str(working_dir),
                startup_info,
            )

            job_handle = _assign_to_job_object(worker_pid)
            if kernel32.ResumeThread(thread_handle) == 0xFFFFFFFF:
                raise ctypes.WinError(ctypes.get_last_error())

            if job_handle:
                _close_job_when_process_exits(process_handle, job_handle)
                process_handle = None
                job_handle = None
        except Exception:
            if process_handle:
                kernel32.TerminateProcess(process_handle, 1)
            if job_handle:
                _winapi.CloseHandle(job_handle)
            raise
        finally:
            if thread_handle:
                _winapi.CloseHandle(thread_handle)
            if process_handle:
                _winapi.CloseHandle(process_handle)

        return WorkerLaunchResult(
            command_id=manifest.command_id,
            job_id=manifest.job_id,
            process_control_id=process_control_id,
            worker_pid=worker_pid,
            process_started_at=process_started_at,
            worker_id=manifest.worker_id,
            launch_attempt=1,
        )


def _assign_to_job_object(pid: int) -> int | None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        wintypes.INT,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.IsProcessInJob.argtypes = [
        wintypes.HANDLE,
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.BOOL),
    ]
    kernel32.IsProcessInJob.restype = wintypes.BOOL

    ERROR_INVALID_PARAMETER = 87
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    PROCESS_TERMINATE = 0x0001
    PROCESS_SET_QUOTA = 0x0100
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
            ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", wintypes.ULARGE_INTEGER),
            ("WriteOperationCount", wintypes.ULARGE_INTEGER),
            ("OtherOperationCount", wintypes.ULARGE_INTEGER),
            ("ReadTransferCount", wintypes.ULARGE_INTEGER),
            ("WriteTransferCount", wintypes.ULARGE_INTEGER),
            ("OtherTransferCount", wintypes.ULARGE_INTEGER),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    job_object = kernel32.CreateJobObjectW(None, None)
    if not job_object:
        raise ctypes.WinError(ctypes.get_last_error())

    limits = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE

    result = kernel32.SetInformationJobObject(
        job_object,
        9,
        ctypes.byref(limits),
        ctypes.sizeof(limits),
    )
    if not result:
        error = ctypes.get_last_error()
        kernel32.CloseHandle(job_object)
        raise ctypes.WinError(error)

    process_handle = kernel32.OpenProcess(
        PROCESS_TERMINATE | PROCESS_SET_QUOTA | PROCESS_QUERY_LIMITED_INFORMATION,
        False,
        pid,
    )
    if not process_handle:
        error = ctypes.get_last_error()
        kernel32.CloseHandle(job_object)
        raise ctypes.WinError(error)

    result = kernel32.AssignProcessToJobObject(job_object, process_handle)
    if not result:
        error = ctypes.get_last_error()
        in_job = wintypes.BOOL(False)
        process_already_in_job = bool(
            kernel32.IsProcessInJob(process_handle, None, ctypes.byref(in_job))
            and in_job.value
        )
        kernel32.CloseHandle(process_handle)
        kernel32.CloseHandle(job_object)
        if error == ERROR_INVALID_PARAMETER and process_already_in_job:
            return None
        raise ctypes.WinError(error)

    kernel32.CloseHandle(process_handle)
    return job_object


def _close_job_when_process_exits(process_handle: int, job_handle: int) -> None:
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    def close_after_exit() -> None:
        kernel32.WaitForSingleObject(process_handle, 0xFFFFFFFF)
        kernel32.CloseHandle(job_handle)
        kernel32.CloseHandle(process_handle)

    threading.Thread(target=close_after_exit, daemon=True).start()


class UnsupportedPlatformWorkerLauncher:
    def launch(
        self,
        *,
        working_dir: Path,
        manifest: CommandManifest,
        manifest_bytes: bytes,
        python_executable: str,
    ) -> WorkerLaunchResult:
        raise UnsupportedPlatformError(sys.platform)


class StubWorkerTerminator:
    """Stub terminator for tests and non-Windows platforms.

    Tracks calls for test assertions without real process termination.
    """

    def __init__(self) -> None:
        self.terminate_calls: list[dict[str, Any]] = []
        self._should_succeed: bool = True

    def terminate(
        self,
        *,
        worker_pid: int,
        process_control_id: str | None = None,
        grace_period_seconds: float = 5.0,
    ) -> bool:
        self.terminate_calls.append({
            "worker_pid": worker_pid,
            "process_control_id": process_control_id,
            "grace_period_seconds": grace_period_seconds,
        })
        return self._should_succeed

    def set_should_succeed(self, value: bool) -> None:
        self._should_succeed = value


class PosixWorkerTerminator:
    """POSIX worker terminator with SIGTERM cooperative stop.

    Sends SIGTERM, waits grace period, then sends SIGKILL if still alive.
    """

    def terminate(
        self,
        *,
        worker_pid: int,
        process_control_id: str | None = None,
        grace_period_seconds: float = 5.0,
    ) -> bool:
        import os as _os
        import time as _time
        import signal as _signal

        try:
            _os.kill(worker_pid, _signal.SIGTERM)
        except OSError:
            return True  # Already dead

        # Wait grace period in small increments
        deadline = _time.monotonic() + grace_period_seconds
        while _time.monotonic() < deadline:
            try:
                pid_result, _ = _os.waitpid(worker_pid, _os.WNOHANG)
                if pid_result == worker_pid or pid_result != 0:
                    return True  # Exited during grace period
            except ChildProcessError:
                return True
            _time.sleep(0.1)

        # Grace period expired, force kill
        try:
            _os.kill(worker_pid, _signal.SIGKILL)
            _os.waitpid(worker_pid, 0)
            return True
        except OSError:
            return True  # Already dead


_DIAGNOSTIC_WORKER_SOURCE = r"""import json, os, sys, time
manifest_path = os.environ.get('COMMAND_MANIFEST_PATH', '')
process_control_id = os.environ.get('PROCESS_CONTROL_ID', '')
sys.stdout.write(json.dumps({"status":"started","process_control_id":process_control_id,"manifest_path":manifest_path}))
sys.stdout.flush()
time.sleep(0.5)
sys.stdout.write(json.dumps({"status":"completed","process_control_id":process_control_id}))
"""
