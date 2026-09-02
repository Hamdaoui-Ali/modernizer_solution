from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import shlex
import subprocess
import time

from migration_factory.maven import resolve_maven_command


@dataclass(frozen=True)
class CommandResult:
    command: str
    exit_code: int
    stdout: list[str] = field(default_factory=list)
    stderr: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0


def run_command(command: str, cwd: Path, stream_output: bool = True) -> CommandResult:
    started = time.monotonic()
    args = _split_command(command)
    args = _resolve_executable(args)
    recorded_command = " ".join(args)

    try:
        process = subprocess.Popen(
            args,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as exc:
        return CommandResult(
            command=recorded_command,
            exit_code=127,
            stdout=[],
            stderr=[
                f"Executable not found: {args[0]}",
                str(exc),
                "Hint: set MAVEN_CMD to the full path of mvn.cmd, for example:",
                r"set MAVEN_CMD=C:\Tools\apache-maven-3.9.15\bin\mvn.cmd",
            ],
            duration_seconds=time.monotonic() - started,
        )

    stdout_text, stderr_text = process.communicate()
    stdout = stdout_text.splitlines()
    stderr = stderr_text.splitlines()

    if stream_output:
        for line in stdout:
            print(line)
        for line in stderr:
            print(line)

    return CommandResult(
        command=recorded_command,
        exit_code=process.returncode,
        stdout=stdout,
        stderr=stderr,
        duration_seconds=time.monotonic() - started,
    )


def _split_command(command: str) -> list[str]:
    return shlex.split(command, posix=False)


def _resolve_executable(args: list[str]) -> list[str]:
    return resolve_maven_command(args)
