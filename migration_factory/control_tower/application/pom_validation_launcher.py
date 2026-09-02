"""F14 — POM validation launcher.

Launches Maven build/test validation against the Stage 3 sandbox.
Supports both real subprocess execution and fake/test launchers.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class ValidationLaunchResult:
    """Result from a validation launch."""
    validation_id: str
    change_id: str
    exit_code: int
    stdout: str
    stderr: str
    duration_ms: int
    success: bool


class SubprocessValidationLauncher:
    """Launches Maven validation as a subprocess against the Stage 3 sandbox.

    Does NOT block the caller — runs validation in a background thread
    and invokes a callback when complete.

    Production path: runs `mvn clean compile test` in the sandbox.
    """

    def __init__(
        self,
        command: str = "mvn clean compile test",
        timeout_seconds: int = 300,
        callback: Callable[[ValidationLaunchResult], None] | None = None,
    ) -> None:
        self._command = command
        self._timeout = timeout_seconds
        self._callback = callback

    def __call__(
        self,
        change_id: str,
        validation_id: str,
        command: str,
        job_id: str,
        sandbox_path: str,
    ) -> None:
        """Launch validation in a background thread.

        Args:
            change_id: The POM change ID
            validation_id: The validation run ID
            command: Maven command to execute (e.g., 'mvn clean compile test')
            job_id: The V2 job ID
            sandbox_path: Path to Stage 3 sandbox (working directory)
        """
        import threading

        cmd_to_run = command or self._command

        def _run():
            start = time.monotonic()
            try:
                proc = subprocess.run(
                    cmd_to_run.split(),
                    cwd=sandbox_path,
                    capture_output=True,
                    text=True,
                    timeout=self._timeout,
                )
                duration_ms = int((time.monotonic() - start) * 1000)
                result = ValidationLaunchResult(
                    validation_id=validation_id,
                    change_id=change_id,
                    exit_code=proc.returncode,
                    stdout=proc.stdout or "",
                    stderr=proc.stderr or "",
                    duration_ms=duration_ms,
                    success=proc.returncode == 0,
                )
            except subprocess.TimeoutExpired:
                duration_ms = int((time.monotonic() - start) * 1000)
                result = ValidationLaunchResult(
                    validation_id=validation_id,
                    change_id=change_id,
                    exit_code=-1,
                    stdout="",
                    stderr=f"Validation timed out after {self._timeout}s",
                    duration_ms=duration_ms,
                    success=False,
                )
            except Exception as e:
                duration_ms = int((time.monotonic() - start) * 1000)
                result = ValidationLaunchResult(
                    validation_id=validation_id,
                    change_id=change_id,
                    exit_code=-1,
                    stdout="",
                    stderr=str(e),
                    duration_ms=duration_ms,
                    success=False,
                )

            if self._callback:
                self._callback(result)

        thread = threading.Thread(target=_run, daemon=True, name=f"f14-val-{validation_id[:8]}")
        thread.start()


class FakeValidationLauncher:
    """Test-only launcher that simulates validation completion.

    Invokes the callback synchronously with a predetermined result.
    """

    def __init__(
        self,
        exit_code: int = 0,
        stdout: str = "[INFO] BUILD SUCCESS",
        stderr: str = "",
        duration_ms: int = 100,
        callback: Callable[[ValidationLaunchResult], None] | None = None,
    ) -> None:
        self._exit_code = exit_code
        self._stdout = stdout
        self._stderr = stderr
        self._duration_ms = duration_ms
        self._callback = callback

    def __call__(
        self,
        change_id: str,
        validation_id: str,
        command: str,
        job_id: str,
        sandbox_path: str,
    ) -> None:
        """Simulate validation by invoking callback immediately."""
        result = ValidationLaunchResult(
            validation_id=validation_id,
            change_id=change_id,
            exit_code=self._exit_code,
            stdout=self._stdout,
            stderr=self._stderr,
            duration_ms=self._duration_ms,
            success=self._exit_code == 0,
        )
        if self._callback:
            self._callback(result)
