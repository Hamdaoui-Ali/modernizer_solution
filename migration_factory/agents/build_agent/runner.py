from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO
import os
import platform
import queue
import signal
import subprocess
import threading
import time

from migration_factory.maven import resolve_maven_command

from .classifier import (
    BuildClassification,
    BuildResultKind,
    classify_line,
    command_timeout_classification,
    command_error_classification,
    process_exit_classification,
    timeout_classification,
    unknown_failure_classification,
)


TERMINATION_GRACE_SECONDS = 5


@dataclass(frozen=True)
class ProcessRunResult:
    classification: BuildClassification
    exit_code: int | None
    stdout: list[str] = field(default_factory=list)
    stderr: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    requested_command: list[str] = field(default_factory=list)
    resolved_command: list[str] = field(default_factory=list)
    diagnostics: dict[str, str | None] = field(default_factory=dict)

    @property
    def succeeded(self) -> bool:
        return self.classification.kind == BuildResultKind.SUCCESS


def run_until_build_result(
    command: list[str],
    cwd: Path,
    timeout_seconds: int,
    stream_output: bool = True,
    stop_after_start: bool = True,
    on_startup_result: Callable[[BuildClassification], None] | None = None,
    env: dict[str, str] | None = None,
) -> ProcessRunResult:
    requested_command = list(command)
    command = resolve_command(command, env=env)
    diagnostics = command_diagnostics(requested_command, command, env=env)
    try:
        popen_kwargs: dict[str, object] = {}
        if os.name == "nt":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            popen_kwargs["start_new_session"] = True

        process = subprocess.Popen(
            command,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            universal_newlines=True,
            env=env,
            **popen_kwargs,
        )
    except FileNotFoundError as exc:
        command_name = requested_command[0] if requested_command else "<empty command>"
        return ProcessRunResult(
            command_error_classification(f"Command not found: {command_name}", str(exc)),
            None,
            requested_command=requested_command,
            resolved_command=command,
            diagnostics=diagnostics,
        )
    except PermissionError as exc:
        command_name = requested_command[0] if requested_command else "<empty command>"
        return ProcessRunResult(
            command_error_classification(f"Command is not executable: {command_name}", str(exc)),
            None,
            requested_command=requested_command,
            resolved_command=command,
            diagnostics=diagnostics,
        )

    output_queue: queue.Queue[tuple[str, str | None]] = queue.Queue()
    stdout: list[str] = []
    stderr: list[str] = []

    threads = [
        threading.Thread(target=_enqueue_lines, args=("stdout", process.stdout, output_queue), daemon=True),
        threading.Thread(target=_enqueue_lines, args=("stderr", process.stderr, output_queue), daemon=True),
    ]
    for thread in threads:
        thread.start()

    deadline = time.monotonic() + timeout_seconds
    last_failure: BuildClassification | None = None
    startup_success: BuildClassification | None = None

    try:
        while True:
            if startup_success is None and time.monotonic() >= deadline:
                termination = _terminate_process_tree(process)
                return ProcessRunResult(
                    timeout_classification(timeout_seconds),
                    process.poll(),
                    stdout,
                    stderr,
                    termination.warnings,
                    requested_command,
                    command,
                    diagnostics,
                )

            exit_code = process.poll()
            if exit_code is not None:
                _drain_queue(output_queue, stdout, stderr, stream_output)
                _close_process_streams(process)
                if startup_success is not None:
                    return ProcessRunResult(
                        startup_success,
                        exit_code,
                        stdout,
                        stderr,
                        requested_command=requested_command,
                        resolved_command=command,
                        diagnostics=diagnostics,
                    )
                if last_failure is not None:
                    return ProcessRunResult(
                        last_failure,
                        exit_code,
                        stdout,
                        stderr,
                        requested_command=requested_command,
                        resolved_command=command,
                        diagnostics=diagnostics,
                    )
                return ProcessRunResult(
                    process_exit_classification(exit_code),
                    exit_code,
                    stdout,
                    stderr,
                    requested_command=requested_command,
                    resolved_command=command,
                    diagnostics=diagnostics,
                )

            try:
                source, line = output_queue.get(timeout=0.1)
            except queue.Empty:
                continue

            if line is None:
                continue

            _record_line(source, line, stdout, stderr, stream_output)
            classification = classify_line(line)
            if classification is None or startup_success is not None:
                continue

            if classification.kind == BuildResultKind.SUCCESS:
                startup_success = classification
                if on_startup_result is not None:
                    on_startup_result(classification)
                if stop_after_start:
                    termination = _terminate_process_tree(process)
                    return ProcessRunResult(
                        classification,
                        process.poll(),
                        stdout,
                        stderr,
                        termination.warnings,
                        requested_command,
                        command,
                        diagnostics,
                    )
                continue

            last_failure = classification
            termination = _terminate_process_tree(process)
            return ProcessRunResult(
                classification,
                process.poll(),
                stdout,
                stderr,
                termination.warnings,
                requested_command,
                command,
                diagnostics,
            )
    except KeyboardInterrupt:
        termination = _terminate_process_tree(process)
        if startup_success is not None:
            return ProcessRunResult(
                startup_success,
                process.poll(),
                stdout,
                stderr,
                termination.warnings,
                requested_command,
                command,
                diagnostics,
            )
        return ProcessRunResult(
            unknown_failure_classification(process.poll()),
            process.poll(),
            stdout,
            stderr,
            termination.warnings,
            requested_command,
            command,
            diagnostics,
        )


def run_until_exit(
    command: list[str],
    cwd: Path,
    timeout_seconds: int,
    stream_output: bool = True,
    env: dict[str, str] | None = None,
) -> ProcessRunResult:
    requested_command = list(command)
    command = resolve_command(command, env=env)
    diagnostics = command_diagnostics(requested_command, command, env=env)
    try:
        process = subprocess.Popen(
            command,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            universal_newlines=True,
            env=env,
        )
    except FileNotFoundError as exc:
        command_name = requested_command[0] if requested_command else "<empty command>"
        return ProcessRunResult(
            command_error_classification(f"Command not found: {command_name}", str(exc)),
            None,
            requested_command=requested_command,
            resolved_command=command,
            diagnostics=diagnostics,
        )
    except PermissionError as exc:
        command_name = requested_command[0] if requested_command else "<empty command>"
        return ProcessRunResult(
            command_error_classification(f"Command is not executable: {command_name}", str(exc)),
            None,
            requested_command=requested_command,
            resolved_command=command,
            diagnostics=diagnostics,
        )

    output_queue: queue.Queue[tuple[str, str | None]] = queue.Queue()
    stdout: list[str] = []
    stderr: list[str] = []
    threads = [
        threading.Thread(target=_enqueue_lines, args=("stdout", process.stdout, output_queue), daemon=True),
        threading.Thread(target=_enqueue_lines, args=("stderr", process.stderr, output_queue), daemon=True),
    ]
    for thread in threads:
        thread.start()

    deadline = time.monotonic() + timeout_seconds
    last_failure: BuildClassification | None = None

    while True:
        if time.monotonic() >= deadline:
            termination = _terminate_process_tree(process)
            return ProcessRunResult(
                command_timeout_classification(timeout_seconds),
                process.poll(),
                stdout,
                stderr,
                termination.warnings,
                requested_command,
                command,
                diagnostics,
            )

        exit_code = process.poll()
        if exit_code is not None:
            _drain_queue(output_queue, stdout, stderr, stream_output)
            _close_process_streams(process)
            if exit_code == 0:
                return ProcessRunResult(
                    BuildClassification(BuildResultKind.SUCCESS, "Build completed successfully"),
                    exit_code,
                    stdout,
                    stderr,
                    requested_command=requested_command,
                    resolved_command=command,
                    diagnostics=diagnostics,
                )
            if last_failure is not None:
                return ProcessRunResult(
                    last_failure,
                    exit_code,
                    stdout,
                    stderr,
                    requested_command=requested_command,
                    resolved_command=command,
                    diagnostics=diagnostics,
                )
            return ProcessRunResult(
                unknown_failure_classification(exit_code),
                exit_code,
                stdout,
                stderr,
                requested_command=requested_command,
                resolved_command=command,
                diagnostics=diagnostics,
            )

        try:
            source, line = output_queue.get(timeout=0.1)
        except queue.Empty:
            continue
        if line is None:
            continue

        _record_line(source, line, stdout, stderr, stream_output)
        classification = classify_line(line)
        if classification is not None and classification.kind != BuildResultKind.SUCCESS:
            last_failure = classification


def resolve_command(command: list[str], env: dict[str, str] | None = None) -> list[str]:
    return resolve_maven_command(command, env=env)


def command_diagnostics(
    requested_command: list[str],
    resolved_command: list[str],
    env: dict[str, str] | None = None,
) -> dict[str, str | None]:
    effective_env = _effective_env(env)
    path_value = effective_env.get("PATH")
    return {
        "requested_command": " ".join(requested_command),
        "resolved_command": " ".join(resolved_command),
        "maven_cmd": effective_env.get("MAVEN_CMD"),
        "maven_home": effective_env.get("MAVEN_HOME"),
        "effective_java_home": effective_env.get("JAVA_HOME"),
        "PATH_excerpt": _path_excerpt(path_value),
        "platform": platform.platform(),
    }


def _effective_env(env: dict[str, str] | None) -> dict[str, str]:
    if env is None:
        return os.environ.copy()
    merged = os.environ.copy()
    merged.update(env)
    return merged


def _path_excerpt(path_value: str | None, *, max_chars: int = 500) -> str | None:
    if path_value is None or len(path_value) <= max_chars:
        return path_value
    return path_value[:max_chars] + "...[truncated]"


def _enqueue_lines(source: str, stream: TextIO | None, output_queue: queue.Queue[tuple[str, str | None]]) -> None:
    if stream is None:
        output_queue.put((source, None))
        return

    try:
        for line in stream:
            output_queue.put((source, line.rstrip("\n")))
    finally:
        output_queue.put((source, None))


def _drain_queue(
    output_queue: queue.Queue[tuple[str, str | None]],
    stdout: list[str],
    stderr: list[str],
    stream_output: bool,
) -> None:
    while True:
        try:
            source, line = output_queue.get_nowait()
        except queue.Empty:
            return
        if line is not None:
            _record_line(source, line, stdout, stderr, stream_output)


def _record_line(source: str, line: str, stdout: list[str], stderr: list[str], stream_output: bool) -> None:
    if source == "stderr":
        stderr.append(line)
    else:
        stdout.append(line)

    if stream_output:
        print(line, flush=True)


@dataclass(frozen=True)
class TerminationResult:
    warnings: list[str] = field(default_factory=list)


def _terminate_process_tree(process: subprocess.Popen[str]) -> TerminationResult:
    warnings: list[str] = []
    if process.poll() is not None:
        _close_process_streams(process)
        return TerminationResult(warnings)

    if os.name == "nt":
        clean_signal_sent = _terminate_windows_tree(process, force=False)
    else:
        clean_signal_sent = _terminate_unix_tree(process, force=False)
    if not clean_signal_sent:
        warnings.append("Process tree required force termination after startup validation.")
        if os.name == "nt":
            _terminate_windows_tree(process, force=True)
        else:
            _terminate_unix_tree(process, force=True)
    try:
        process.wait(timeout=TERMINATION_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        if not warnings:
            warnings.append("Process tree required force termination after startup validation.")
        if os.name == "nt":
            _terminate_windows_tree(process, force=True)
        else:
            _terminate_unix_tree(process, force=True)
        try:
            process.wait(timeout=TERMINATION_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            warnings.append("Process tree did not terminate cleanly within force-termination grace period.")
    finally:
        _close_process_streams(process)
    return TerminationResult(warnings)


def _terminate(process: subprocess.Popen[str]) -> TerminationResult:
    return _terminate_process_tree(process)


def _terminate_windows_tree(process: subprocess.Popen[str], *, force: bool) -> bool:
    command = ["taskkill", "/T", "/PID", str(process.pid)]
    if force:
        command.insert(1, "/F")
    try:
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        return result.returncode == 0
    except OSError:
        if force:
            process.kill()
        else:
            process.terminate()
        return True


def _terminate_unix_tree(process: subprocess.Popen[str], *, force: bool) -> bool:
    signum = signal.SIGKILL if force else signal.SIGTERM
    try:
        os.killpg(process.pid, signum)
        return True
    except ProcessLookupError:
        return True
    except OSError:
        if force:
            process.kill()
        else:
            process.terminate()
        return True


def _close_process_streams(process: subprocess.Popen[str]) -> None:
    _close_stream(process.stdout)
    _close_stream(process.stderr)


def _close_stream(stream: TextIO | None) -> None:
    if stream is not None and not stream.closed:
        stream.close()
