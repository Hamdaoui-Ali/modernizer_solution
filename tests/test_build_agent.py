from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from helpers import workspace_temp_dir
from migration_factory.agents.build_agent import run_build_agent
from migration_factory.agents.build_agent.classifier import BuildClassification, BuildResultKind, classify_line
from migration_factory.agents.build_agent.detection import (
    BuildTool,
    JavaProjectInfo,
    build_run_command,
    detect_java_project,
    discover_maven_run_target,
    full_validation_command,
)
from migration_factory.agents.build_agent import runner as runner_module
from migration_factory.agents.build_agent.runner import ProcessRunResult, resolve_command, run_until_build_result, run_until_exit
from migration_factory.contracts.migration import (
    BuildValidationStatus,
    initialize_ledger,
    load_ledger,
    mark_unit_awaiting_build,
    mark_unit_in_progress,
)


class BuildAgentTests(unittest.TestCase):
    def test_detects_maven_wrapper(self) -> None:
        with workspace_temp_dir() as project:
            (project / "pom.xml").write_text("<project />", encoding="utf-8")
            wrapper_name = "mvnw.cmd" if os.name == "nt" else "mvnw"
            (project / wrapper_name).write_text("", encoding="utf-8")

            info = detect_java_project(project)

            self.assertEqual(info.build_tool, BuildTool.MAVEN)
            self.assertTrue(info.uses_wrapper)

    def test_build_command_adds_maven_module_and_main_class_for_single_module(self) -> None:
        command = build_run_command(
            ["mvn", "spring-boot:run"],
            BuildTool.MAVEN,
            module="app-service",
            main_class="com.example.Application",
        )

        self.assertEqual(
            command,
            [
                "mvn",
                "-f",
                "app-service/pom.xml",
                "-Dspring-boot.run.main-class=com.example.Application",
                "spring-boot:run",
            ],
        )

    def test_build_command_can_build_reactor_module_when_explicitly_requested(self) -> None:
        command = build_run_command(
            ["mvn", "spring-boot:run"],
            BuildTool.MAVEN,
            module="shoppoc-app",
            main_class="com.shoppoc.app.ShoppocApplication",
            use_reactor=True,
        )

        self.assertEqual(
            command,
            [
                "mvn",
                "-pl",
                "shoppoc-app",
                "-am",
                "-Dspring-boot.run.main-class=com.shoppoc.app.ShoppocApplication",
                "spring-boot:run",
            ],
        )
        self.assertNotIn("-f", command)
        self.assertNotIn(os.path.join("shoppoc-app", "pom.xml"), command)

    def test_classifies_compilation_error(self) -> None:
        result = classify_line("[ERROR] COMPILATION ERROR")

        self.assertIsNotNone(result)
        self.assertEqual(result.kind, BuildResultKind.COMPILATION_ERROR)

    def test_classifies_spring_boot_startup_success(self) -> None:
        for line in (
            "Started DemoApplication in 4.321 seconds (JVM running for 5.0)",
            "Tomcat started on port(s): 8080 (http) with context path ''",
            "Netty started on port 8080",
            "Started Application in 1.0 seconds",
        ):
            result = classify_line(line)

            self.assertIsNotNone(result)
            self.assertEqual(result.kind, BuildResultKind.SUCCESS)

    def test_classifies_maven_enforcer_java_runtime_mismatch(self) -> None:
        line = "[ERROR] Detected JDK version 21.0.11 is not in allowed range [1.8,1.9)."

        result = classify_line(line)

        self.assertIsNotNone(result)
        self.assertEqual(result.kind, BuildResultKind.JAVA_RUNTIME_MISMATCH)
        self.assertIn("detected 21.0.11", result.message)
        self.assertIn("expected [1.8,1.9)", result.message)

    def test_runner_returns_success_and_stops_process_after_startup_detection(self) -> None:
        with workspace_temp_dir() as tmp:
            command = [
                sys.executable,
                "-u",
                "-c",
                "import time; print('Tomcat started on port(s): 8080', flush=True); time.sleep(60)",
            ]

            with patch(
                "migration_factory.agents.build_agent.runner._terminate_process_tree",
                wraps=runner_module._terminate_process_tree,
            ) as terminate:
                result = run_until_build_result(command, tmp, timeout_seconds=10, stream_output=False)

            self.assertTrue(result.succeeded)
            self.assertEqual(result.classification.kind, BuildResultKind.SUCCESS)
            self.assertIn("Tomcat started on port", result.classification.line or "")
            terminate.assert_called_once()

    def test_runner_timeout_kills_process_tree_and_returns_failure(self) -> None:
        with workspace_temp_dir() as tmp:
            command = [sys.executable, "-u", "-c", "import time; time.sleep(60)"]

            with patch(
                "migration_factory.agents.build_agent.runner._terminate_process_tree",
                wraps=runner_module._terminate_process_tree,
            ) as terminate:
                result = run_until_build_result(command, tmp, timeout_seconds=1, stream_output=False)

            self.assertFalse(result.succeeded)
            self.assertEqual(result.classification.kind, BuildResultKind.TIMEOUT)
            terminate.assert_called_once()

    def test_run_until_exit_timeout_reports_command_completion_timeout(self) -> None:
        with workspace_temp_dir() as tmp:
            command = [sys.executable, "-u", "-c", "import time; time.sleep(60)"]

            result = run_until_exit(command, tmp, timeout_seconds=1, stream_output=False)

            self.assertFalse(result.succeeded)
            self.assertEqual(result.classification.kind, BuildResultKind.TIMEOUT)
            self.assertEqual(result.classification.message, "Command timed out after 1 seconds before completion")
            self.assertNotIn("startup", result.classification.message)

    def test_resolve_maven_command_prefers_maven_cmd_env(self) -> None:
        maven_cmd = r"C:\Tools\apache-maven-3.9.15\bin\mvn.cmd"

        resolved = resolve_command(["mvn", "clean", "test"], env={"MAVEN_CMD": maven_cmd})

        self.assertEqual(resolved[0], maven_cmd)
        self.assertEqual(resolved[1:], ["clean", "test"])

    def test_runner_failure_pattern_kills_process_tree_and_returns_failure(self) -> None:
        with workspace_temp_dir() as tmp:
            command = [
                sys.executable,
                "-u",
                "-c",
                "import time; print('Address already in use', flush=True); time.sleep(60)",
            ]

            with patch(
                "migration_factory.agents.build_agent.runner._terminate_process_tree",
                wraps=runner_module._terminate_process_tree,
            ) as terminate:
                result = run_until_build_result(command, tmp, timeout_seconds=10, stream_output=False)

            self.assertFalse(result.succeeded)
            self.assertEqual(result.classification.kind, BuildResultKind.PORT_IN_USE)
            terminate.assert_called_once()

    def test_discovers_maven_module_and_main_class_from_parent_project(self) -> None:
        with workspace_temp_dir() as project:
            (project / "pom.xml").write_text(
                """<project>
  <modelVersion>4.0.0</modelVersion>
  <packaging>pom</packaging>
  <modules>
    <module>shoppoc-app</module>
  </modules>
</project>""",
                encoding="utf-8",
            )
            app_source = project / "shoppoc-app" / "src" / "main" / "java" / "com" / "example"
            app_source.mkdir(parents=True)
            (app_source / "Application.java").write_text(
                """package com.example;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

@SpringBootApplication
public class Application {
    public static void main(String[] args) {
        SpringApplication.run(Application.class, args);
    }
}
""",
                encoding="utf-8",
            )

            target = discover_maven_run_target(project)

            self.assertEqual(target.module, "shoppoc-app")
            self.assertEqual(target.main_class, "com.example.Application")

    def test_detects_maven_reactor_root_from_app_module_path(self) -> None:
        with workspace_temp_dir() as project:
            _write_multi_module_project(project)

            info = detect_java_project(project / "shoppoc-app")

            self.assertEqual(info.path, project)
            self.assertEqual(info.requested_path, project / "shoppoc-app")
            self.assertEqual(info.maven_modules, ("shoppoc-user", "shoppoc-app"))

    def test_baseline_multi_module_startup_validation_uses_app_pom_from_reactor_root(self) -> None:
        with workspace_temp_dir() as project:
            _write_multi_module_project(project)
            process_result = ProcessRunResult(
                classification=BuildClassification(
                    BuildResultKind.SUCCESS,
                    "Application started successfully",
                    "Started ShoppocApplication",
                ),
                exit_code=0,
            )

            with patch(
                "migration_factory.agents.build_agent.agent.run_until_build_result",
                return_value=process_result,
            ) as run_process:
                result = run_build_agent(
                    project / "shoppoc-app",
                    stream_output=False,
                    validation_unit_id="baseline",
                    source_changing_unit=False,
                )

            self.assertTrue(result.succeeded)
            run_process.assert_called_once()
            kwargs = run_process.call_args.kwargs
            self.assertEqual(kwargs["cwd"], project)
            self.assertEqual(kwargs["timeout_seconds"], 120)
            self.assertEqual(
                kwargs["command"],
                    [
                        kwargs["command"][0],
                        "-f",
                        "shoppoc-app/pom.xml",
                        "-Dspring-boot.run.main-class=com.shoppoc.app.ShoppocApplication",
                        "spring-boot:run",
                    ],
                )
            self.assertTrue(str(kwargs["command"][0]).endswith(("mvn", "mvn.cmd", "mvn.bat", "mvn.exe")))
            self.assertNotIn("-pl", kwargs["command"])
            self.assertNotIn("-am", kwargs["command"])

    def test_baseline_validation_uses_source_jdk_env(self) -> None:
        with workspace_temp_dir() as project:
            _write_multi_module_project(project)
            java8_home = str(project / "jdk8")
            process_result = ProcessRunResult(
                classification=BuildClassification(BuildResultKind.SUCCESS, "Build completed successfully"),
                exit_code=0,
            )

            with patch.dict(os.environ, {"JAVA8_HOME": java8_home}, clear=False):
                with patch(
                    "migration_factory.agents.build_agent.agent.run_until_exit",
                    return_value=process_result,
                ) as run_process:
                    result = run_build_agent(
                        project / "shoppoc-app",
                        stream_output=False,
                        validation_unit_id="baseline",
                        validation_command="mvn clean test",
                        source_jdk_home_env="JAVA8_HOME",
                    )

            self.assertTrue(result.succeeded)
            env = run_process.call_args.kwargs["env"]
            self.assertEqual(env["JAVA_HOME"], java8_home)
            self.assertTrue(env["PATH"].startswith(str(Path(java8_home) / "bin") + os.pathsep))
            self.assertNotIn("-Denforcer.skip=true", run_process.call_args.kwargs["command"])

    def test_post_transform_multi_module_validation_uses_reactor_clean_test(self) -> None:
        with workspace_temp_dir() as project:
            _write_multi_module_project(project)
            process_result = ProcessRunResult(
                classification=BuildClassification(
                    BuildResultKind.SUCCESS,
                    "Build completed successfully",
                ),
                exit_code=0,
            )

            with patch(
                "migration_factory.agents.build_agent.agent.run_until_exit",
                return_value=process_result,
            ) as run_process:
                with patch("migration_factory.agents.build_agent.agent.run_until_build_result") as run_startup:
                    result = run_build_agent(
                        project / "shoppoc-app",
                        stream_output=False,
                        validation_unit_id="java-17",
                        source_changing_unit=True,
                    )

            self.assertTrue(result.succeeded)
            run_startup.assert_not_called()
            run_process.assert_called_once()
            kwargs = run_process.call_args.kwargs
            self.assertEqual(kwargs["cwd"], project)
            self.assertEqual(kwargs["command"][1:], ["clean", "test"])
            self.assertEqual(kwargs["timeout_seconds"], 300)
            self.assertNotIn("-pl", kwargs["command"])
            self.assertNotIn("-am", kwargs["command"])
            self.assertNotIn("spring-boot:run", kwargs["command"])

    def test_target_validation_uses_target_jdk_env(self) -> None:
        with workspace_temp_dir() as project:
            _write_multi_module_project(project)
            java21_home = str(project / "jdk21")
            process_result = ProcessRunResult(
                classification=BuildClassification(BuildResultKind.SUCCESS, "Build completed successfully"),
                exit_code=0,
            )

            with patch.dict(os.environ, {"JAVA21_HOME": java21_home}, clear=False):
                with patch(
                    "migration_factory.agents.build_agent.agent.run_until_exit",
                    return_value=process_result,
                ) as run_process:
                    result = run_build_agent(
                        project / "shoppoc-app",
                        stream_output=False,
                        validation_unit_id="java-17",
                        source_changing_unit=True,
                        target_jdk_home_env="JAVA21_HOME",
                    )

            self.assertTrue(result.succeeded)
            env = run_process.call_args.kwargs["env"]
            self.assertEqual(env["JAVA_HOME"], java21_home)
            self.assertTrue(env["PATH"].startswith(str(Path(java21_home) / "bin") + os.pathsep))

    def test_post_transform_multi_module_validation_honors_timeout_override(self) -> None:
        with workspace_temp_dir() as project:
            _write_multi_module_project(project)
            process_result = ProcessRunResult(
                classification=BuildClassification(
                    BuildResultKind.SUCCESS,
                    "Build completed successfully",
                ),
                exit_code=0,
            )

            with patch(
                "migration_factory.agents.build_agent.agent.run_until_exit",
                return_value=process_result,
            ) as run_process:
                result = run_build_agent(
                    project / "shoppoc-app",
                    timeout_seconds=450,
                    stream_output=False,
                    validation_unit_id="java-17",
                    source_changing_unit=True,
                )

            self.assertTrue(result.succeeded)
            self.assertEqual(run_process.call_args.kwargs["timeout_seconds"], 450)

    def test_source_changing_spring_boot_unit_records_root_reactor_validation_metadata(self) -> None:
        with workspace_temp_dir() as project:
            _write_multi_module_project(project)
            (project / ".m2" / "repository").mkdir(parents=True)
            ledger_file = project / ".migration" / "ledger.json"
            initialize_ledger(
                ledger_file,
                migration_id="test",
                migration_name="Test",
                total_units=1,
                target_path=project,
            )
            mark_unit_in_progress(
                ledger_file,
                unit_id="spring-boot-3-5-14",
                unit_index=0,
                title="Spring Boot 3.5.14",
            )
            mark_unit_awaiting_build(ledger_file, unit_id="spring-boot-3-5-14")
            process_result = ProcessRunResult(
                classification=BuildClassification(
                    BuildResultKind.SUCCESS,
                    "Build completed successfully",
                ),
                exit_code=0,
                warnings=["reactor validation warning"],
            )

            with patch(
                "migration_factory.agents.build_agent.agent.run_until_exit",
                return_value=process_result,
            ) as run_process:
                with patch("migration_factory.agents.build_agent.agent.run_until_build_result") as run_startup:
                    result = run_build_agent(
                        project / "shoppoc-app",
                        ledger_file=ledger_file,
                        stream_output=False,
                        validation_unit_id="spring-boot-3-5-14",
                        source_changing_unit=True,
                    )

            self.assertTrue(result.succeeded)
            run_startup.assert_not_called()
            run_process.assert_called_once()
            command = run_process.call_args.kwargs["command"]
            self.assertEqual(run_process.call_args.kwargs["cwd"], project)
            self.assertEqual(run_process.call_args.kwargs["timeout_seconds"], 300)
            self.assertEqual(command[1:], ["clean", "test"])
            self.assertNotIn("spring-boot:run", command)
            self.assertNotIn("-f", command)
            self.assertNotIn("shoppoc-app/pom.xml", command)
            ledger = load_ledger(ledger_file)
            validation = ledger["build_validation"]
            self.assertEqual(validation["unit_id"], "spring-boot-3-5-14")
            self.assertEqual(validation["cwd"], str(project))
            self.assertEqual(validation["command"], command)
            self.assertEqual(validation["warnings"], ["reactor validation warning"])

    def test_multi_module_full_validation_command_is_clean_test_from_reactor_root(self) -> None:
        with workspace_temp_dir() as project:
            _write_multi_module_project(project)
            info = detect_java_project(project / "shoppoc-app")

            command = full_validation_command(info.base_command, info.build_tool)

            self.assertEqual(info.path, project)
            self.assertEqual(command[1:], ["clean", "test"])
            self.assertNotIn("-Denforcer.skip=true", command)

    def test_java21_validation_blocks_when_runtime_is_too_old(self) -> None:
        with workspace_temp_dir() as project:
            _write_multi_module_project(project)
            version_result = ProcessRunResult(
                classification=BuildClassification(BuildResultKind.SUCCESS, "version checked"),
                exit_code=0,
                stderr=['openjdk version "17.0.12"'],
            )

            with patch(
                "migration_factory.agents.build_agent.agent._run_version_command",
                return_value=version_result,
            ):
                with patch("migration_factory.agents.build_agent.agent.run_until_exit") as run_process:
                    result = run_build_agent(
                        project / "shoppoc-app",
                        stream_output=False,
                        validation_unit_id="java-21",
                        source_changing_unit=True,
                    )

            self.assertFalse(result.succeeded)
            self.assertIn("incompatible with target Java 21", result.message)
            run_process.assert_not_called()

    def test_boot4_validation_requires_maven_363_or_newer(self) -> None:
        with workspace_temp_dir() as project:
            _write_multi_module_project(project)
            output_dir = project / "contracts"

            def version_side_effect(command, env=None):
                if command[0] == "java":
                    return ProcessRunResult(
                        classification=BuildClassification(BuildResultKind.SUCCESS, "version checked"),
                        exit_code=0,
                        stderr=['openjdk version "21.0.2"'],
                    )
                return ProcessRunResult(
                    classification=BuildClassification(BuildResultKind.SUCCESS, "version checked"),
                    exit_code=0,
                    stdout=["Apache Maven 3.6.2"],
                )

            with patch(
                "migration_factory.agents.build_agent.agent._run_version_command",
                side_effect=version_side_effect,
            ):
                result = run_build_agent(
                    project / "shoppoc-app",
                    output_dir=output_dir,
                    stream_output=False,
                    validation_unit_id="spring-boot-4-0",
                    source_changing_unit=True,
                )

            self.assertFalse(result.succeeded)
            self.assertIn("Maven version 3.6.2 is incompatible", result.message)
            self.assertIsNotNone(result.error_contract_path)
            contract = json.loads(result.error_contract_path.read_text(encoding="utf-8"))
            self.assertEqual(contract["detected_version"], "3.6.2")
            self.assertEqual(contract["required_minimum"], "3.6.3")
            self.assertEqual(contract["profile"], "spring-boot-4")
            self.assertEqual(contract["target_unit"], "spring-boot-4-0")

    def test_boot4_validation_allows_maven_363(self) -> None:
        with workspace_temp_dir() as project:
            _write_multi_module_project(project)

            def version_side_effect(command, env=None):
                if command[0] == "java":
                    return ProcessRunResult(
                        classification=BuildClassification(BuildResultKind.SUCCESS, "version checked"),
                        exit_code=0,
                        stderr=['openjdk version "21.0.2"'],
                    )
                return ProcessRunResult(
                    classification=BuildClassification(BuildResultKind.SUCCESS, "version checked"),
                    exit_code=0,
                    stdout=["Apache Maven 3.6.3"],
                )

            with patch(
                "migration_factory.agents.build_agent.agent._run_version_command",
                side_effect=version_side_effect,
            ):
                with patch("migration_factory.agents.build_agent.agent.run_until_exit") as run_process:
                    run_process.return_value = ProcessRunResult(
                        classification=BuildClassification(BuildResultKind.SUCCESS, "build ok"),
                        exit_code=0,
                        stdout=["BUILD SUCCESS"],
                    )
                    result = run_build_agent(
                        project / "shoppoc-app",
                        stream_output=False,
                        validation_unit_id="spring-boot-4-0",
                        source_changing_unit=True,
                    )

            self.assertTrue(result.succeeded)
            run_process.assert_called_once()

    def test_boot4_validation_uses_system_maven_when_wrapper_is_not_executable(self) -> None:
        with workspace_temp_dir() as project:
            (project / "pom.xml").write_text("<project />", encoding="utf-8")
            (project / "mvnw").write_text("#!/bin/sh\n", encoding="utf-8")
            version_commands: list[list[str]] = []

            def version_side_effect(command, env=None):
                version_commands.append(command)
                if command[0] == "java":
                    return ProcessRunResult(
                        classification=BuildClassification(BuildResultKind.SUCCESS, "version checked"),
                        exit_code=0,
                        stderr=['openjdk version "21.0.2"'],
                    )
                return ProcessRunResult(
                    classification=BuildClassification(BuildResultKind.SUCCESS, "version checked"),
                    exit_code=0,
                    stdout=["Apache Maven 3.6.3"],
                )

            with patch(
                "migration_factory.agents.build_agent.agent._run_version_command",
                side_effect=version_side_effect,
            ):
                with patch("migration_factory.agents.build_agent.agent.run_until_exit") as run_process:
                    run_process.return_value = ProcessRunResult(
                        classification=BuildClassification(BuildResultKind.SUCCESS, "build ok"),
                        exit_code=0,
                        stdout=["BUILD SUCCESS"],
                    )
                    result = run_build_agent(
                        project,
                        stream_output=False,
                        validation_unit_id="spring-boot-4-0",
                        source_changing_unit=True,
                        validation_command=["mvn", "clean", "test"],
                    )

            self.assertTrue(result.succeeded)
            self.assertTrue(str(version_commands[1][0]).endswith(("mvn", "mvn.cmd", "mvn.bat", "mvn.exe")))
            self.assertEqual(version_commands[1][1:], ["-version"])

    def test_boot4_validation_allows_maven_39x(self) -> None:
        with workspace_temp_dir() as project:
            _write_multi_module_project(project)

            def version_side_effect(command, env=None):
                if command[0] == "java":
                    return ProcessRunResult(
                        classification=BuildClassification(BuildResultKind.SUCCESS, "version checked"),
                        exit_code=0,
                        stderr=['openjdk version "21.0.2"'],
                    )
                return ProcessRunResult(
                    classification=BuildClassification(BuildResultKind.SUCCESS, "version checked"),
                    exit_code=0,
                    stdout=["Apache Maven 3.9.9"],
                )

            with patch(
                "migration_factory.agents.build_agent.agent._run_version_command",
                side_effect=version_side_effect,
            ):
                with patch("migration_factory.agents.build_agent.agent.run_until_exit") as run_process:
                    run_process.return_value = ProcessRunResult(
                        classification=BuildClassification(BuildResultKind.SUCCESS, "build ok"),
                        exit_code=0,
                        stdout=["BUILD SUCCESS"],
                    )
                    result = run_build_agent(
                        project / "shoppoc-app",
                        stream_output=False,
                        validation_unit_id="spring-boot-4-0",
                        source_changing_unit=True,
                    )

            self.assertTrue(result.succeeded)
            run_process.assert_called_once()

    def test_boot4_validation_fails_clearly_for_malformed_maven_output(self) -> None:
        with workspace_temp_dir() as project:
            _write_multi_module_project(project)
            output_dir = project / "contracts"

            def version_side_effect(command, env=None):
                if command[0] == "java":
                    return ProcessRunResult(
                        classification=BuildClassification(BuildResultKind.SUCCESS, "version checked"),
                        exit_code=0,
                        stderr=['openjdk version "21.0.2"'],
                    )
                return ProcessRunResult(
                    classification=BuildClassification(BuildResultKind.SUCCESS, "version checked"),
                    exit_code=0,
                    stdout=["Maven home: /opt/maven"],
                )

            with patch(
                "migration_factory.agents.build_agent.agent._run_version_command",
                side_effect=version_side_effect,
            ):
                with patch("migration_factory.agents.build_agent.agent.run_until_exit") as run_process:
                    result = run_build_agent(
                        project / "shoppoc-app",
                        output_dir=output_dir,
                        stream_output=False,
                        validation_unit_id="spring-boot-4-0",
                        source_changing_unit=True,
                    )

            self.assertFalse(result.succeeded)
            self.assertIn("unparseable", result.message)
            self.assertIsNotNone(result.error_contract_path)
            contract = json.loads(result.error_contract_path.read_text(encoding="utf-8"))
            self.assertEqual(contract["detected_version"], None)
            self.assertEqual(contract["required_minimum"], "3.6.3")
            self.assertEqual(contract["profile"], "spring-boot-4")
            self.assertEqual(contract["target_unit"], "spring-boot-4-0")
            run_process.assert_not_called()

    def test_writes_json_contract_when_project_detection_fails(self) -> None:
        with workspace_temp_dir() as tmp:
            output_dir = tmp / "contracts" / "build"
            result = run_build_agent(tmp / "missing-project", output_dir=output_dir, stream_output=False)

            self.assertFalse(result.succeeded)
            self.assertIsNotNone(result.error_contract_path)
            contract = json.loads(result.error_contract_path.read_text(encoding="utf-8"))

            self.assertEqual(contract["agent"], "build-agent")
            self.assertEqual(contract["status"], "failed")
            self.assertEqual(contract["result_kind"], "command_error")
            self.assertEqual(contract["build_tool"], None)

    def test_successful_build_updates_ledger(self) -> None:
        with workspace_temp_dir() as tmp:
            ledger_file = tmp / ".migration" / "ledger.json"
            initialize_ledger(
                ledger_file,
                migration_id="test",
                migration_name="Test",
                total_units=1,
                target_path=tmp,
            )
            mark_unit_in_progress(ledger_file, unit_id="unit-001", unit_index=0, title="Unit 1")
            mark_unit_awaiting_build(ledger_file, unit_id="unit-001")

            project_info = JavaProjectInfo(
                path=tmp,
                build_tool=BuildTool.MAVEN,
                base_command=["mvn", "spring-boot:run"],
                uses_wrapper=False,
            )
            process_result = ProcessRunResult(
                classification=BuildClassification(
                    BuildResultKind.SUCCESS,
                    "Application started successfully",
                    "Started DemoApplication",
                ),
                exit_code=0,
            )

            with patch("migration_factory.agents.build_agent.agent.detect_java_project", return_value=project_info):
                with patch("migration_factory.agents.build_agent.agent.run_until_build_result", return_value=process_result):
                    result = run_build_agent(tmp, ledger_file=ledger_file, stream_output=False)

            ledger = load_ledger(ledger_file)
            self.assertTrue(result.succeeded)
            self.assertEqual(ledger["build_validation"]["status"], BuildValidationStatus.PASSED)
            self.assertEqual(ledger["completed_units"], ["unit-001"])
            self.assertEqual(ledger["build_validation"]["command"], ["mvn", "spring-boot:run"])
            self.assertEqual(ledger["build_validation"]["cwd"], str(tmp))
            self.assertIn("command_duration_seconds", ledger["build_validation"])
            self.assertGreaterEqual(ledger["build_validation"]["command_duration_seconds"], 0)


def _write_multi_module_project(project: Path) -> None:
    (project / "pom.xml").write_text(
        """<project>
  <modelVersion>4.0.0</modelVersion>
  <packaging>pom</packaging>
  <modules>
    <module>shoppoc-user</module>
    <module>shoppoc-app</module>
  </modules>
</project>""",
        encoding="utf-8",
    )
    (project / "shoppoc-user").mkdir()
    (project / "shoppoc-user" / "pom.xml").write_text("<project />", encoding="utf-8")
    app = project / "shoppoc-app"
    app.mkdir()
    (app / "pom.xml").write_text("<project />", encoding="utf-8")
    app_source = app / "src" / "main" / "java" / "com" / "shoppoc" / "app"
    app_source.mkdir(parents=True)
    (app_source / "ShoppocApplication.java").write_text(
        """package com.shoppoc.app;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

@SpringBootApplication
public class ShoppocApplication {
    public static void main(String[] args) {
        SpringApplication.run(ShoppocApplication.class, args);
    }
}
""",
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
