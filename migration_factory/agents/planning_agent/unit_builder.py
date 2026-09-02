from dataclasses import dataclass
from typing import Any, Literal
from .assist_config import AssistPolicy, build_assist_policy


RequiredMode = Literal["yes", "auto"]
UnitId = str
ToolList = tuple[str, ...]

UNIT_ORDER: tuple[UnitId, ...] = (
    "baseline",
    "java-17",
    "spring-boot-3-5-14",
    "jakarta",
    "dependency-cleanup",
    "existing-test-migration",
)

# Deterministic tool mapping owned centrally to avoid per-unit drift.
TOOLS_BY_UNIT: dict[UnitId, ToolList] = {
    "baseline": ("maven", "junit"),
    "java-17": ("maven",),
    "java-21": ("maven",),
    "java-21-runtime-validation": ("maven", "junit"),
    "spring-boot-2-7": ("maven",),
    "spring-boot-3-5-14": ("maven",),
    "spring-boot-4-0": ("maven",),
    "jakarta": ("maven", "jdeps"),
    "dependency-cleanup": ("maven",),
    "existing-test-migration": ("maven", "junit"),
}

_BANNED_TOOL_TOKENS: tuple[str, ...] = ("copilot", "llm")


@dataclass(frozen=True)
class MigrationUnit:
    id: str
    goal: str
    writes_source: bool
    tools: tuple[str, ...]
    validation: tuple[str, ...]
    expected_artifacts: tuple[str, ...]
    rollback_strategy: str
    blocking_gate: str
    required: RequiredMode
    assist_policy: AssistPolicy


def _tools_for(unit_id: UnitId) -> ToolList:
    tools = TOOLS_BY_UNIT.get(unit_id, ("maven",))
    for tool in tools:
        lower_tool = tool.lower()
        if any(token in lower_tool for token in _BANNED_TOOL_TOKENS):
            raise ValueError(f"Disallowed tool token for {unit_id}: {tool}")
    return tools


def build_migration_units(profile: dict[str, Any] | None = None) -> tuple[MigrationUnit, ...]:
    """Return deterministic migration units in stable execution order."""
    assist_policy = build_assist_policy()
    strategy = str((profile or {}).get("strategy") or "")
    if strategy == "java21_runtime_validation_only":
        return (_baseline_unit(assist_policy), _java21_validation_unit(assist_policy))

    source = _source_from_profile(profile)
    target = _target_from_profile(profile)
    java_unit = f"java-{target.java}"
    boot_unit = _spring_boot_unit_id(target.spring_boot)
    java_label = f"Java {target.java}"
    boot_label = _spring_boot_label(target.spring_boot)

    units: list[MigrationUnit] = [_baseline_unit(assist_policy)]
    if source.java != target.java:
        units.append(
            MigrationUnit(
                id=java_unit,
                goal=f"Upgrade project runtime and build configuration to {java_label}.",
                writes_source=True,
                tools=_tools_for(java_unit),
                validation=("mvn", "clean", "test"),
                expected_artifacts=("target/classes", "target/surefire-reports"),
                rollback_strategy=f"Revert {java_label} configuration and dependency changes.",
                blocking_gate=f"Proceed only if {java_label} build and tests pass.",
                required="yes",
                assist_policy=assist_policy,
            )
        )
    if source.spring_boot != target.spring_boot:
        units.append(
            MigrationUnit(
                id=boot_unit,
                goal=f"Upgrade Spring Boot dependencies and plugins to {boot_label}.",
                writes_source=True,
                tools=_tools_for(boot_unit),
                validation=("mvn", "clean", "test"),
                expected_artifacts=("target/classes", "target/surefire-reports"),
                rollback_strategy="Revert Spring Boot version and related plugin updates.",
                blocking_gate=f"Proceed only if Spring Boot {boot_label} build and tests pass.",
                required="yes",
                assist_policy=assist_policy,
            )
        )
    if _spring_boot_major(target.spring_boot) >= 3:
        units.append(
            MigrationUnit(
                id="jakarta",
                goal="Migrate javax usages to Jakarta namespace and APIs.",
                writes_source=True,
                tools=_tools_for("jakarta"),
                validation=("mvn", "clean", "test"),
                expected_artifacts=("target/classes", "target/surefire-reports"),
                rollback_strategy="Revert Jakarta namespace refactors and dependency adjustments.",
                blocking_gate="Proceed only if Jakarta migration compiles and tests pass.",
                required="yes",
                assist_policy=assist_policy,
            )
        )
    units.extend(
        (
            MigrationUnit(
            id="dependency-cleanup",
            goal="Resolve obsolete and incompatible dependencies after platform upgrades.",
            writes_source=True,
            tools=_tools_for("dependency-cleanup"),
            validation=("mvn", "clean", "test"),
            expected_artifacts=("target/dependency", "target/surefire-reports"),
            rollback_strategy="Revert dependency cleanup updates to previous locked set.",
            blocking_gate="Proceed only if dependency graph resolves and tests pass.",
            required="yes",
            assist_policy=assist_policy,
            ),
            MigrationUnit(
                id="existing-test-migration",
                goal="Adapt existing test suites and test infrastructure to upgraded stack.",
                writes_source=True,
                tools=_tools_for("existing-test-migration"),
                validation=("mvn", "clean", "test"),
                expected_artifacts=("target/test-classes", "target/surefire-reports"),
                rollback_strategy="Revert test framework and test source migration changes.",
                blocking_gate="Proceed only if migrated tests pass on upgraded stack.",
                required="auto",
                assist_policy=assist_policy,
            ),
        )
    )
    return tuple(units)


@dataclass(frozen=True)
class _TargetVersions:
    java: str = "17"
    spring_boot: str = "3.5.14"


def _baseline_unit(assist_policy: AssistPolicy) -> MigrationUnit:
    return MigrationUnit(
        id="baseline",
        goal="Establish baseline build and test posture before migration changes.",
        writes_source=False,
        tools=_tools_for("baseline"),
        validation=("mvn", "clean", "test"),
        expected_artifacts=("target/surefire-reports",),
        rollback_strategy="Revert baseline verification changes and restore prior working tree state.",
        blocking_gate="Proceed only if baseline mvn clean test passes.",
        required="yes",
        assist_policy=assist_policy,
    )


def _java21_validation_unit(assist_policy: AssistPolicy) -> MigrationUnit:
    return MigrationUnit(
        id="java-21-runtime-validation",
        goal="Validate the already-migrated Spring Boot 3.5 application on a Java 21 runtime.",
        writes_source=True,
        tools=_tools_for("java-21-runtime-validation"),
        validation=("mvn", "clean", "test"),
        expected_artifacts=("target/classes", "target/surefire-reports"),
        rollback_strategy="Revert Java 21 recipe application and restore prior Java 17-compatible sources.",
        blocking_gate="Proceed only if Java 21 recipe application and runtime validation pass.",
        required="yes",
        assist_policy=assist_policy,
    )


def _target_from_profile(profile: dict[str, Any] | None) -> _TargetVersions:
    target = profile.get("target") if isinstance(profile, dict) else None
    if not isinstance(target, dict):
        return _TargetVersions()
    java = _major_text(target.get("java")) or "17"
    spring_boot = _version_text(target.get("spring_boot")) or "3.5.14"
    return _TargetVersions(java=java, spring_boot=spring_boot)


def _source_from_profile(profile: dict[str, Any] | None) -> _TargetVersions:
    source = profile.get("source") if isinstance(profile, dict) else None
    if not isinstance(source, dict):
        return _TargetVersions(java="", spring_boot="")
    java = _first_allowed(source.get("java")) or ""
    spring_boot = _first_allowed(source.get("spring_boot")) or ""
    return _TargetVersions(java=java, spring_boot=spring_boot)


def _spring_boot_unit_id(version: str) -> str:
    parts = version.split(".")
    if version == "3.5.14":
        return "spring-boot-3-5-14"
    if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
        return f"spring-boot-{parts[0]}-{parts[1]}"
    return "spring-boot-" + version.replace(".", "-")


def _spring_boot_label(version: str) -> str:
    if version == "3.5.14":
        return "3.5.14"
    parts = version.split(".")
    if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
        return f"{parts[0]}.{parts[1]}"
    return version


def _major_text(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return text.split(".", 1)[0]


def _version_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _first_allowed(value: Any) -> str | None:
    if not isinstance(value, dict):
        return _version_text(value)
    values = value.get("allowed_versions") or value.get("allowed_version_prefixes")
    if isinstance(values, list) and values:
        return _version_text(values[0])
    return None


def _spring_boot_major(value: str) -> int:
    try:
        return int(str(value).split(".", 1)[0])
    except (TypeError, ValueError):
        return 0
