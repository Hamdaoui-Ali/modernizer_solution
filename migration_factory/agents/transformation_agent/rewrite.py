from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import xml.etree.ElementTree as ET

from migration_factory.maven import resolve_maven_executable

DEFAULT_OPENREWRITE_MAVEN_PLUGIN_VERSION = "6.39.0"
OPENREWRITE_MAVEN_PLUGIN = ("org.openrewrite.maven", "rewrite-maven-plugin")


class RewritePluginError(Exception):
    pass


@dataclass(frozen=True)
class RewritePluginInjection:
    pom_path: Path
    coordinates: tuple[str, str]


def inject_rewrite_plugin(
    project_path: Path,
    plugin_txt_path: str | Path,
    *,
    module: str | None = None,
    backup: bool = True,
) -> RewritePluginInjection:
    pom_path = _resolve_pom(project_path, module)
    plugin_element = _parse_plugin_xml(Path(plugin_txt_path).expanduser().resolve())
    coordinates = _plugin_coordinates(plugin_element)

    tree = ET.parse(pom_path)
    root = tree.getroot()
    namespace = _namespace_uri(root.tag)
    if namespace:
        ET.register_namespace("", namespace)

    build = _find_or_create_child(root, "build", namespace)
    plugins = _find_or_create_child(build, "plugins", namespace)
    _upsert_plugin(plugins, plugin_element, coordinates, namespace)

    if backup:
        shutil.copyfile(pom_path, pom_path.with_suffix(".xml.bak"))
    tree.write(pom_path, encoding="utf-8", xml_declaration=True)
    return RewritePluginInjection(pom_path=pom_path, coordinates=coordinates)


def build_rewrite_run_command(
    active_recipes: list[str],
    *,
    recipe_artifacts: list[str] | None = None,
    plugin_version: str = DEFAULT_OPENREWRITE_MAVEN_PLUGIN_VERSION,
    apply_goal: str = "run",
    maven_args: list[str] | None = None,
) -> str:
    goal_name = str(apply_goal or "run").strip() or "run"
    goal = f"{OPENREWRITE_MAVEN_PLUGIN[0]}:{OPENREWRITE_MAVEN_PLUGIN[1]}:{_concrete_plugin_version(plugin_version)}:{goal_name}"
    args = [goal]
    if active_recipes:
        args.append(f"-Drewrite.activeRecipes={','.join(active_recipes)}")
    if recipe_artifacts:
        args.append(f"-Drewrite.recipeArtifactCoordinates={','.join(recipe_artifacts)}")
    if maven_args:
        args.extend(str(item) for item in maven_args)
    return " ".join([resolve_maven_executable(), *args])


def rewrite_plugin_version_from_xml(plugin_txt_path: str | Path) -> str:
    plugin_element = _parse_plugin_xml(Path(plugin_txt_path).expanduser().resolve())
    group_id, artifact_id = _plugin_coordinates(plugin_element)
    if (group_id, artifact_id) != OPENREWRITE_MAVEN_PLUGIN:
        return DEFAULT_OPENREWRITE_MAVEN_PLUGIN_VERSION
    return _concrete_plugin_version(_child_text(plugin_element, "version"))


def _resolve_pom(project_path: Path, module: str | None) -> Path:
    pom_path = project_path / module / "pom.xml" if module else project_path / "pom.xml"
    if not pom_path.is_file():
        raise RewritePluginError(f"Could not find pom.xml at: {pom_path}")
    return pom_path


def _parse_plugin_xml(plugin_txt_path: Path) -> ET.Element:
    if not plugin_txt_path.is_file():
        raise RewritePluginError(f"OpenRewrite plugin file does not exist: {plugin_txt_path}")
    content = plugin_txt_path.read_text(encoding="utf-8").strip()
    if not content:
        raise RewritePluginError("OpenRewrite plugin file is empty")
    try:
        element = ET.fromstring(content)
    except ET.ParseError as exc:
        raise RewritePluginError(f"OpenRewrite plugin file is not valid XML: {exc}") from exc
    if _local_name(element.tag) != "plugin":
        raise RewritePluginError("OpenRewrite plugin file must contain one <plugin> element")
    return element


def _plugin_coordinates(plugin_element: ET.Element) -> tuple[str, str]:
    group_id = (_child_text(plugin_element, "groupId") or "org.openrewrite.maven").strip()
    artifact_id = (_child_text(plugin_element, "artifactId") or "").strip()
    if not artifact_id:
        raise RewritePluginError("OpenRewrite plugin XML must include <artifactId>")
    return group_id, artifact_id


def _concrete_plugin_version(version: str | None) -> str:
    value = str(version or "").strip()
    if not value or value.upper() == "RELEASE":
        return DEFAULT_OPENREWRITE_MAVEN_PLUGIN_VERSION
    return value


def _upsert_plugin(
    plugins_node: ET.Element,
    plugin_element: ET.Element,
    coordinates: tuple[str, str],
    namespace: str | None,
) -> None:
    expected_group, expected_artifact = coordinates
    for existing in list(plugins_node):
        if _local_name(existing.tag) != "plugin":
            continue
        existing_group = (_child_text(existing, "groupId") or "org.apache.maven.plugins").strip()
        existing_artifact = (_child_text(existing, "artifactId") or "").strip()
        if existing_group == expected_group and existing_artifact == expected_artifact:
            plugins_node.remove(existing)
            break
    plugins_node.append(_with_namespace(plugin_element, namespace))


def _with_namespace(element: ET.Element, namespace: str | None) -> ET.Element:
    copied = ET.fromstring(ET.tostring(element, encoding="unicode"))
    if not namespace:
        return copied
    for node in copied.iter():
        node.tag = f"{{{namespace}}}{_local_name(node.tag)}"
    return copied


def _find_or_create_child(parent: ET.Element, name: str, namespace: str | None) -> ET.Element:
    child = _find_child(parent, name)
    if child is not None:
        return child
    tag = f"{{{namespace}}}{name}" if namespace else name
    child = ET.Element(tag)
    parent.append(child)
    return child


def _find_child(parent: ET.Element, local_name: str) -> ET.Element | None:
    for child in parent:
        if _local_name(child.tag) == local_name:
            return child
    return None


def _child_text(parent: ET.Element, local_name: str) -> str | None:
    child = _find_child(parent, local_name)
    if child is None or child.text is None:
        return None
    return child.text


def _namespace_uri(tag: str) -> str | None:
    if tag.startswith("{") and "}" in tag:
        return tag[1:].split("}", 1)[0]
    return None


def _local_name(tag: str) -> str:
    if tag.startswith("{") and "}" in tag:
        return tag.split("}", 1)[1]
    return tag
