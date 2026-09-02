from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import os
import shlex
import shutil
import xml.etree.ElementTree as ET

from migration_factory.maven import resolve_maven_executable


class BuildTool(str, Enum):
    MAVEN = "maven"
    GRADLE = "gradle"


class BuildValidationMode(str, Enum):
    STARTUP = "startup"
    PLAN_COMMAND = "plan_command"
    REACTOR_TEST = "reactor_test"


@dataclass(frozen=True)
class JavaProjectInfo:
    path: Path
    build_tool: BuildTool
    base_command: list[str]
    uses_wrapper: bool
    maven_modules: tuple[str, ...] = ()
    requested_path: Path | None = None


class JavaProjectDetectionError(Exception):
    pass


@dataclass(frozen=True)
class MavenRunTarget:
    module: str | None
    main_class: str | None


def detect_java_project(project_path: str | Path) -> JavaProjectInfo:
    path = Path(project_path).expanduser().resolve()

    if not path.exists():
        raise JavaProjectDetectionError(f"Project path does not exist: {path}")
    if not path.is_dir():
        raise JavaProjectDetectionError(f"Project path is not a directory: {path}")

    if (path / "pom.xml").is_file():
        return _maven_project(path)

    if any((path / filename).is_file() for filename in _gradle_markers()):
        return _gradle_project(path)

    raise JavaProjectDetectionError(
        "Could not detect Maven or Gradle project. Expected pom.xml, build.gradle, "
        "build.gradle.kts, settings.gradle, or settings.gradle.kts."
    )


def build_run_command(
    base_command: list[str],
    build_tool: BuildTool,
    module: str | None = None,
    main_class: str | None = None,
    use_reactor: bool = False,
) -> list[str]:
    command = list(base_command)
    if build_tool != BuildTool.MAVEN:
        return command

    executable = command[0]
    goal = command[1] if len(command) > 1 else "spring-boot:run"
    maven_args: list[str] = []

    if module:
        if use_reactor:
            maven_args.extend(["-pl", module, "-am"])
        else:
            maven_args.extend(["-f", (Path(module) / "pom.xml").as_posix()])
    if main_class:
        maven_args.append(f"-Dspring-boot.run.main-class={main_class}")

    return [executable, *maven_args, goal]


def full_validation_command(base_command: list[str], build_tool: BuildTool) -> list[str]:
    executable = base_command[0]
    if build_tool == BuildTool.MAVEN:
        return [executable, "clean", "test"]
    if build_tool == BuildTool.GRADLE:
        return [executable, "clean", "test"]
    return list(base_command)


def plan_validation_command(command: str | list[str] | tuple[str, ...], base_command: list[str]) -> list[str]:
    tokens = _command_tokens(command)
    if not tokens:
        return []

    normalized = list(tokens)
    if _same_build_executable(normalized[0], base_command[0]):
        normalized[0] = base_command[0]
    return normalized


def is_startup_validation_command(command: list[str]) -> bool:
    return any(token in {"spring-boot:run", "bootRun"} for token in command)


def is_maven_clean_test_command(command: list[str]) -> bool:
    if len(command) < 3:
        return False
    return "clean" in command[1:] and "test" in command[1:]


def _command_tokens(command: str | list[str] | tuple[str, ...]) -> list[str]:
    if isinstance(command, str):
        return shlex.split(command, posix=os.name != "nt")
    return [str(token) for token in command]


def _same_build_executable(left: str, right: str) -> bool:
    left_name = Path(left).name.lower()
    right_name = Path(right).name.lower()
    return _strip_command_extension(left_name) == _strip_command_extension(right_name)


def _strip_command_extension(name: str) -> str:
    for suffix in (".cmd", ".bat", ".exe"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def discover_maven_run_target(
    project_path: Path,
    module: str | None = None,
    main_class: str | None = None,
) -> MavenRunTarget:
    if module and main_class:
        return MavenRunTarget(module, main_class)

    candidates = _candidate_source_roots(project_path, module)
    discovered_module = module
    discovered_main_class = main_class

    for candidate_module, source_root in candidates:
        found_main_class = _find_spring_boot_main_class(source_root)
        if found_main_class is None:
            continue
        if discovered_module is None:
            discovered_module = candidate_module
        if discovered_main_class is None:
            discovered_main_class = found_main_class
        break

    return MavenRunTarget(discovered_module, discovered_main_class)


def _maven_project(path: Path) -> JavaProjectInfo:
    reactor_root, modules = _find_maven_reactor_root(path)
    command_root = reactor_root or path
    wrapper = _wrapper_command(command_root, "mvnw")
    if wrapper:
        return JavaProjectInfo(
            command_root,
            BuildTool.MAVEN,
            [wrapper, "spring-boot:run"],
            True,
            tuple(modules),
            path,
        )
    return JavaProjectInfo(
        command_root,
        BuildTool.MAVEN,
        [_resolve_system_command("mvn"), "spring-boot:run"],
        False,
        tuple(modules),
        path,
    )


def _gradle_project(path: Path) -> JavaProjectInfo:
    wrapper = _wrapper_command(path, "gradlew")
    if wrapper:
        return JavaProjectInfo(path, BuildTool.GRADLE, [wrapper, "bootRun"], True, requested_path=path)
    return JavaProjectInfo(
        path,
        BuildTool.GRADLE,
        [_resolve_system_command("gradle"), "bootRun"],
        False,
        requested_path=path,
    )


def _wrapper_command(path: Path, base_name: str) -> str | None:
    candidates = [base_name]
    if os.name == "nt":
        candidates = [f"{base_name}.cmd", f"{base_name}.bat", base_name]

    for candidate in candidates:
        wrapper = path / candidate
        if wrapper.is_file():
            return str(wrapper)

    return None


def _resolve_system_command(base_name: str) -> str:
    if base_name == "mvn":
        return resolve_maven_executable()

    candidates = [base_name]
    if os.name == "nt":
        candidates = [f"{base_name}.cmd", f"{base_name}.bat", f"{base_name}.exe", base_name]

    for candidate in candidates:
        resolved = shutil.which(candidate)
        if resolved:
            return resolved

    return base_name


def _gradle_markers() -> tuple[str, ...]:
    return ("build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts")


def _candidate_source_roots(project_path: Path, module: str | None) -> list[tuple[str | None, Path]]:
    if module:
        return [(module, project_path / module / "src" / "main" / "java")]

    modules = _read_maven_modules(project_path / "pom.xml")
    if modules:
        return [(module_name, project_path / module_name / "src" / "main" / "java") for module_name in modules]

    return [(None, project_path / "src" / "main" / "java")]


def _find_maven_reactor_root(path: Path) -> tuple[Path | None, list[str]]:
    for candidate in [path, *path.parents]:
        pom_path = candidate / "pom.xml"
        modules = _read_maven_modules(pom_path)
        if not modules:
            continue
        if _path_is_reactor_member(candidate, path, modules):
            return candidate, modules
    return None, []


def _path_is_reactor_member(root: Path, path: Path, modules: list[str]) -> bool:
    if root == path:
        return True
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError:
        return False
    return any(relative == module or relative.startswith(f"{module}/") for module in modules)


def _read_maven_modules(pom_path: Path) -> list[str]:
    if not pom_path.is_file():
        return []

    try:
        root = ET.parse(pom_path).getroot()
    except ET.ParseError:
        return []

    modules_node = _find_child(root, "modules")
    if modules_node is None:
        return []

    modules: list[str] = []
    for child in modules_node:
        if _local_name(child.tag) == "module" and child.text and child.text.strip():
            modules.append(child.text.strip().replace("\\", "/"))
    return modules


def _find_spring_boot_main_class(source_root: Path) -> str | None:
    if not source_root.is_dir():
        return None

    for java_file in source_root.rglob("*.java"):
        text = java_file.read_text(encoding="utf-8", errors="ignore")
        if "@SpringBootApplication" not in text and "SpringApplication.run" not in text:
            continue

        package_name = _extract_package_name(text)
        class_name = java_file.stem
        if package_name:
            return f"{package_name}.{class_name}"
        return class_name

    return None


def _extract_package_name(source_text: str) -> str | None:
    for raw_line in source_text.splitlines():
        line = raw_line.strip()
        if line.startswith("package ") and line.endswith(";"):
            return line.removeprefix("package ").removesuffix(";").strip()
    return None


def _find_child(parent: ET.Element, local_name: str) -> ET.Element | None:
    for child in parent:
        if _local_name(child.tag) == local_name:
            return child
    return None


def _local_name(tag: str) -> str:
    if tag.startswith("{") and "}" in tag:
        return tag.split("}", 1)[1]
    return tag
