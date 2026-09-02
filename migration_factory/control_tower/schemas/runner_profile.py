"""Runner profile schemas for Control Tower configuration."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, field_validator, model_validator

from .common import NonEmptyString, StrictModel, ensure_unique_ids, require_non_empty_string


FilesystemRootKind = Literal["source", "output"]


class RegisteredRoot(StrictModel):
    root_id: NonEmptyString
    kind: FilesystemRootKind
    path: NonEmptyString

    @field_validator("root_id", "path", mode="after")
    @classmethod
    def _validate_required_strings(cls, value: str, info):
        return require_non_empty_string(value, info.field_name)


class FilesystemPolicy(StrictModel):
    roots: tuple[RegisteredRoot, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _validate_unique_root_ids(self) -> "FilesystemPolicy":
        ensure_unique_ids(self.roots, "root_id", "registered root ID")
        return self


class MavenConfig(StrictModel):
    executable_path: NonEmptyString
    expected_version: NonEmptyString
    allow_wrapper: bool = False

    @field_validator("executable_path", "expected_version", mode="after")
    @classmethod
    def _validate_required_strings(cls, value: str, info):
        return require_non_empty_string(value, info.field_name)


class JdkConfig(StrictModel):
    jdk_id: NonEmptyString
    java_home: NonEmptyString
    expected_major: int
    role: Literal["source", "target", "runtime", "optional"]

    @field_validator("jdk_id", "java_home", mode="after")
    @classmethod
    def _validate_required_strings(cls, value: str, info):
        return require_non_empty_string(value, info.field_name)


class NetworkPolicy(StrictModel):
    mode: Literal["offline", "allowlisted"]
    allowed_hosts: tuple[str, ...] = ()

    @field_validator("allowed_hosts", mode="after")
    @classmethod
    def _validate_allowed_hosts(cls, value: tuple[str, ...], info):
        return tuple(require_non_empty_string(item, info.field_name) for item in value)


class AIProfileReference(StrictModel):
    profile_id: NonEmptyString

    @field_validator("profile_id", mode="after")
    @classmethod
    def _validate_profile_id(cls, value: str, info):
        return require_non_empty_string(value, info.field_name)


class RunnerProfile(StrictModel):
    schema_version: NonEmptyString
    runner_profile_id: NonEmptyString
    runner_profile_version: NonEmptyString
    display_name: NonEmptyString
    python_executable: NonEmptyString
    ai_hub_path: NonEmptyString
    maven: MavenConfig
    jdks: tuple[JdkConfig, ...] = Field(min_length=1)
    filesystem: FilesystemPolicy
    network: NetworkPolicy
    ai_profile: AIProfileReference | None = None

    @field_validator(
        "schema_version",
        "runner_profile_id",
        "runner_profile_version",
        "display_name",
        "python_executable",
        "ai_hub_path",
        mode="after",
    )
    @classmethod
    def _validate_required_strings(cls, value: str, info):
        return require_non_empty_string(value, info.field_name)

    @model_validator(mode="after")
    def _validate_unique_jdks(self) -> "RunnerProfile":
        ensure_unique_ids(self.jdks, "jdk_id", "jdk ID")
        return self


RegisteredFilesystemRoot = RegisteredRoot
MavenConfiguration = MavenConfig
JdkInstallation = JdkConfig
AiProfileReference = AIProfileReference
