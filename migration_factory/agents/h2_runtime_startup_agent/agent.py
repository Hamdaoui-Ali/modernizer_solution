from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from migration_factory.agents.build_agent.runner import run_until_build_result
from migration_factory.contracts import SCHEMA_VERSION
from migration_factory.maven import resolve_maven_command


H2_LIMITATIONS = [
    "SQL Server not validated",
    "production DB not validated",
    "endpoint behavior not validated",
]
H2_CONFIG = """spring.datasource.url=jdbc:h2:mem:migration_smoke;MODE=LEGACY;DB_CLOSE_DELAY=-1
spring.datasource.driver-class-name=org.h2.Driver
spring.datasource.username=sa
spring.datasource.password=
spring.sql.init.mode=never
spring.flyway.enabled=false
spring.liquibase.enabled=false
spring.jpa.hibernate.ddl-auto=none
"""
FAILURE_PATTERNS = (
    "APPLICATION FAILED TO START",
    "ClassNotFoundException",
    "NoClassDefFoundError",
    "BeanCreationException",
    "missing h2",
)
SECURITY_WARNING_PATTERNS = ("keystore", "jwt", "secret", "certificate", "private key")


def write_h2_config(run_dir: str | Path) -> Path:
    config_path = Path(run_dir) / "runtime" / "h2" / "application-h2-smoke.properties"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(H2_CONFIG, encoding="utf-8")
    return config_path


def build_h2_startup_report(
    *,
    run_id: str,
    run_dir: str | Path,
    sandbox_path: str | Path,
    required: bool = False,
    timeout_seconds: int = 120,
    runner=run_until_build_result,
) -> dict[str, Any]:
    config_path = write_h2_config(run_dir)
    command = [
        "mvn",
        "spring-boot:run",
        f"-Dspring-boot.run.arguments=--spring.config.additional-location=file:{config_path.resolve().as_posix()}",
    ]
    resolved_command = resolve_maven_command(command)
    try:
        result = runner(
            command=command,
            cwd=Path(sandbox_path),
            timeout_seconds=timeout_seconds,
            stream_output=False,
            stop_after_start=True,
        )
        resolved_command = list(getattr(result, "resolved_command", []) or resolved_command)
        stdout = list(getattr(result, "stdout", []) or [])
        stderr = list(getattr(result, "stderr", []) or [])
        text = "\n".join([*stdout, *stderr])
        startup_observed = "Started " in text or "Tomcat started on port" in text or bool(getattr(result, "succeeded", False))
        failure_patterns = [pattern for pattern in FAILURE_PATTERNS if pattern.lower() in text.lower()]
        security_warnings = [line for line in [*stdout, *stderr] if any(token in line.lower() for token in SECURITY_WARNING_PATTERNS)]
        if startup_observed and not failure_patterns:
            status = "H2_STARTUP_WARNING" if security_warnings else "H2_STARTUP_PASSED"
            proof = "h2_runtime_started"
        else:
            status = "H2_STARTUP_FAILED" if required else "H2_STARTUP_WARNING"
            proof = "not_verified"
    except Exception as exc:
        stdout = []
        stderr = [str(exc)]
        text = str(exc)
        startup_observed = False
        failure_patterns = [str(exc)]
        security_warnings = []
        status = "H2_STARTUP_FAILED" if required else "H2_STARTUP_WARNING"
        proof = "not_verified"

    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "h2_status": status,
        "required": required,
        "proof_level": proof,
        "command": resolved_command,
        "requested_command": command,
        "cwd": str(Path(sandbox_path)),
        "java_version": _java_version(),
        "maven_command": resolved_command[0] if resolved_command else "",
        "config_path": str(config_path),
        "startup_observed": startup_observed,
        "failure_patterns": failure_patterns,
        "security_env_warnings": security_warnings,
        "runtime_config_findings": _runtime_config_findings(text),
        "stdout_tail": _tail(stdout),
        "stderr_tail": _tail(stderr),
        "limitations": H2_LIMITATIONS,
    }


def write_h2_startup_report(*, run_dir: str | Path, report: dict[str, Any]) -> Path:
    path = Path(run_dir) / "runtime" / "h2_startup_report.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _tail(lines: list[str], max_chars: int = 2000) -> str:
    text = "\n".join(lines[-80:])
    return text[-max_chars:] if len(text) > max_chars else text


def _java_version() -> str:
    try:
        result = subprocess.run(
            ["java", "-version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    text = result.stderr or result.stdout or ""
    return text.strip().splitlines()[0] if text.strip() else ""


def _runtime_config_findings(text: str) -> list[dict[str, str]]:
    lowered = text.lower()
    findings: list[dict[str, str]] = []
    if (
        "beancreationexception" in lowered
        and "cachingconfig" in lowered
        and "properties.get(object) returned null" in lowered
    ):
        findings.append(
            {
                "type": "RUNTIME_CONFIG_MISSING_PROPERTY",
                "root_cause": "missing runtime config property for cachingConfig",
                "property_key": "unknown",
            }
        )
    return findings
