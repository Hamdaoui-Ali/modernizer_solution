from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re


class BuildResultKind(str, Enum):
    SUCCESS = "success"
    COMPILATION_ERROR = "compilation_error"
    DEPENDENCY_ERROR = "dependency_error"
    REPOSITORY_TLS_FAILURE = "repository_tls_failure"
    JAVA_VERSION_MISMATCH = "java_version_mismatch"
    JAVA_RUNTIME_MISMATCH = "java_runtime_mismatch"
    PORT_IN_USE = "port_already_in_use"
    MAIN_CLASS_NOT_FOUND = "main_class_not_found"
    MISSING_CONFIG = "missing_config"
    PROCESS_EXITED = "process_exited"
    COMMAND_ERROR = "command_error"
    TIMEOUT = "timeout"
    UNKNOWN_FAILURE = "unknown_failure"


@dataclass(frozen=True)
class BuildClassification:
    kind: BuildResultKind
    message: str
    line: str | None = None


SUCCESS_PATTERNS = (
    "Started ",
    "Tomcat started on port",
    "Netty started on port",
    "Started Application in ",
)

FAILURE_PATTERNS: tuple[tuple[BuildResultKind, tuple[str, ...], str], ...] = (
    (
        BuildResultKind.COMPILATION_ERROR,
        (
            "COMPILATION ERROR",
            "Compilation failure",
            "Compilation failed",
            "Execution failed for task ':compile",
            "cannot find symbol",
            "package does not exist",
            "Could not compile",
            "Failed to execute goal org.apache.maven.plugins:maven-compiler-plugin",
        ),
        "Java application failed to compile",
    ),
    (
        BuildResultKind.REPOSITORY_TLS_FAILURE,
        (
            "PKIX path building failed",
            "certificate_unknown",
            "SunCertPathBuilderException",
            "unable to find valid certification path",
            "SSLHandshakeException",
            "CertificateException",
        ),
        "Repository TLS certificate trust failure — environment/infrastructure issue",
    ),
    (
        BuildResultKind.DEPENDENCY_ERROR,
        (
            "Could not resolve dependencies",
            "Could not find artifact",
            "Could not transfer artifact",
            "Could not resolve all files",
            "Could not resolve all dependencies",
            "PluginResolutionException",
        ),
        "Java application dependency resolution failed",
    ),
    (
        BuildResultKind.JAVA_VERSION_MISMATCH,
        (
            "UnsupportedClassVersionError",
            "has been compiled by a more recent version of the Java Runtime",
            "invalid source release",
            "invalid target release",
            "release version",
            "Unsupported class file major version",
        ),
        "Java version mismatch",
    ),
    (
        BuildResultKind.PORT_IN_USE,
        (
            "Web server failed to start. Port",
            "Address already in use",
            "BindException",
            "port is already in use",
            "port already in use",
        ),
        "Application port is already in use",
    ),
    (
        BuildResultKind.MAIN_CLASS_NOT_FOUND,
        (
            "Unable to find a suitable main class",
            "please add a 'mainClass' property",
            "no main manifest attribute",
        ),
        "Spring Boot main class was not found",
    ),
    (
        BuildResultKind.MISSING_CONFIG,
        (
            "Could not resolve placeholder",
            "Failed to bind properties",
            "No qualifying bean of type",
            "UnsatisfiedDependencyException",
            "BeanCreationException",
            "PropertyReferenceException",
        ),
        "Application configuration is missing or invalid",
    ),
)


def classify_line(line: str) -> BuildClassification | None:
    normalized = line.strip()
    if not normalized:
        return None

    enforcer = _classify_maven_enforcer_java_version(normalized)
    if enforcer is not None:
        return enforcer

    for pattern in SUCCESS_PATTERNS:
        if pattern in normalized:
            return BuildClassification(BuildResultKind.SUCCESS, "Application started successfully", normalized)

    for kind, patterns, message in FAILURE_PATTERNS:
        if _matches(normalized, patterns):
            return BuildClassification(kind, message, normalized)

    return None


def _classify_maven_enforcer_java_version(line: str) -> BuildClassification | None:
    match = re.search(
        r"Detected JDK version\s+([^\s]+)\s+is not in allowed range\s+(\[[^\]]+\]|\([^)]+\)|[^\s]+)",
        line,
        re.IGNORECASE,
    )
    if match:
        detected, expected = match.groups()
        return BuildClassification(
            BuildResultKind.JAVA_RUNTIME_MISMATCH,
            f"Java runtime mismatch: detected {detected}, expected {expected}",
            line,
        )
    if "RequireJavaVersion" in line and "allowed range" in line:
        return BuildClassification(
            BuildResultKind.JAVA_RUNTIME_MISMATCH,
            "Java runtime mismatch from Maven Enforcer RequireJavaVersion",
            line,
        )
    return None


def process_exit_classification(exit_code: int) -> BuildClassification:
    return BuildClassification(
        BuildResultKind.PROCESS_EXITED,
        f"Process exited before startup was detected with exit code {exit_code}",
    )


def command_error_classification(message: str, line: str | None = None) -> BuildClassification:
    return BuildClassification(BuildResultKind.COMMAND_ERROR, message, line)


def timeout_classification(seconds: int) -> BuildClassification:
    return BuildClassification(
        BuildResultKind.TIMEOUT,
        f"Timed out after {seconds} seconds before startup was detected",
    )


def command_timeout_classification(seconds: int) -> BuildClassification:
    return BuildClassification(
        BuildResultKind.TIMEOUT,
        f"Command timed out after {seconds} seconds before completion",
    )


def unknown_failure_classification(exit_code: int | None) -> BuildClassification:
    if exit_code is None:
        return BuildClassification(BuildResultKind.UNKNOWN_FAILURE, "Application failed before startup was detected")
    return BuildClassification(BuildResultKind.UNKNOWN_FAILURE, f"Application failed with exit code {exit_code}")


def _matches(line: str, patterns: tuple[str, ...]) -> bool:
    lower = line.lower()
    return any(pattern.lower() in lower for pattern in patterns)
