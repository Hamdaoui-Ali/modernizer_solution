"""Local-only FastAPI security and readiness settings for Control Tower."""

from __future__ import annotations

from dataclasses import dataclass
import getpass
import os
from pathlib import Path
import re
import sys
from typing import Any, Protocol
from urllib.parse import urlparse
from uuid import uuid4


MUTATION_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
DEFAULT_FRONTEND_CLIENT_ID = "control-tower-frontend"
_WINDOWS_ABSOLUTE_PATH_RE = re.compile(r"(?<![A-Za-z]:)(?<![A-Za-z])[A-Za-z]:[\\/](?:[^\\/\s:]*[\\/])*[^\\/\s:]*")
_POSIX_ABSOLUTE_PATH_RE = re.compile(r"(?<![A-Za-z0-9_/<])/(?:[^/\s]+/)*[^/\s]*")
_ENV_ASSIGNMENT_RE = re.compile(r"\b[A-Z][A-Z0-9_]{1,}=[^\s]+")
_SECRET_KEY_RE = re.compile(r"(secret|token|password|credential|api[_-]?key)", re.IGNORECASE)
_PID_RE = re.compile(r"\bpid\b|\bprocess[_-]?id\b|\bhandle\b", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class ActorIdentity:
    actor_type: str
    actor_id: str


class ActorProvider(Protocol):
    def current_actor(self) -> ActorIdentity: ...


class OperatingSystemActorProvider:
    """Derive local operator identity from OS account running the API."""

    def current_actor(self) -> ActorIdentity:
        return ActorIdentity(actor_type="local_operator", actor_id=getpass.getuser())


@dataclass(frozen=True, slots=True)
class LocalApiSecuritySettings:
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    frontend_host: str = "127.0.0.1"
    frontend_port: int = 3000
    frontend_client_id: str = DEFAULT_FRONTEND_CLIENT_ID

    def __post_init__(self) -> None:
        for field_name, value in (
            ("api_host", self.api_host),
            ("frontend_host", self.frontend_host),
        ):
            if value != "127.0.0.1":
                raise ValueError(f"{field_name} must be '127.0.0.1', not {value!r}")
        for field_name, value in (
            ("api_port", self.api_port),
            ("frontend_port", self.frontend_port),
        ):
            if value <= 0 or value > 65535:
                raise ValueError(f"{field_name} must be a valid TCP port")
        if "localhost" in {self.api_host, self.frontend_host}:
            raise ValueError("supported config must not mix or use localhost; use 127.0.0.1")

    @property
    def api_origin(self) -> str:
        return f"http://{self.api_host}:{self.api_port}"

    @property
    def frontend_origin(self) -> str:
        return f"http://{self.frontend_host}:{self.frontend_port}"

    @property
    def trusted_api_host(self) -> str:
        return f"{self.api_host}:{self.api_port}"

    @property
    def allowed_frontend_origins(self) -> tuple[str, ...]:
        ports = {self.frontend_port, 3000, 5173}
        origins: list[str] = []
        for port in sorted(ports):
            origins.append(f"http://127.0.0.1:{port}")
            origins.append(f"http://localhost:{port}")
        return tuple(origins)

    def is_allowed_frontend_origin(self, origin: str | None) -> bool:
        return origin in self.allowed_frontend_origins

    @property
    def cors_allowed_methods(self) -> tuple[str, ...]:
        return ("GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS")

    @property
    def cors_allowed_headers(self) -> tuple[str, ...]:
        return ("*",)


def generate_correlation_id() -> str:
    return uuid4().hex


def normalize_correlation_id(value: str | None) -> str:
    if value and 1 <= len(value) <= 128:
        return value
    return generate_correlation_id()


def redact_public_message(message: str) -> str:
    parsed = urlparse(message)
    looks_like_safe_url = parsed.scheme in {"http", "https"} and parsed.hostname is not None
    redacted = _WINDOWS_ABSOLUTE_PATH_RE.sub("[redacted-path]", message)
    if not looks_like_safe_url:
        redacted = _POSIX_ABSOLUTE_PATH_RE.sub("[redacted-path]", redacted)
    redacted = _ENV_ASSIGNMENT_RE.sub("[redacted-env]", redacted)
    if _SECRET_KEY_RE.search(redacted):
        redacted = _SECRET_KEY_RE.sub("redacted", redacted)
    if "traceback" in redacted.lower():
        redacted = "Internal server error."
    return redacted


def redact_public_data(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[str, Any] = {}
        for key, item in value.items():
            lowered = key.lower()
            if _SECRET_KEY_RE.search(lowered) or _PID_RE.search(lowered):
                sanitized[key] = "[redacted]"
                continue
            sanitized[key] = redact_public_data(item)
        return sanitized
    if isinstance(value, (list, tuple)):
        return [redact_public_data(item) for item in value]
    if isinstance(value, str):
        return redact_public_message(value)
    return value


def public_error_payload(code: str, message: str, correlation_id: str) -> dict[str, Any]:
    return {
        "error": {
            "code": code,
            "message": redact_public_message(message),
            "correlation_id": correlation_id,
        }
    }


def dependency_versions() -> dict[str, str]:
    import fastapi

    return {
        "python": sys.version.split()[0],
        "fastapi": fastapi.__version__,
        "sqlite": getattr(__import__("sqlite3"), "sqlite_version"),
    }


def path_accessible(path: Path) -> bool:
    return path.exists() and os.access(path, os.R_OK)


def parse_origin(value: str) -> tuple[str, str, int]:
    parsed = urlparse(value)
    port = parsed.port
    if parsed.scheme != "http" or parsed.hostname is None or port is None:
        raise ValueError(f"Origin must be exact http origin with explicit port: {value!r}")
    return parsed.scheme, parsed.hostname, port
