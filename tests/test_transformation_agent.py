from __future__ import annotations

import json
import unittest
from unittest import mock
import io
from pathlib import Path
from contextlib import redirect_stderr, redirect_stdout

import yaml

from migration_factory.contracts.build import BuildRunResult
from migration_factory.agents.build_agent.classifier import BuildClassification, BuildResultKind
from migration_factory.agents.build_agent.runner import ProcessRunResult
from migration_factory.agents.transformation_agent.agent import (
    TransformationAgentError,
    TransformationRunResult,
)
from helpers import workspace_temp_dir
from migration_factory.agents.transformation_agent.execution_plan import (
    TransformationExecutionPlanError,
    write_transformation_execution_plan,
)
from migration_factory.agents.transformation_agent.executor import CommandResult
from migration_factory.agents.transformation_agent.plan import load_migration_plan
from migration_factory.agents.transformation_agent.pom_patches import (
    detect_spring_boot_version,
    is_stable_spring_boot_35_version,
    patch_batch_config_flat_file_item_reader_constructor,
    patch_maven_enforcer_java_version,
    patch_pom_property,
    patch_security_config_authorize_http_requests,
)
from migration_factory.approval import write_approval_decision, write_approved_plan_lock
from migration_factory.agents.transformation_agent import run_transformation_agent
from migration_factory.agents.transformation_agent.workspace import (
    TransformationWorkspaceError,
    prepare_sandbox_workspace,
)
from migration_factory.agents.test_agent.agent import TestAgentResult as _TestAgentResult
from migration_factory.agents.transformation_agent import workspace as workspace_module
from migration_factory import transform_v1_after_approval as transform_module
from migration_factory.contracts.migration import (
    BuildValidationStatus,
    LedgerStatus,
    initialize_ledger,
    load_ledger,
    mark_build_failed,
    mark_build_passed,
    mark_unit_awaiting_build,
    mark_unit_in_progress,
)
from migration_factory.transform_v1_after_approval import main as transform_v1_after_approval_main


PLUGIN_XML = """<plugin>
  <groupId>org.openrewrite.maven</groupId>
  <artifactId>rewrite-maven-plugin</artifactId>
  <version>6.23.0</version>
</plugin>
"""


PLAN_YAML = """schema_version: "1.3"
migration:
  id: test-migration
  name: Test Migration
workspaces:
  target:
    path: ./modernized-app
    migration_dir: .migration
    ledger_file: .migration/ledger.json
migration_units:
  - id: unit-001
    title: First Unit
    expected_files:
      - pom.xml
    transformations:
      - type: custom_code_change
        description: record only
    checks:
      - id: compile
        command: mvn clean compile
        required: true
"""


class TransformationAgentTests(unittest.TestCase):
    def test_execution_plan_adapter_writes_current_transformer_yaml(self) -> None:
        with workspace_temp_dir() as tmp:
            app = tmp / "modernized-app"
            run_id = "run-1"
            _write_approved_run_artifacts(app, run_id, include_rewrite_plan=True)

            output_path = write_transformation_execution_plan(app, run_id)
            payload = yaml.safe_load(output_path.read_text(encoding="utf-8"))
            loaded_plan = load_migration_plan(output_path)

            self.assertEqual(
                output_path,
                app
                / ".migration"
                / "runs"
                / run_id
                / "transformation"
                / "transformation_execution_plan.yaml",
            )
            self.assertEqual(payload["schema_version"], "1.3")
            self.assertEqual(payload["migration"]["id"], run_id)
            self.assertEqual(payload["workspaces"]["target"]["path"], str(app.resolve()))
            self.assertEqual([unit["id"] for unit in payload["migration_units"]], ["baseline", "java-17"])
            self.assertEqual(payload["migration_units"][0]["checks"][0]["command"], "mvn clean test")
            self.assertEqual(payload["migration_units"][1]["transformations"][0]["type"], "openrewrite")
            self.assertEqual(
                payload["migration_units"][1]["transformations"][0]["active_recipes"],
                ["org.openrewrite.java.migrate.UpgradeToJava17"],
            )
            self.assertEqual(
                payload["migration_units"][1]["transformations"][0]["recipe_artifacts"],
                ["org.openrewrite.recipe:rewrite-migrate-java:3.20.0"],
            )
            self.assertEqual(loaded_plan.migration_id, run_id)
            self.assertEqual([unit.id for unit in loaded_plan.units], ["baseline", "java-17"])

    def test_execution_plan_adapter_rejects_missing_approval_decision(self) -> None:
        with workspace_temp_dir() as tmp:
            app = tmp / "modernized-app"
            run_id = "run-1"
            _write_run_artifacts(app, run_id)
            write_approved_plan_lock(_run_dir(app, run_id), run_id)

            with self.assertRaisesRegex(
                TransformationExecutionPlanError,
                "approval_decision.json missing",
            ):
                write_transformation_execution_plan(app, run_id)

    def test_execution_plan_adapter_rejects_invalid_plan_lock(self) -> None:
        with workspace_temp_dir() as tmp:
            app = tmp / "modernized-app"
            run_id = "run-1"
            _write_approved_run_artifacts(app, run_id)
            units_path = _run_dir(app, run_id) / "planning" / "migration_units.yaml"
            units_path.write_text(units_path.read_text(encoding="utf-8") + "# changed\n", encoding="utf-8")

            with self.assertRaisesRegex(
                TransformationExecutionPlanError,
                "approved_plan_lock.json artifact hashes do not match current run artifacts",
            ):
                write_transformation_execution_plan(app, run_id)

    def test_transformation_agent_initializes_ledger_and_waits_for_build(self) -> None:
        with workspace_temp_dir() as tmp:
            app = tmp / "modernized-app"
            app.mkdir()
            (app / "pom.xml").write_text("<project />", encoding="utf-8")
            plan = tmp / "plan.yaml"
            plan.write_text(PLAN_YAML, encoding="utf-8")
            plugin = tmp / "rewrite-plugin.txt"
            plugin.write_text(PLUGIN_XML, encoding="utf-8")

            result = run_transformation_agent(app, plugin, plan, dry_run=True, wait_for_continue=False)
            ledger = load_ledger(result.ledger_file)

            self.assertEqual(result.status, LedgerStatus.AWAITING_BUILD_AGENT)
            self.assertEqual(ledger["current_unit"], "unit-001")
            self.assertEqual(ledger["build_validation"]["status"], BuildValidationStatus.PENDING)

    def test_baseline_unit_does_not_inject_openrewrite_plugin_into_pom(self) -> None:
        with workspace_temp_dir() as tmp:
            app = tmp / "modernized-app"
            app.mkdir()
            (app / "pom.xml").write_text("<project />", encoding="utf-8")
            plan = tmp / "plan.yaml"
            plan.write_text(
                PLAN_YAML.replace("unit-001", "baseline").replace("First Unit", "Baseline"),
                encoding="utf-8",
            )
            plugin = tmp / "rewrite-plugin.txt"
            plugin.write_text(PLUGIN_XML, encoding="utf-8")

            result = run_transformation_agent(app, plugin, plan, dry_run=True, wait_for_continue=False)

            self.assertEqual(result.status, LedgerStatus.AWAITING_BUILD_AGENT)
            self.assertNotIn("rewrite-maven-plugin", (app / "pom.xml").read_text(encoding="utf-8"))

    def test_openrewrite_transform_uses_fully_qualified_concrete_plugin_goal(self) -> None:
        with workspace_temp_dir() as tmp:
            app = tmp / "modernized-app"
            app.mkdir()
            (app / "pom.xml").write_text("<project />", encoding="utf-8")
            plan = tmp / "plan.yaml"
            plan.write_text(
                """
schema_version: "1.3"
migration:
  id: test-migration
  name: Test Migration
workspaces:
  target:
    path: ./modernized-app
    migration_dir: .migration
    ledger_file: .migration/ledger.json
migration_units:
  - id: java-17
    title: Java 17
    transformations:
      - type: openrewrite
        active_recipes:
          - org.openrewrite.java.migrate.UpgradeToJava17
        recipe_artifacts:
          - org.openrewrite.recipe:rewrite-migrate-java:RELEASE
    checks: []
""",
                encoding="utf-8",
            )
            plugin = tmp / "rewrite-plugin.txt"
            plugin.write_text(
                PLUGIN_XML.replace("<version>6.23.0</version>", "<version>RELEASE</version>"),
                encoding="utf-8",
            )

            result = run_transformation_agent(app, plugin, plan, dry_run=True, wait_for_continue=False)
            ledger = load_ledger(result.ledger_file)
            command = ledger["units"]["java-17"]["commands"][0]["command"]

            self.assertIn("org.openrewrite.maven:rewrite-maven-plugin:6.39.0:run", command)
            self.assertIn("-Drewrite.recipeArtifactCoordinates=org.openrewrite.recipe:rewrite-migrate-java:RELEASE", command)
            self.assertNotIn("rewrite:run", command)
            self.assertNotIn("rewrite-maven-plugin:RELEASE", command)

    def test_openrewrite_apply_uses_configured_goal_and_maven_args(self) -> None:
        with workspace_temp_dir() as tmp:
            app = tmp / "modernized-app"
            app.mkdir()
            (app / "pom.xml").write_text("<project />", encoding="utf-8")
            plan = tmp / "plan.yaml"
            plan.write_text(
                """
schema_version: "1.3"
migration:
  id: test-migration
  name: Test Migration
workspaces:
  target:
    path: ./modernized-app
    migration_dir: .migration
    ledger_file: .migration/ledger.json
migration_units:
  - id: java-21
    title: Java 21
    transformations:
      - type: openrewrite
        apply_goal: runNoFork
        apply_maven_args:
          - -Denforcer.skip=true
        active_recipes:
          - org.openrewrite.java.migrate.UpgradeToJava21
        recipe_artifacts:
          - org.openrewrite.recipe:rewrite-migrate-java:RELEASE
    checks: []
""",
                encoding="utf-8",
            )
            plugin = tmp / "rewrite-plugin.txt"
            plugin.write_text(PLUGIN_XML, encoding="utf-8")

            result = run_transformation_agent(app, plugin, plan, dry_run=True, wait_for_continue=False)
            ledger = load_ledger(result.ledger_file)
            command = ledger["units"]["java-21"]["commands"][0]["command"]

            self.assertIn("org.openrewrite.maven:rewrite-maven-plugin:6.23.0:runNoFork", command)
            self.assertIn("-Denforcer.skip=true", command)

    def test_openrewrite_apply_uses_lifecycle_forking_goal_for_maven_reactor(self) -> None:
        with workspace_temp_dir() as tmp:
            app = tmp / "modernized-app"
            app.mkdir()
            (app / "pom.xml").write_text(
                """
<project>
  <packaging>pom</packaging>
  <modules>
    <module>shared</module>
  </modules>
</project>
""".strip(),
                encoding="utf-8",
            )
            (app / "shared").mkdir()
            (app / "shared" / "pom.xml").write_text("<project />", encoding="utf-8")
            plan = tmp / "plan.yaml"
            plan.write_text(
                """
schema_version: "1.3"
migration:
  id: test-migration
  name: Test Migration
workspaces:
  target:
    path: ./modernized-app
    migration_dir: .migration
    ledger_file: .migration/ledger.json
migration_units:
  - id: java-17
    title: Java 17
    transformations:
      - type: openrewrite
        apply_goal: runNoFork
        active_recipes:
          - org.openrewrite.java.migrate.UpgradeToJava17
    checks: []
""",
                encoding="utf-8",
            )
            plugin = tmp / "rewrite-plugin.txt"
            plugin.write_text(PLUGIN_XML, encoding="utf-8")

            result = run_transformation_agent(app, plugin, plan, dry_run=True, wait_for_continue=False)
            ledger = load_ledger(result.ledger_file)
            command = ledger["units"]["java-17"]["commands"][0]["command"]

            self.assertIn("org.openrewrite.maven:rewrite-maven-plugin:6.23.0:run", command)
            self.assertNotIn("org.openrewrite.maven:rewrite-maven-plugin:6.23.0:runNoFork", command)

    def test_openrewrite_apply_settings_are_loaded_from_profile_and_catalog(self) -> None:
        with workspace_temp_dir() as tmp:
            app = tmp / "modernized-app"
            app.mkdir()
            run_id = "run-1"
            _write_approved_run_artifacts(
                app,
                run_id,
                include_rewrite_plan=True,
                source_unit_id="java-21",
                source_unit_goal="Upgrade project runtime to Java 21.",
            )
            ai_hub = tmp / "ai-hub"
            _write_ai_hub_profile(
                ai_hub,
                extra_profile_yaml="""
  apply_goal: runNoFork
  apply_maven_args:
    - -Denforcer.skip=true
""",
            )
            plan_path = write_transformation_execution_plan(app, run_id)
            transform_module._apply_openrewrite_apply_settings(plan_path, str(ai_hub), "java17")
            plan = load_migration_plan(plan_path, app)
            transformation = plan.units[1].transformations[0]

            self.assertEqual(transformation["apply_goal"], "runNoFork")
            self.assertEqual(transformation["apply_maven_args"], ["-Denforcer.skip=true"])

    def test_maven_enforcer_java8_range_patch_updates_to_java21_range(self) -> None:
        for legacy_range in ("[1.8,1.9)", "[8,9)", "1.8", "8"):
            with self.subTest(legacy_range=legacy_range), workspace_temp_dir() as tmp:
                app = tmp / "modernized-app"
                app.mkdir()
                (app / "pom.xml").write_text(
                    f"""<project>
  <build>
    <plugins>
      <plugin>
        <artifactId>maven-enforcer-plugin</artifactId>
        <configuration>
          <rules>
            <requireJavaVersion>
              <version>{legacy_range}</version>
            </requireJavaVersion>
          </rules>
        </configuration>
      </plugin>
    </plugins>
  </build>
</project>
""",
                    encoding="utf-8",
                )

                patches = patch_maven_enforcer_java_version(app, unit_id="java-21")

                self.assertEqual(len(patches), 1)
                self.assertEqual(patches[0].old_range, legacy_range)
                self.assertEqual(patches[0].new_range, "[21,)")
                self.assertIn("<version>[21,)</version>", (app / "pom.xml").read_text(encoding="utf-8"))

    def test_pom_property_patch_updates_archunit_java21_version(self) -> None:
        with workspace_temp_dir() as tmp:
            app = tmp / "modernized-app"
            app.mkdir()
            (app / "pom.xml").write_text(
                """<project>
  <properties>
    <archunit.version>0.23.1</archunit.version>
  </properties>
</project>
""",
                encoding="utf-8",
            )

            patches = patch_pom_property(
                app,
                unit_id="java-21",
                property_name="archunit.version",
                old_value="0.23.1",
                new_value="1.4.1",
            )

            self.assertEqual(len(patches), 1)
            self.assertEqual(patches[0].property, "archunit.version")
            self.assertEqual(patches[0].old_value, "0.23.1")
            self.assertEqual(patches[0].new_value, "1.4.1")
            self.assertIn(
                "<archunit.version>1.4.1</archunit.version>",
                (app / "pom.xml").read_text(encoding="utf-8"),
            )

    def test_spring_boot_version_patch_updates_properties_and_dependencies(self) -> None:
        with workspace_temp_dir() as tmp:
            app = tmp / "modernized-app"
            app.mkdir()
            (app / "pom.xml").write_text(
                """<project>
  <properties>
    <spring-boot.version>3.5.14</spring-boot.version>
    <org.springframework.version>3.5.14</org.springframework.version>
  </properties>
  <dependencies>
    <dependency>
      <groupId>org.springframework.boot</groupId>
      <artifactId>spring-boot-starter-web</artifactId>
      <version>3.5.14</version>
    </dependency>
  </dependencies>
</project>
""",
                encoding="utf-8",
            )

            patches = transform_module.patch_spring_boot_version(
                app,
                unit_id="spring-boot-3-5",
                old_value="3.5.14",
                new_value="3.5.6",
            )

            self.assertEqual(len(patches), 3)
            pom_text = (app / "pom.xml").read_text(encoding="utf-8")
            self.assertIn("<spring-boot.version>3.5.6</spring-boot.version>", pom_text)
            self.assertIn("<org.springframework.version>3.5.6</org.springframework.version>", pom_text)
            self.assertIn("<version>3.5.6</version>", pom_text)

    def test_spring_boot_version_accepts_35_patch_line_when_profile_targets_35(self) -> None:
        self.assertTrue(is_stable_spring_boot_35_version("3.5.6"))
        self.assertTrue(is_stable_spring_boot_35_version("3.5.15"))
        self.assertFalse(is_stable_spring_boot_35_version("3.4.9"))
        self.assertFalse(is_stable_spring_boot_35_version("3.6.0"))
        self.assertFalse(is_stable_spring_boot_35_version("3.5.15-SNAPSHOT"))

    def test_spring_boot_version_required_patch_accepts_already_migrated_property_version(self) -> None:
        with workspace_temp_dir() as tmp:
            app = tmp / "modernized-app"
            app.mkdir()
            (app / "pom.xml").write_text(
                """<project>
  <properties>
    <spring-boot.version>3.5.15</spring-boot.version>
  </properties>
</project>
""",
                encoding="utf-8",
            )

            plan = _write_spring_boot_transform_plan(app, run_id="run-1")
            with mock.patch(
                "migration_factory.agents.transformation_agent.agent._verify_build_validation",
                return_value=BuildValidationStatus.PASSED,
            ) as verify_mock:
                result = run_transformation_agent(app, plan["plugin_xml"], plan["plan_yaml"], wait_for_continue=False)

            ledger = load_ledger(result.ledger_file)
            unit = ledger["units"]["spring-boot-3-5"]
            self.assertEqual(result.status, LedgerStatus.COMPLETED)
            self.assertEqual(ledger["status"], LedgerStatus.COMPLETED)
            self.assertEqual(unit["transformations"][0]["status"], "satisfied")
            self.assertEqual(unit["transformations"][0]["spring_boot_version_status"], "satisfied")
            self.assertEqual(unit["transformations"][0]["spring_boot_version_detected"], "3.5.15")
            self.assertEqual(unit["transformations"][0]["spring_boot_version_target"], "3.5.x")
            self.assertEqual(unit["transformations"][0]["spring_boot_version_location"], "property")
            verify_mock.assert_called_once()

    def test_spring_boot_version_accepts_bom_property_reference_35x(self) -> None:
        with workspace_temp_dir() as tmp:
            app = tmp / "modernized-app"
            app.mkdir()
            (app / "pom.xml").write_text(
                """<project>
  <properties>
    <spring.boot.version>3.5.15</spring.boot.version>
  </properties>
  <dependencyManagement>
    <dependencies>
      <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-dependencies</artifactId>
        <version>${spring.boot.version}</version>
        <type>pom</type>
        <scope>import</scope>
      </dependency>
    </dependencies>
  </dependencyManagement>
</project>
""",
                encoding="utf-8",
            )

            detection = detect_spring_boot_version(app)
            self.assertIsNotNone(detection)
            self.assertEqual(detection.version, "3.5.15")
            self.assertEqual(detection.location, "bom")

    def test_spring_boot_version_accepts_direct_bom_35x(self) -> None:
        with workspace_temp_dir() as tmp:
            app = tmp / "modernized-app"
            app.mkdir()
            (app / "pom.xml").write_text(
                """<project>
  <dependencyManagement>
    <dependencies>
      <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-dependencies</artifactId>
        <version>3.5.15</version>
        <type>pom</type>
        <scope>import</scope>
      </dependency>
    </dependencies>
  </dependencyManagement>
</project>
""",
                encoding="utf-8",
            )

            detection = detect_spring_boot_version(app)
            self.assertIsNotNone(detection)
            self.assertEqual(detection.version, "3.5.15")
            self.assertEqual(detection.location, "bom")

    def test_spring_boot_version_accepts_plugin_property_35x(self) -> None:
        with workspace_temp_dir() as tmp:
            app = tmp / "modernized-app"
            app.mkdir()
            (app / "pom.xml").write_text(
                """<project>
  <properties>
    <spring-boot.version>3.5.15</spring-boot.version>
  </properties>
  <build>
    <plugins>
      <plugin>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-maven-plugin</artifactId>
        <version>${spring-boot.version}</version>
      </plugin>
    </plugins>
  </build>
</project>
""",
                encoding="utf-8",
            )

            detection = detect_spring_boot_version(app)
            self.assertIsNotNone(detection)
            self.assertEqual(detection.version, "3.5.15")
            self.assertEqual(detection.location, "plugin")

    def test_spring_boot_version_rejects_34x_with_detected_expected_details(self) -> None:
        with workspace_temp_dir() as tmp:
            app = tmp / "modernized-app"
            app.mkdir()
            (app / "pom.xml").write_text(
                """<project>
  <dependencyManagement>
    <dependencies>
      <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-dependencies</artifactId>
        <version>3.4.9</version>
        <type>pom</type>
        <scope>import</scope>
      </dependency>
    </dependencies>
  </dependencyManagement>
</project>
""",
                encoding="utf-8",
            )

            plan = _write_spring_boot_transform_plan(app, run_id="run-reject-34")
            with self.assertRaises(TransformationAgentError) as exc:
                run_transformation_agent(app, plan["plugin_xml"], plan["plan_yaml"], wait_for_continue=False)

            self.assertIn("REQUIRED_POM_PATCH_NOT_APPLIED spring_boot_version", str(exc.exception))
            self.assertIn("detected_version=3.4.9", str(exc.exception))
            self.assertIn("expected_target_line=3.5.x", str(exc.exception))
            self.assertIn("detected_location=bom", str(exc.exception))
            ledger = load_ledger(app / ".migration" / "ledger.json")
            self.assertIn("detected_version=3.4.9", ledger["units"]["spring-boot-3-5"]["blocking_reason"])

    def test_spring_boot_version_rejects_36x_with_detected_expected_details(self) -> None:
        with workspace_temp_dir() as tmp:
            app = tmp / "modernized-app"
            app.mkdir()
            (app / "pom.xml").write_text(
                """<project>
  <parent>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-parent</artifactId>
    <version>3.6.0</version>
  </parent>
</project>
""",
                encoding="utf-8",
            )

            plan = _write_spring_boot_transform_plan(app, run_id="run-reject-36")
            with self.assertRaises(TransformationAgentError) as exc:
                run_transformation_agent(app, plan["plugin_xml"], plan["plan_yaml"], wait_for_continue=False)

            self.assertIn("REQUIRED_POM_PATCH_NOT_APPLIED spring_boot_version", str(exc.exception))
            self.assertIn("detected_version=3.6.0", str(exc.exception))
            self.assertIn("expected_target_line=3.5.x", str(exc.exception))
            self.assertIn("detected_location=parent", str(exc.exception))
            ledger = load_ledger(app / ".migration" / "ledger.json")
            self.assertIn("detected_version=3.6.0", ledger["units"]["spring-boot-3-5"]["blocking_reason"])

    def test_spring_boot_version_rejects_missing_version_with_detected_locations_empty(self) -> None:
        with workspace_temp_dir() as tmp:
            app = tmp / "modernized-app"
            app.mkdir()
            (app / "pom.xml").write_text("<project><properties /></project>", encoding="utf-8")

            plan = _write_spring_boot_transform_plan(app, run_id="run-reject-missing")
            with self.assertRaises(TransformationAgentError) as exc:
                run_transformation_agent(app, plan["plugin_xml"], plan["plan_yaml"], wait_for_continue=False)

            self.assertIn("REQUIRED_POM_PATCH_NOT_APPLIED spring_boot_version", str(exc.exception))
            self.assertIn("detected_locations=[]", str(exc.exception))
            self.assertIn("expected_target_line=3.5.x", str(exc.exception))
            ledger = load_ledger(app / ".migration" / "ledger.json")
            self.assertIn("detected_locations=[]", ledger["units"]["spring-boot-3-5"]["blocking_reason"])

    def test_stage2_does_not_stop_before_build_when_pom_already_satisfies_boot_target(self) -> None:
        with workspace_temp_dir() as tmp:
            app = tmp / "modernized-app"
            app.mkdir()
            (app / "pom.xml").write_text(
                """<project>
  <properties>
    <spring-boot.version>3.5.15</spring-boot.version>
  </properties>
  <dependencyManagement>
    <dependencies>
      <dependency>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-dependencies</artifactId>
        <version>${spring-boot.version}</version>
        <type>pom</type>
        <scope>import</scope>
      </dependency>
    </dependencies>
  </dependencyManagement>
</project>
""",
                encoding="utf-8",
            )

            plan = _write_spring_boot_transform_plan(app, run_id="run-stage2")
            with mock.patch(
                "migration_factory.agents.transformation_agent.agent._verify_build_validation",
                return_value=BuildValidationStatus.PASSED,
            ):
                result = run_transformation_agent(app, plan["plugin_xml"], plan["plan_yaml"], wait_for_continue=False)

            ledger = load_ledger(result.ledger_file)
            self.assertEqual(result.status, LedgerStatus.COMPLETED)
            self.assertEqual(ledger["status"], LedgerStatus.COMPLETED)
            self.assertEqual(ledger["units"]["spring-boot-3-5"]["transformations"][0]["status"], "satisfied")

    def test_boot4_source_patches_update_security_and_batch_config(self) -> None:
        with workspace_temp_dir() as tmp:
            app = tmp / "modernized-app"
            security = app / "src/main/java/com/example/flightapp/config/SecurityConfig.java"
            batch = app / "src/main/java/com/example/flightapp/batch/config/BatchConfig.java"
            security.parent.mkdir(parents=True)
            batch.parent.mkdir(parents=True)
            security.write_text(
                "return new InMemoryUserDetailsManager(User.builder().username(\"viewer\").build());\n"
                "http.authorizeRequests(auth -> auth.requestMatchers(\"/actuator/health\").permitAll());\n",
                encoding="utf-8",
            )
            batch.write_text(
                "FlatFileItemReader<FlightCsvRow> reader = new FlatFileItemReader<FlightCsvRow>();\n"
                "reader.setResource(resolveInput(fileName));\n"
                "reader.setLinesToSkip(1);\n"
                "DefaultLineMapper<FlightCsvRow> lineMapper = new DefaultLineMapper<FlightCsvRow>();\n"
                "lineMapper.setLineTokenizer(tokenizer);\n"
                "lineMapper.setFieldSetMapper(fieldSetMapper);\n"
                "reader.setLineMapper(lineMapper);\n"
                "return reader;\n",
                encoding="utf-8",
            )

            security_patches = patch_security_config_authorize_http_requests(app, unit_id="java-21")
            batch_patches = patch_batch_config_flat_file_item_reader_constructor(app, unit_id="java-21")

            self.assertEqual(len(security_patches), 1)
            self.assertIn(".authorizeHttpRequests(", security.read_text(encoding="utf-8"))
            self.assertIn('.roles("ADMIN")', security.read_text(encoding="utf-8"))
            self.assertIn('.roles("AGENT")', security.read_text(encoding="utf-8"))
            self.assertIn('.roles("VIEWER")', security.read_text(encoding="utf-8"))
            self.assertEqual(len(batch_patches), 1)
            self.assertIn(
                "new FlatFileItemReader<FlightCsvRow>(lineMapper)",
                batch.read_text(encoding="utf-8"),
            )

    def test_boot4_java21_profile_adds_post_openrewrite_enforcer_patch(self) -> None:
        with workspace_temp_dir() as tmp:
            app = tmp / "modernized-app"
            app.mkdir()
            run_id = "run-1"
            _write_approved_run_artifacts(
                app,
                run_id,
                include_rewrite_plan=True,
                source_unit_id="java-21",
                source_unit_goal="Upgrade project runtime to Java 21.",
            )
            ai_hub = tmp / "ai-hub"
            _write_ai_hub_profile(
                ai_hub,
                extra_profile_yaml="""
  apply_goal: runNoFork
  apply_maven_args:
    - -Denforcer.skip=true
  post_apply_patches:
    - type: maven_enforcer_java_version
      target_range: "[21,)"
    - type: pom_property
      property: archunit.version
      old_value: 0.23.1
      new_value: 1.4.1
""",
            )
            plan_path = write_transformation_execution_plan(app, run_id)

            transform_module._apply_openrewrite_apply_settings(plan_path, str(ai_hub), "java17")
            plan = load_migration_plan(plan_path, app)
            transformations = plan.units[1].transformations

            self.assertEqual(transformations[0]["type"], "openrewrite")
            self.assertEqual(transformations[1]["type"], "maven_enforcer_java_version")
            self.assertEqual(transformations[1]["target_range"], "[21,)")
            self.assertEqual(transformations[2]["type"], "pom_property")
            self.assertEqual(transformations[2]["property"], "archunit.version")

    def test_openrewrite_then_pom_property_patch_runs_before_java21_validation(self) -> None:
        with workspace_temp_dir() as tmp:
            app = tmp / "modernized-app"
            app.mkdir()
            (app / "pom.xml").write_text(
                "<project><properties><archunit.version>0.23.1</archunit.version></properties>"
                "<build><plugins><plugin><artifactId>maven-enforcer-plugin</artifactId>"
                "<configuration><rules><requireJavaVersion><version>[1.8,1.9)</version>"
                "</requireJavaVersion></rules></configuration></plugin></plugins></build></project>",
                encoding="utf-8",
            )
            plan = tmp / "plan.yaml"
            plan.write_text(
                """
schema_version: "1.3"
migration:
  id: test-migration
  name: Test Migration
workspaces:
  target:
    path: ./modernized-app
    migration_dir: .migration
    ledger_file: .migration/ledger.json
migration_units:
  - id: java-21
    title: Java 21
    transformations:
      - type: openrewrite
        apply_goal: runNoFork
        apply_maven_args:
          - -Denforcer.skip=true
        active_recipes:
          - org.openrewrite.java.migrate.UpgradeToJava21
      - type: maven_enforcer_java_version
        target_range: "[21,)"
      - type: pom_property
        property: archunit.version
        old_value: 0.23.1
        new_value: 1.4.1
    checks: []
""",
                encoding="utf-8",
            )
            plugin = tmp / "rewrite-plugin.txt"
            plugin.write_text(PLUGIN_XML, encoding="utf-8")

            with mock.patch(
                "migration_factory.agents.transformation_agent.agent.run_command",
                return_value=CommandResult(
                    command="mvn",
                    exit_code=0,
                    stdout=[],
                    stderr=[],
                    duration_seconds=0.01,
                ),
            ) as run_command:
                result = run_transformation_agent(app, plugin, plan, wait_for_continue=False)

            ledger = load_ledger(result.ledger_file)
            command = run_command.call_args.args[0]
            transformations = ledger["units"]["java-21"]["transformations"]
            self.assertIn("-Denforcer.skip=true", command)
            self.assertEqual(transformations[0]["type"], "maven_enforcer_java_version")
            self.assertEqual(transformations[0]["status"], "applied")
            self.assertEqual(transformations[0]["patches"][0]["old_range"], "[1.8,1.9)")
            self.assertEqual(transformations[1]["type"], "pom_property")
            self.assertEqual(transformations[1]["status"], "applied")
            self.assertEqual(transformations[1]["patches"][0]["property"], "archunit.version")
            self.assertEqual(transformations[1]["patches"][0]["old_value"], "0.23.1")
            self.assertEqual(transformations[1]["patches"][0]["new_value"], "1.4.1")
            self.assertEqual(ledger["build_validation"]["unit_id"], "java-21")
            self.assertIn("<version>[21,)</version>", (app / "pom.xml").read_text(encoding="utf-8"))
            self.assertIn(
                "<archunit.version>1.4.1</archunit.version>",
                (app / "pom.xml").read_text(encoding="utf-8"),
            )
            run_command.assert_called_once()

    def test_required_enforcer_patch_missing_match_fails_before_validation(self) -> None:
        with workspace_temp_dir() as tmp:
            app = tmp / "modernized-app"
            app.mkdir()
            (app / "pom.xml").write_text(
                "<project><build><plugins><plugin><artifactId>maven-enforcer-plugin</artifactId>"
                "<configuration><rules><requireJavaVersion><version>[17,)</version>"
                "</requireJavaVersion></rules></configuration></plugin></plugins></build></project>",
                encoding="utf-8",
            )
            plan = tmp / "plan.yaml"
            plan.write_text(
                """
schema_version: "1.3"
migration:
  id: test-migration
  name: Test Migration
workspaces:
  target:
    path: ./modernized-app
    migration_dir: .migration
    ledger_file: .migration/ledger.json
migration_units:
  - id: java-21
    title: Java 21
    transformations:
      - type: openrewrite
        apply_goal: runNoFork
        active_recipes:
          - org.openrewrite.java.migrate.UpgradeToJava21
      - type: maven_enforcer_java_version
        target_range: "[21,)"
    checks: []
""",
                encoding="utf-8",
            )
            plugin = tmp / "rewrite-plugin.txt"
            plugin.write_text(PLUGIN_XML, encoding="utf-8")

            with mock.patch(
                "migration_factory.agents.transformation_agent.agent.run_command",
                return_value=CommandResult(
                    command="mvn",
                    exit_code=0,
                    stdout=[],
                    stderr=[],
                    duration_seconds=0.01,
                ),
            ):
                with self.assertRaisesRegex(
                    TransformationAgentError,
                    "REQUIRED_POM_PATCH_NOT_APPLIED maven_enforcer_java_version",
                ):
                    run_transformation_agent(app, plugin, plan, wait_for_continue=False)

            ledger = load_ledger(app / ".migration" / "ledger.json")
            self.assertEqual(ledger["status"], LedgerStatus.BLOCKED)
            self.assertEqual(ledger["blocked_unit"], "java-21")
            self.assertEqual(
                ledger["units"]["java-21"]["blocking_reason"],
                "REQUIRED_POM_PATCH_NOT_APPLIED maven_enforcer_java_version",
            )
            self.assertEqual(ledger["build_validation"]["status"], BuildValidationStatus.NOT_REQUIRED)

    def test_baseline_unit_leaves_pom_untouched_before_baseline_validation(self) -> None:
        with workspace_temp_dir() as tmp:
            app = tmp / "modernized-app"
            app.mkdir()
            original_pom = (
                "<project><build><plugins><plugin><artifactId>maven-enforcer-plugin</artifactId>"
                "<configuration><rules><requireJavaVersion><version>[1.8,1.9)</version>"
                "</requireJavaVersion></rules></configuration></plugin></plugins></build></project>"
            )
            (app / "pom.xml").write_text(original_pom, encoding="utf-8")
            plan = tmp / "plan.yaml"
            plan.write_text(
                """
schema_version: "1.3"
migration:
  id: test-migration
  name: Test Migration
workspaces:
  target:
    path: ./modernized-app
    migration_dir: .migration
    ledger_file: .migration/ledger.json
migration_units:
  - id: baseline
    title: Baseline
    transformations:
      - type: custom_code_change
        description: baseline validation only
    checks: []
  - id: java-21
    title: Java 21
    transformations:
      - type: maven_enforcer_java_version
        target_range: "[21,)"
    checks: []
""",
                encoding="utf-8",
            )
            plugin = tmp / "rewrite-plugin.txt"
            plugin.write_text(PLUGIN_XML, encoding="utf-8")

            result = run_transformation_agent(app, plugin, plan, wait_for_continue=False)
            ledger = load_ledger(result.ledger_file)

            self.assertEqual(ledger["build_validation"]["unit_id"], "baseline")
            self.assertEqual((app / "pom.xml").read_text(encoding="utf-8"), original_pom)

    def test_java17_profile_does_not_add_enforcer_patch_without_configuration(self) -> None:
        with workspace_temp_dir() as tmp:
            app = tmp / "modernized-app"
            app.mkdir()
            run_id = "run-1"
            _write_approved_run_artifacts(app, run_id, include_rewrite_plan=True)
            ai_hub = tmp / "ai-hub"
            _write_ai_hub_profile(ai_hub)
            plan_path = write_transformation_execution_plan(app, run_id)

            transform_module._apply_openrewrite_apply_settings(plan_path, str(ai_hub), "java17")
            payload = yaml.safe_load(plan_path.read_text(encoding="utf-8"))

            self.assertNotIn(
                "maven_enforcer_java_version",
                json.dumps(payload),
            )

    def test_build_ledger_pass_marks_unit_completed(self) -> None:
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

            mark_build_passed(ledger_file, result_kind="success", message="Application started")
            ledger = json.loads(ledger_file.read_text(encoding="utf-8"))

            self.assertEqual(ledger["status"], LedgerStatus.BUILD_VALIDATED)
            self.assertEqual(ledger["build_validation"]["status"], BuildValidationStatus.PASSED)
            self.assertEqual(ledger["completed_units"], ["unit-001"])

    def test_build_ledger_failure_blocks_unit(self) -> None:
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

            mark_build_failed(
                ledger_file,
                result_kind="compilation_error",
                message="Compile failed",
                error_contract_path=tmp / "build-error.json",
            )
            ledger = load_ledger(ledger_file)

            self.assertEqual(ledger["status"], LedgerStatus.BLOCKED)
            self.assertEqual(ledger["blocked_unit"], "unit-001")
            self.assertEqual(ledger["build_validation"]["status"], BuildValidationStatus.FAILED)

    def test_prepare_sandbox_workspace_copies_legacy_with_exclusions_and_checkpoint(self) -> None:
        with workspace_temp_dir() as tmp:
            legacy = tmp / "legacy-app"
            modernized = tmp / "modernized-app"
            run_dir = modernized / ".migration" / "runs" / "run-1"
            legacy.mkdir()
            modernized.mkdir()
            (legacy / "pom.xml").write_text("<project />", encoding="utf-8")
            (legacy / ".git").mkdir()
            (legacy / ".git" / "config").write_text("source git", encoding="utf-8")
            (legacy / ".migration").mkdir()
            (legacy / ".migration" / "ledger.json").write_text("{}", encoding="utf-8")
            (legacy / "target").mkdir()
            (legacy / "target" / "classes.txt").write_text("compiled", encoding="utf-8")
            (legacy / "build").mkdir()
            (legacy / "build" / "output.txt").write_text("built", encoding="utf-8")
            (legacy / "node_modules").mkdir()
            (legacy / "node_modules" / "package.txt").write_text("dependency", encoding="utf-8")
            (legacy / "__pycache__").mkdir()
            (legacy / "__pycache__" / "module.pyc").write_text("cache", encoding="utf-8")
            (modernized / "marker.txt").write_text("do not change", encoding="utf-8")

            sandbox = prepare_sandbox_workspace(
                legacy_app_path=legacy,
                modernized_app_path=modernized,
                run_dir=run_dir,
            )

            self.assertEqual(sandbox.path, run_dir / "workspaces" / "sandbox")
            self.assertEqual((sandbox.path / "pom.xml").read_text(encoding="utf-8"), "<project />")
            self.assertFalse((sandbox.path / ".migration").exists())
            self.assertFalse((sandbox.path / "target").exists())
            self.assertFalse((sandbox.path / "build").exists())
            self.assertFalse((sandbox.path / "node_modules").exists())
            self.assertFalse((sandbox.path / "__pycache__").exists())
            self.assertEqual((legacy / "pom.xml").read_text(encoding="utf-8"), "<project />")
            self.assertEqual((modernized / "marker.txt").read_text(encoding="utf-8"), "do not change")
            self.assertIn(sandbox.checkpoint_type, {"git", "manifest"})
            if sandbox.checkpoint_type == "git":
                self.assertTrue((sandbox.path / ".git").is_dir())
                self.assertRegex(sandbox.checkpoint_ref, r"^[0-9a-f]{40}$")
            else:
                self.assertTrue(Path(sandbox.checkpoint_ref).is_file())

    def test_prepare_sandbox_workspace_writes_manifest_without_git(self) -> None:
        with workspace_temp_dir() as tmp:
            legacy = tmp / "legacy-app"
            modernized = tmp / "modernized-app"
            run_dir = modernized / ".migration" / "runs" / "run-1"
            legacy.mkdir()
            modernized.mkdir()
            (legacy / "pom.xml").write_text("<project />", encoding="utf-8")

            with mock.patch("migration_factory.agents.transformation_agent.workspace.shutil.which", return_value=None):
                sandbox = prepare_sandbox_workspace(
                    legacy_app_path=legacy,
                    modernized_app_path=modernized,
                    run_dir=run_dir,
                )

            manifest_path = sandbox.path / "baseline_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(sandbox.checkpoint_type, "manifest")
            self.assertEqual(Path(sandbox.checkpoint_ref), manifest_path)
            self.assertEqual([entry["path"] for entry in manifest["files"]], ["pom.xml"])

    def test_prepare_sandbox_workspace_rejects_sandbox_outside_run_dir(self) -> None:
        with workspace_temp_dir() as tmp:
            legacy = tmp / "legacy-app"
            modernized = tmp / "modernized-app"
            run_dir = modernized / ".migration" / "runs" / "run-1"
            outside = tmp / "outside"
            legacy.mkdir()
            modernized.mkdir()
            run_dir.mkdir(parents=True)
            outside.mkdir()
            _symlink_or_skip(self, run_dir / "workspaces", outside, target_is_directory=True)

            with self.assertRaisesRegex(TransformationWorkspaceError, "sandbox must stay inside run_dir"):
                prepare_sandbox_workspace(
                    legacy_app_path=legacy,
                    modernized_app_path=modernized,
                    run_dir=run_dir,
                )

    def test_prepare_sandbox_workspace_rejects_sandbox_equal_to_source_or_target(self) -> None:
        with workspace_temp_dir() as tmp:
            run_dir = tmp / "run"
            legacy = run_dir / "workspaces" / "sandbox"
            modernized = tmp / "modernized-app"
            legacy.mkdir(parents=True)
            modernized.mkdir()

            with self.assertRaisesRegex(TransformationWorkspaceError, "sandbox must not be the legacy_app_path"):
                prepare_sandbox_workspace(
                    legacy_app_path=legacy,
                    modernized_app_path=modernized,
                    run_dir=run_dir,
                )

            modernized = legacy
            legacy = tmp / "legacy-app"
            legacy.mkdir()
            with self.assertRaisesRegex(TransformationWorkspaceError, "sandbox must not be the modernized_app_path"):
                prepare_sandbox_workspace(
                    legacy_app_path=legacy,
                    modernized_app_path=modernized,
                    run_dir=run_dir,
                )

    def test_prepare_sandbox_workspace_rejects_symlink_escape(self) -> None:
        with workspace_temp_dir() as tmp:
            legacy = tmp / "legacy-app"
            modernized = tmp / "modernized-app"
            outside = tmp / "outside.txt"
            legacy.mkdir()
            modernized.mkdir()
            outside.write_text("outside", encoding="utf-8")
            _symlink_or_skip(self, legacy / "escape.txt", outside)

            with self.assertRaisesRegex(TransformationWorkspaceError, "Symlink escapes"):
                prepare_sandbox_workspace(
                    legacy_app_path=legacy,
                    modernized_app_path=modernized,
                    run_dir=modernized / ".migration" / "runs" / "run-1",
                )

    def test_prepare_sandbox_workspace_wraps_cleanup_permission_error(self) -> None:
        with workspace_temp_dir() as tmp:
            legacy = tmp / "legacy-app"
            modernized = tmp / "modernized-app"
            run_dir = modernized / ".migration" / "runs" / "run-1"
            sandbox = run_dir / "workspaces" / "sandbox"
            legacy.mkdir()
            modernized.mkdir()
            sandbox.mkdir(parents=True)
            (legacy / "pom.xml").write_text("<project />", encoding="utf-8")
            (sandbox / "locked.txt").write_text("locked", encoding="utf-8")

            with mock.patch(
                "migration_factory.agents.transformation_agent.workspace.shutil.rmtree",
                side_effect=PermissionError("[WinError 5] Access is denied"),
            ):
                with self.assertRaisesRegex(TransformationWorkspaceError, "SANDBOX_CLEAN_FAILED") as raised:
                    prepare_sandbox_workspace(
                        legacy_app_path=legacy,
                        modernized_app_path=modernized,
                        run_dir=run_dir,
                    )

            message = str(raised.exception)
            self.assertIn(str(sandbox), message)
            self.assertIn("stop Java process / close terminals/editors", message)
            self.assertIn("delete sandbox manually", message)
            self.assertIn("use a new run id", message)

    def test_sandbox_cleanup_refuses_target_outside_run_dir(self) -> None:
        with workspace_temp_dir() as tmp:
            run_dir = tmp / "run"
            outside = tmp / "outside-sandbox"
            run_dir.mkdir()
            outside.mkdir()

            with mock.patch("migration_factory.agents.transformation_agent.workspace.shutil.rmtree") as rmtree:
                with self.assertRaisesRegex(TransformationWorkspaceError, "sandbox must stay inside run_dir"):
                    workspace_module._remove_existing_sandbox(outside, run_dir)

            rmtree.assert_not_called()

    def test_transform_v1_after_approval_runs_transformer_against_sandbox(self) -> None:
        with workspace_temp_dir() as tmp:
            legacy = tmp / "legacy-app"
            modernized = tmp / "modernized-app"
            ai_hub = tmp / "ai-hub"
            run_id = "run-1"
            run_dir = _run_dir(modernized, run_id)
            legacy.mkdir()
            modernized.mkdir()
            (legacy / "pom.xml").write_text("<project />", encoding="utf-8")
            _write_ai_hub_profile(ai_hub)
            _write_approved_run_artifacts(modernized, run_id, include_rewrite_plan=True)
            ledger_file = run_dir / "workspaces" / "sandbox" / ".migration" / "ledger.json"

            def run_agent_side_effect(*args: object, **kwargs: object) -> TransformationRunResult:
                print("OPENREWRITE_FULL_LOG should be quiet by default")
                unit_id = str(kwargs.get("start_unit") or "baseline")
                _write_awaiting_build_ledger(ledger_file, unit_id)
                return TransformationRunResult(
                    ledger_file=ledger_file,
                    status=LedgerStatus.AWAITING_BUILD_AGENT,
                    completed_units=[],
                )

            stdout = io.StringIO()
            with mock.patch(
                "migration_factory.transform_v1_after_approval.run_transformation_agent",
                side_effect=run_agent_side_effect,
            ) as run_agent:
                with mock.patch(
                    "migration_factory.transform_v1_after_approval.run_build_agent",
                    side_effect=lambda **kwargs: (
                        print("MAVEN_FULL_LOG should be quiet by default")
                        or BuildRunResult(
                            succeeded=True,
                            result_kind="success",
                            message="Application started successfully",
                        )
                    ),
                ) as run_build:
                    with mock.patch(
                        "migration_factory.transform_v1_after_approval.run_test_agent",
                        return_value=_passed_test_result(run_dir),
                    ) as run_test:
                        with redirect_stdout(stdout):
                            result = transform_v1_after_approval_main(
                                [
                                    "--run-dir",
                                    str(run_dir),
                                    "--legacy-app",
                                    str(legacy),
                                    "--modernized-app",
                                    str(modernized),
                                    "--ai-hub",
                                    str(ai_hub),
                                    "--profile",
                                    "java17",
                                    "--approved-by",
                                    "human",
                                ]
                            )

            sandbox_path = run_dir / "workspaces" / "sandbox"
            plan_path = run_dir / "transformation" / "transformation_execution_plan.yaml"
            plugin_path = run_dir / "transformation" / "openrewrite-plugin.xml"
            log_file = run_dir / "logs" / "phase2_transform.log"
            plan_payload = yaml.safe_load(plan_path.read_text(encoding="utf-8"))

            self.assertEqual(result, 0)
            self.assertIn("APPROVED_FOR_TRANSFORM", stdout.getvalue())
            self.assertIn("SANDBOX_PREPARED", stdout.getvalue())
            self.assertIn("TRANSFORM_RUNNING", stdout.getvalue())
            self.assertIn("BUILD_RUNNING_IN_SANDBOX", stdout.getvalue())
            self.assertIn("BUILD_PASSED_IN_SANDBOX", stdout.getvalue())
            self.assertEqual(stdout.getvalue().count("BUILD_PASSED_IN_SANDBOX"), 2)
            self.assertIn("TRANSFORM_APPLIED_IN_SANDBOX", stdout.getvalue())
            self.assertNotIn("OPENREWRITE_FULL_LOG", stdout.getvalue())
            self.assertNotIn("MAVEN_FULL_LOG", stdout.getvalue())
            self.assertIn("OPENREWRITE_FULL_LOG", log_file.read_text(encoding="utf-8"))
            self.assertIn("MAVEN_FULL_LOG", log_file.read_text(encoding="utf-8"))
            first_build_passed = stdout.getvalue().index("BUILD_PASSED_IN_SANDBOX")
            second_transform_running = stdout.getvalue().index("TRANSFORM_RUNNING", stdout.getvalue().index("TRANSFORM_RUNNING") + 1)
            self.assertLess(first_build_passed, second_transform_running)
            self.assertEqual(stdout.getvalue().count("TRANSFORM_RUNNING"), 2)
            self.assertGreater(
                stdout.getvalue().index("TRANSFORM_APPLIED_IN_SANDBOX"),
                stdout.getvalue().rindex("BUILD_PASSED_IN_SANDBOX"),
            )
            self.assertEqual(plan_payload["workspaces"]["target"]["path"], str(sandbox_path.resolve()))
            self.assertTrue((sandbox_path / "pom.xml").is_file())
            self.assertIn("<artifactId>rewrite-maven-plugin</artifactId>", plugin_path.read_text(encoding="utf-8"))
            self.assertNotIn("<version>RELEASE</version>", plugin_path.read_text(encoding="utf-8"))
            run_agent.assert_has_calls(
                [
                    mock.call(
                        sandbox_path,
                        plugin_path,
                        plan_path,
                        start_unit=None,
                        dry_run=False,
                        stream_output=True,
                        wait_for_continue=False,
                    ),
                    mock.call(
                        sandbox_path,
                        plugin_path,
                        plan_path,
                        start_unit="java-17",
                        dry_run=False,
                        stream_output=True,
                        wait_for_continue=False,
                    ),
                ]
            )
            self.assertEqual(run_build.call_count, 2)
            run_test.assert_called_once()
            self.assertNotIn("enforcer.skip", str(run_test.call_args.kwargs.get("command")))
            self.assertNotIn("apply_goal", plan_payload["migration_units"][1]["transformations"][0])
            self.assertNotIn("apply_maven_args", plan_payload["migration_units"][1]["transformations"][0])
            run_build.assert_has_calls(
                [
                    mock.call(
                        project_path=sandbox_path,
                        ledger_file=ledger_file,
                        output_dir=run_dir / "build",
                        stream_output=True,
                        validation_unit_id="baseline",
                        source_changing_unit=False,
                        validation_command="mvn clean test",
                    ),
                    mock.call(
                        project_path=sandbox_path,
                        ledger_file=ledger_file,
                        output_dir=run_dir / "build",
                        stream_output=True,
                        validation_unit_id="java-17",
                        source_changing_unit=True,
                        validation_command="mvn clean test",
                    ),
                ]
            )
            for call_args in run_build.call_args_list:
                self.assertNotIn("enforcer.skip", str(call_args.kwargs.get("validation_command")))

    def test_transform_v1_java21_validation_sees_patched_sandbox_pom(self) -> None:
        with workspace_temp_dir() as tmp:
            legacy = tmp / "legacy-app"
            modernized = tmp / "modernized-app"
            ai_hub = tmp / "ai-hub"
            run_id = "run-1"
            run_dir = _run_dir(modernized, run_id)
            legacy.mkdir()
            modernized.mkdir()
            (legacy / "pom.xml").write_text(
                "<project><build><plugins><plugin><artifactId>maven-enforcer-plugin</artifactId>"
                "<configuration><rules><requireJavaVersion><version>[1.8,1.9)</version>"
                "</requireJavaVersion></rules></configuration></plugin></plugins></build></project>",
                encoding="utf-8",
            )
            _write_ai_hub_profile(
                ai_hub,
                extra_profile_yaml="""
  apply_goal: runNoFork
  apply_maven_args:
    - -Denforcer.skip=true
  post_openrewrite_patches:
    - type: maven_enforcer_java_version
      target_range: "[21,)"
""",
            )
            _write_approved_run_artifacts(
                modernized,
                run_id,
                include_rewrite_plan=True,
                source_unit_id="java-21",
                source_unit_goal="Upgrade project runtime to Java 21.",
            )

            seen_java21_validation = False

            def build_side_effect(**kwargs: object) -> BuildRunResult:
                nonlocal seen_java21_validation
                ledger_file = Path(str(kwargs["ledger_file"]))
                unit_id = str(kwargs["validation_unit_id"])
                if unit_id == "java-21":
                    pom_text = (run_dir / "workspaces" / "sandbox" / "pom.xml").read_text(
                        encoding="utf-8"
                    )
                    self.assertIn("<version>[21,)</version>", pom_text)
                    seen_java21_validation = True
                mark_build_passed(ledger_file, result_kind="success", message="ok")
                return BuildRunResult(succeeded=True, result_kind="success", message="ok")

            with mock.patch(
                "migration_factory.agents.transformation_agent.agent.run_command",
                return_value=CommandResult(
                    command="mvn",
                    exit_code=0,
                    stdout=[],
                    stderr=[],
                    duration_seconds=0.01,
                ),
            ):
                with mock.patch(
                    "migration_factory.transform_v1_after_approval.run_build_agent",
                    side_effect=build_side_effect,
                ):
                    with mock.patch(
                        "migration_factory.transform_v1_after_approval.run_test_agent",
                        return_value=_passed_test_result(run_dir),
                    ):
                        result = transform_v1_after_approval_main(
                            [
                                "--run-dir",
                                str(run_dir),
                                "--legacy-app",
                                str(legacy),
                                "--modernized-app",
                                str(modernized),
                                "--ai-hub",
                                str(ai_hub),
                                "--profile",
                                "java17",
                                "--approved-by",
                                "human",
                            ]
                        )

            ledger = load_ledger(run_dir / "workspaces" / "sandbox" / ".migration" / "ledger.json")
            self.assertEqual(result, 0)
            self.assertTrue(seen_java21_validation)
            self.assertEqual(
                ledger["units"]["java-21"]["transformations"][0]["patches"][0]["old_range"],
                "[1.8,1.9)",
            )

    def test_generated_openrewrite_plugin_xml_replaces_release_plugin_version(self) -> None:
        with workspace_temp_dir() as tmp:
            run_dir = tmp / "run"
            ai_hub = tmp / "ai-hub"
            analysis_dir = run_dir / "analysis"
            analysis_dir.mkdir(parents=True)
            _write_ai_hub_profile(ai_hub)
            (analysis_dir / "rewrite_plugin_plan.json").write_text(
                json.dumps(
                    {
                        "plugin": "org.openrewrite.maven:rewrite-maven-plugin:RELEASE",
                        "recipe_artifacts": ["org.openrewrite.recipe:rewrite-migrate-java:RELEASE"],
                    }
                ),
                encoding="utf-8",
            )

            plugin_path = transform_module._write_openrewrite_plugin_xml(run_dir, str(ai_hub), "java17")
            plugin_xml = plugin_path.read_text(encoding="utf-8")

            self.assertIn("<artifactId>rewrite-maven-plugin</artifactId>", plugin_xml)
            self.assertIn("<version>6.39.0</version>", plugin_xml)
            self.assertNotIn("<artifactId>rewrite-maven-plugin</artifactId>\n  <version>RELEASE</version>", plugin_xml)

    def test_profile_jdk_env_names_are_loaded_from_ai_hub_profile(self) -> None:
        with workspace_temp_dir() as tmp:
            ai_hub = tmp / "ai-hub"
            _write_ai_hub_profile(
                ai_hub,
                extra_profile_yaml="""
source_jdk_home_env: JAVA8_HOME
target_jdk_home_env: JAVA21_HOME
""",
            )

            env = transform_module._profile_jdk_env(str(ai_hub), "java17")

            self.assertEqual(
                env,
                {
                    "source_jdk_home_env": "JAVA8_HOME",
                    "target_jdk_home_env": "JAVA21_HOME",
                },
            )

    def test_transform_v1_after_approval_validates_spring_boot_source_unit_from_reactor_root(self) -> None:
        with workspace_temp_dir() as tmp:
            legacy = tmp / "legacy-app"
            modernized = tmp / "modernized-app"
            ai_hub = tmp / "ai-hub"
            run_id = "run-1"
            run_dir = _run_dir(modernized, run_id)
            legacy.mkdir()
            modernized.mkdir()
            _write_multi_module_project(legacy)
            _write_ai_hub_profile(ai_hub)
            _write_approved_run_artifacts(
                modernized,
                run_id,
                include_rewrite_plan=True,
                source_unit_id="spring-boot-3-5-14",
                source_unit_goal="Upgrade Spring Boot runtime.",
            )
            ledger_file = run_dir / "workspaces" / "sandbox" / ".migration" / "ledger.json"

            def run_agent_side_effect(*args: object, **kwargs: object) -> TransformationRunResult:
                _write_awaiting_build_ledger(ledger_file, "spring-boot-3-5-14")
                return TransformationRunResult(
                    ledger_file=ledger_file,
                    status=LedgerStatus.AWAITING_BUILD_AGENT,
                    completed_units=[],
                )

            process_result = ProcessRunResult(
                classification=BuildClassification(
                    BuildResultKind.SUCCESS,
                    "Build completed successfully",
                ),
                exit_code=0,
            )

            with mock.patch(
                "migration_factory.transform_v1_after_approval.run_transformation_agent",
                side_effect=run_agent_side_effect,
            ):
                with mock.patch(
                    "migration_factory.agents.build_agent.agent.run_until_exit",
                    return_value=process_result,
                ) as run_process:
                    with mock.patch(
                        "migration_factory.agents.build_agent.agent.run_until_build_result"
                    ) as run_startup:
                        with mock.patch(
                            "migration_factory.transform_v1_after_approval.run_test_agent",
                            return_value=_passed_test_result(run_dir),
                        ):
                            result = transform_v1_after_approval_main(
                                [
                                    "--run-dir",
                                    str(run_dir),
                                    "--legacy-app",
                                    str(legacy),
                                    "--modernized-app",
                                    str(modernized),
                                    "--ai-hub",
                                    str(ai_hub),
                                    "--profile",
                                    "java17",
                                    "--approved-by",
                                    "human",
                                ]
                            )

            sandbox_path = run_dir / "workspaces" / "sandbox"
            self.assertEqual(result, 0)
            run_startup.assert_not_called()
            run_process.assert_called_once()
            command = run_process.call_args.kwargs["command"]
            self.assertEqual(run_process.call_args.kwargs["cwd"], sandbox_path)
            self.assertEqual(run_process.call_args.kwargs["timeout_seconds"], 300)
            self.assertEqual(command[1:], ["clean", "test"])
            self.assertNotIn("spring-boot:run", command)
            self.assertNotIn("-f", command)
            self.assertNotIn("shoppoc-app/pom.xml", command)
            ledger = load_ledger(ledger_file)
            self.assertEqual(ledger["build_validation"]["unit_id"], "spring-boot-3-5-14")
            self.assertEqual(ledger["build_validation"]["command"], command)
            self.assertEqual(ledger["build_validation"]["cwd"], str(sandbox_path))

    def test_transform_v1_after_approval_reports_build_failure(self) -> None:
        with workspace_temp_dir() as tmp:
            legacy = tmp / "legacy-app"
            modernized = tmp / "modernized-app"
            ai_hub = tmp / "ai-hub"
            run_id = "run-1"
            run_dir = _run_dir(modernized, run_id)
            legacy.mkdir()
            modernized.mkdir()
            (legacy / "pom.xml").write_text("<project />", encoding="utf-8")
            _write_ai_hub_profile(ai_hub)
            _write_approved_run_artifacts(modernized, run_id, include_rewrite_plan=True)
            error_contract = run_dir / "build" / "build-error.json"
            ledger_file = run_dir / "workspaces" / "sandbox" / ".migration" / "ledger.json"

            def run_agent_side_effect(*args: object, **kwargs: object) -> TransformationRunResult:
                if kwargs.get("start_unit") is None:
                    _write_awaiting_build_ledger(ledger_file, "baseline")
                    return TransformationRunResult(
                        ledger_file=ledger_file,
                        status=LedgerStatus.AWAITING_BUILD_AGENT,
                        completed_units=[],
                    )
                return TransformationRunResult(
                    ledger_file=ledger_file,
                    status=LedgerStatus.COMPLETED,
                    completed_units=["baseline"],
                )

            stdout = io.StringIO()
            stderr = io.StringIO()
            with mock.patch(
                "migration_factory.transform_v1_after_approval.run_transformation_agent",
                side_effect=run_agent_side_effect,
            ):
                with mock.patch(
                    "migration_factory.transform_v1_after_approval.run_build_agent",
                    side_effect=lambda **kwargs: (
                        print("[ERROR] COMPILATION ERROR full Maven output")
                        or BuildRunResult(
                            succeeded=False,
                            result_kind="compilation_error",
                            message="Compilation failed",
                            error_contract_path=error_contract,
                        )
                    ),
                ):
                    with redirect_stdout(stdout), redirect_stderr(stderr):
                        result = transform_v1_after_approval_main(
                            [
                                "--run-dir",
                                str(run_dir),
                                "--legacy-app",
                                str(legacy),
                                "--modernized-app",
                                str(modernized),
                                "--ai-hub",
                                str(ai_hub),
                                "--profile",
                                "java17",
                                "--approved-by",
                                "human",
                            ]
                        )

            self.assertEqual(result, 1)
            self.assertNotIn("TRANSFORM_APPLIED_IN_SANDBOX", stdout.getvalue())
            self.assertIn("BUILD_FAILED_IN_SANDBOX", stdout.getvalue())
            self.assertEqual(stdout.getvalue().count("TRANSFORM_RUNNING"), 1)
            self.assertNotIn("[ERROR] COMPILATION ERROR", stdout.getvalue())
            self.assertIn("Build result kind: compilation_error", stderr.getvalue())
            self.assertIn("Build message: Compilation failed", stderr.getvalue())
            self.assertIn(f"Build error contract: {error_contract}", stderr.getvalue())
            self.assertIn("log_file:", stderr.getvalue())
            self.assertIn("[ERROR] COMPILATION ERROR full Maven output", stderr.getvalue())
            self.assertTrue((run_dir / "performance" / "timing_report.json").is_file())

    def test_transform_v1_after_approval_allows_candidate_when_build_passed_and_test_reports_missing(self) -> None:
        with workspace_temp_dir() as tmp:
            legacy = tmp / "legacy-app"
            modernized = tmp / "modernized-app"
            ai_hub = tmp / "ai-hub"
            run_id = "run-1"
            run_dir = _run_dir(modernized, run_id)
            legacy.mkdir()
            modernized.mkdir()
            (legacy / "pom.xml").write_text("<project />", encoding="utf-8")
            _write_ai_hub_profile(ai_hub)
            _write_approved_run_artifacts(modernized, run_id, include_rewrite_plan=True)
            ledger_file = run_dir / "workspaces" / "sandbox" / ".migration" / "ledger.json"

            def run_agent_side_effect(*args: object, **kwargs: object) -> TransformationRunResult:
                if kwargs.get("start_unit") is None:
                    _write_awaiting_build_ledger(ledger_file, "baseline")
                    return TransformationRunResult(
                        ledger_file=ledger_file,
                        status=LedgerStatus.AWAITING_BUILD_AGENT,
                        completed_units=[],
                    )
                return TransformationRunResult(
                    ledger_file=ledger_file,
                    status=LedgerStatus.COMPLETED,
                    completed_units=["baseline"],
                )

            stdout = io.StringIO()
            with mock.patch(
                "migration_factory.transform_v1_after_approval.run_transformation_agent",
                side_effect=run_agent_side_effect,
            ):
                with mock.patch(
                    "migration_factory.transform_v1_after_approval.run_build_agent",
                    return_value=BuildRunResult(
                        succeeded=True,
                        result_kind="success",
                        message="ok",
                    ),
                ):
                    with redirect_stdout(stdout):
                        result = transform_v1_after_approval_main(
                            [
                                "--run-dir",
                                str(run_dir),
                                "--legacy-app",
                                str(legacy),
                                "--modernized-app",
                                str(modernized),
                                "--ai-hub",
                                str(ai_hub),
                                "--profile",
                                "java17",
                                "--approved-by",
                                "human",
                            ]
                        )

            self.assertEqual(result, 0)
            self.assertIn("TRANSFORM_APPLIED_IN_SANDBOX", stdout.getvalue())
            self.assertIn("Sandbox migration candidate ready.", stdout.getvalue())
            report = json.loads((run_dir / "test" / "post_transform" / "test_report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["test_status"], "PASS_WITH_WARNINGS")
            self.assertEqual(report["reason"], "BUILD_PASSED_NO_SUREFIRE_REPORTS_NO_RUNNABLE_TESTS")

    def test_transform_v1_after_approval_reports_transform_failure(self) -> None:
        with workspace_temp_dir() as tmp:
            legacy = tmp / "legacy-app"
            modernized = tmp / "modernized-app"
            ai_hub = tmp / "ai-hub"
            run_id = "run-1"
            run_dir = _run_dir(modernized, run_id)
            legacy.mkdir()
            modernized.mkdir()
            (legacy / "pom.xml").write_text("<project />", encoding="utf-8")
            _write_ai_hub_profile(ai_hub)
            _write_approved_run_artifacts(modernized, run_id, include_rewrite_plan=True)

            stdout = io.StringIO()
            stderr = io.StringIO()
            def run_agent_side_effect(*args: object, **kwargs: object) -> TransformationRunResult:
                print("OpenRewrite failure output")
                raise TransformationAgentError("boom")

            with mock.patch(
                "migration_factory.transform_v1_after_approval.run_transformation_agent",
                side_effect=run_agent_side_effect,
            ):
                with mock.patch("migration_factory.transform_v1_after_approval.run_build_agent") as run_build:
                    with redirect_stdout(stdout), redirect_stderr(stderr):
                        result = transform_v1_after_approval_main(
                            [
                                "--run-dir",
                                str(run_dir),
                                "--legacy-app",
                                str(legacy),
                                "--modernized-app",
                                str(modernized),
                                "--ai-hub",
                                str(ai_hub),
                                "--profile",
                                "java17",
                                "--approved-by",
                                "human",
                            ]
                        )

            self.assertEqual(result, 1)
            self.assertIn("TRANSFORM_FAILED_IN_SANDBOX", stdout.getvalue())
            self.assertNotIn("OpenRewrite failure output", stdout.getvalue())
            self.assertIn("ERROR: boom", stderr.getvalue())
            self.assertIn("log_file:", stderr.getvalue())
            self.assertIn("OpenRewrite failure output", stderr.getvalue())
            run_build.assert_not_called()

    def test_transform_v1_after_approval_writes_custom_log_file(self) -> None:
        with workspace_temp_dir() as tmp:
            legacy = tmp / "legacy-app"
            modernized = tmp / "modernized-app"
            ai_hub = tmp / "ai-hub"
            custom_log = tmp / "custom" / "phase2.log"
            run_id = "run-1"
            run_dir = _run_dir(modernized, run_id)
            legacy.mkdir()
            modernized.mkdir()
            (legacy / "pom.xml").write_text("<project />", encoding="utf-8")
            _write_ai_hub_profile(ai_hub)
            _write_approved_run_artifacts(modernized, run_id, include_rewrite_plan=True)

            def run_agent_side_effect(*args: object, **kwargs: object) -> TransformationRunResult:
                print("CUSTOM_LOG_OPENREWRITE_OUTPUT")
                return TransformationRunResult(
                    ledger_file=run_dir / "workspaces" / "sandbox" / ".migration" / "ledger.json",
                    status=LedgerStatus.COMPLETED,
                    completed_units=["java-17"],
                )

            stdout = io.StringIO()
            with mock.patch(
                "migration_factory.transform_v1_after_approval.run_transformation_agent",
                side_effect=run_agent_side_effect,
            ):
                with mock.patch("migration_factory.transform_v1_after_approval.run_build_agent") as run_build:
                    with mock.patch(
                        "migration_factory.transform_v1_after_approval.run_test_agent",
                        return_value=_passed_test_result(run_dir),
                    ):
                        with redirect_stdout(stdout):
                            result = transform_v1_after_approval_main(
                                [
                                    "--run-dir",
                                    str(run_dir),
                                    "--legacy-app",
                                    str(legacy),
                                    "--modernized-app",
                                    str(modernized),
                                    "--ai-hub",
                                    str(ai_hub),
                                    "--profile",
                                    "java17",
                                    "--approved-by",
                                    "human",
                                    "--log-file",
                                    str(custom_log),
                                ]
                            )

            self.assertEqual(result, 0)
            self.assertNotIn("CUSTOM_LOG_OPENREWRITE_OUTPUT", stdout.getvalue())
            self.assertIn("CUSTOM_LOG_OPENREWRITE_OUTPUT", custom_log.read_text(encoding="utf-8"))
            self.assertFalse((run_dir / "logs" / "phase2_transform.log").exists())
            run_build.assert_not_called()

    def test_transform_v1_after_approval_verbose_streams_subprocess_output(self) -> None:
        with workspace_temp_dir() as tmp:
            legacy = tmp / "legacy-app"
            modernized = tmp / "modernized-app"
            ai_hub = tmp / "ai-hub"
            run_id = "run-1"
            run_dir = _run_dir(modernized, run_id)
            legacy.mkdir()
            modernized.mkdir()
            (legacy / "pom.xml").write_text("<project />", encoding="utf-8")
            _write_ai_hub_profile(ai_hub)
            _write_approved_run_artifacts(modernized, run_id, include_rewrite_plan=True)

            def run_agent_side_effect(*args: object, **kwargs: object) -> TransformationRunResult:
                print("VERBOSE_OPENREWRITE_OUTPUT")
                return TransformationRunResult(
                    ledger_file=run_dir / "workspaces" / "sandbox" / ".migration" / "ledger.json",
                    status=LedgerStatus.COMPLETED,
                    completed_units=["java-17"],
                )

            stdout = io.StringIO()
            with mock.patch(
                "migration_factory.transform_v1_after_approval.run_transformation_agent",
                side_effect=run_agent_side_effect,
            ):
                with mock.patch(
                    "migration_factory.transform_v1_after_approval.run_test_agent",
                    return_value=_passed_test_result(run_dir),
                ):
                    with redirect_stdout(stdout):
                        result = transform_v1_after_approval_main(
                            [
                                "--run-dir",
                                str(run_dir),
                                "--legacy-app",
                                str(legacy),
                                "--modernized-app",
                                str(modernized),
                                "--ai-hub",
                                str(ai_hub),
                                "--profile",
                                "java17",
                                "--approved-by",
                                "human",
                                "--verbose",
                            ]
                        )

            log_file = run_dir / "logs" / "phase2_transform.log"
            self.assertEqual(result, 0)
            self.assertIn("VERBOSE_OPENREWRITE_OUTPUT", stdout.getvalue())
            self.assertIn("Transformer status: completed", stdout.getvalue())
            self.assertIn("VERBOSE_OPENREWRITE_OUTPUT", log_file.read_text(encoding="utf-8"))

    def test_transform_v1_after_approval_reports_sandbox_cleanup_failure_without_traceback(self) -> None:
        with workspace_temp_dir() as tmp:
            legacy = tmp / "legacy-app"
            modernized = tmp / "modernized-app"
            ai_hub = tmp / "ai-hub"
            run_id = "run-1"
            run_dir = _run_dir(modernized, run_id)
            sandbox = run_dir / "workspaces" / "sandbox"
            legacy.mkdir()
            modernized.mkdir()
            sandbox.mkdir(parents=True)
            (legacy / "pom.xml").write_text("<project />", encoding="utf-8")
            (sandbox / "locked.txt").write_text("locked", encoding="utf-8")
            _write_ai_hub_profile(ai_hub)
            _write_approved_run_artifacts(modernized, run_id, include_rewrite_plan=True)

            stdout = io.StringIO()
            stderr = io.StringIO()
            with mock.patch(
                "migration_factory.agents.transformation_agent.workspace.shutil.rmtree",
                side_effect=PermissionError("[WinError 5] Access is denied"),
            ):
                with mock.patch("migration_factory.transform_v1_after_approval.run_transformation_agent") as run_agent:
                    with redirect_stdout(stdout), redirect_stderr(stderr):
                        result = transform_v1_after_approval_main(
                            [
                                "--run-dir",
                                str(run_dir),
                                "--legacy-app",
                                str(legacy),
                                "--modernized-app",
                                str(modernized),
                                "--ai-hub",
                                str(ai_hub),
                                "--profile",
                                "java17",
                                "--approved-by",
                                "human",
                            ]
                        )

            self.assertEqual(result, 1)
            self.assertIn("TRANSFORM_FAILED_IN_SANDBOX", stdout.getvalue())
            self.assertNotIn("Traceback", stdout.getvalue())
            self.assertNotIn("Traceback", stderr.getvalue())
            self.assertIn("SANDBOX_CLEAN_FAILED", stderr.getvalue())
            self.assertIn(str(sandbox), stderr.getvalue())
            self.assertIn("stop Java process / close terminals/editors", stderr.getvalue())
            self.assertIn("delete sandbox manually", stderr.getvalue())
            self.assertIn("use a new run id", stderr.getvalue())
            run_agent.assert_not_called()


def _run_dir(app: Path, run_id: str) -> Path:
    return app / ".migration" / "runs" / run_id


def _symlink_or_skip(
    test_case: unittest.TestCase,
    link_path: Path,
    target: Path,
    *,
    target_is_directory: bool = False,
) -> None:
    try:
        link_path.symlink_to(target, target_is_directory=target_is_directory)
    except OSError as exc:
        if getattr(exc, "winerror", None) == 1314:
            test_case.skipTest("Windows symlink privilege is not available")
        raise


def _write_awaiting_build_ledger(ledger_file: Path, unit_id: str) -> None:
    ledger_file.parent.mkdir(parents=True, exist_ok=True)
    ledger_file.write_text(
        json.dumps(
            {
                "status": LedgerStatus.AWAITING_BUILD_AGENT,
                "current_unit": unit_id,
                "blocked_unit": None,
                "completed_units": [],
                "build_validation": {
                    "required": True,
                    "status": BuildValidationStatus.PENDING,
                    "unit_id": unit_id,
                },
            }
        ),
        encoding="utf-8",
    )


def _passed_test_result(run_dir: Path) -> _TestAgentResult:
    test_dir = run_dir / "test" / "post_transform"
    test_dir.mkdir(parents=True, exist_ok=True)
    report = test_dir / "test_report.json"
    summary = test_dir / "test_summary.md"
    log = test_dir / "test_agent.log"
    report.write_text("{}\n", encoding="utf-8")
    summary.write_text("# summary\n", encoding="utf-8")
    log.write_text("ok\n", encoding="utf-8")
    return _TestAgentResult(
        test_status="TEST_PASSED",
        totals={"tests": 1, "passed": 1, "failures": 0, "errors": 0, "skipped": 0},
        report_path=report,
        summary_path=summary,
        log_path=log,
        report_paths=[str(report)],
        parse_duration_seconds=0.01,
    )


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
    source = app / "src" / "main" / "java" / "com" / "shoppoc" / "app"
    source.mkdir(parents=True)
    (source / "ShoppocApplication.java").write_text(
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


def _write_approved_run_artifacts(
    app: Path,
    run_id: str,
    *,
    include_rewrite_plan: bool = False,
    source_unit_id: str = "java-17",
    source_unit_goal: str = "Upgrade project runtime to Java 17.",
) -> None:
    _write_run_artifacts(
        app,
        run_id,
        include_rewrite_plan=include_rewrite_plan,
        source_unit_id=source_unit_id,
        source_unit_goal=source_unit_goal,
    )
    run_dir = _run_dir(app, run_id)
    write_approved_plan_lock(run_dir, run_id)
    write_approval_decision(
        run_dir,
        run_id,
        "approved",
        plan_lock_ref="approved_plan_lock.json",
    )


def _write_run_artifacts(
    app: Path,
    run_id: str,
    *,
    include_rewrite_plan: bool = False,
    source_unit_id: str = "java-17",
    source_unit_goal: str = "Upgrade project runtime to Java 17.",
) -> None:
    run_dir = _run_dir(app, run_id)
    planning_dir = run_dir / "planning"
    assessment_dir = run_dir / "assessment"
    analysis_dir = run_dir / "analysis"
    planning_dir.mkdir(parents=True, exist_ok=True)
    assessment_dir.mkdir(parents=True, exist_ok=True)
    analysis_dir.mkdir(parents=True, exist_ok=True)

    (planning_dir / "migration_plan.yaml").write_text(
        f"""
schema_version: "1.0.0"
run_id: "{run_id}"
status: "PASS"
risk: "LOW"
profile: "java17"
artifact_refs:
  self: "migration_plan.yaml"
""".lstrip(),
        encoding="utf-8",
    )
    (planning_dir / "migration_units.yaml").write_text(
        f"""
schema_version: "1.0.0"
run_id: "{run_id}"
status: "PASS"
artifact_refs:
  self: "migration_units.yaml"
units:
  - id: "baseline"
    goal: "Establish baseline build."
    tools:
      - "maven"
      - "junit"
    validation:
      - "mvn"
      - "clean"
      - "test"
    writes_source: false
    required: "yes"
    expected_artifacts:
      - "target/surefire-reports"
  - id: "{source_unit_id}"
    goal: "{source_unit_goal}"
    tools:
      - "maven"
    validation:
      - "mvn"
      - "clean"
      - "test"
    writes_source: true
    required: "yes"
    expected_artifacts:
      - "target/classes"
""".lstrip(),
        encoding="utf-8",
    )
    (assessment_dir / "assessment_report.json").write_text(
        json.dumps({"profile": "java17"}),
        encoding="utf-8",
    )
    if include_rewrite_plan:
        (analysis_dir / "rewrite_plugin_plan.json").write_text(
            json.dumps(
                {
                    "plugin": "org.openrewrite.maven:rewrite-maven-plugin:6.39.0",
                    "recipe_artifacts": ["org.openrewrite.recipe:rewrite-migrate-java:3.20.0"],
                    "active_recipes": ["org.openrewrite.java.migrate.UpgradeToJava17"],
                }
            ),
            encoding="utf-8",
        )


def _write_ai_hub_profile(ai_hub: Path, extra_profile_yaml: str = "") -> None:
    profiles = ai_hub / "profiles"
    catalogs = ai_hub / "catalogs"
    profiles.mkdir(parents=True)
    catalogs.mkdir(parents=True)
    (profiles / "java17.yaml").write_text(
        f"""
id: java17
openrewrite:
  catalog_path: catalogs/openrewrite.yaml
{extra_profile_yaml}
""".lstrip(),
        encoding="utf-8",
    )
    (catalogs / "openrewrite.yaml").write_text(
        """
id: openrewrite-java17
plugin:
  group_id: org.openrewrite.maven
  artifact_id: rewrite-maven-plugin
  version: 6.39.0
recipe_artifacts:
  - group_id: org.openrewrite.recipe
    artifact_id: rewrite-migrate-java
    version: 3.20.0
""".lstrip(),
        encoding="utf-8",
    )


def _write_spring_boot_transform_plan(app: Path, *, run_id: str) -> dict[str, Path]:
    run_dir = app / ".migration" / "runs" / run_id
    plugin_xml = run_dir / "rewrite-plugin.xml"
    plan_yaml = run_dir / "transformation" / "transformation_execution_plan.yaml"
    plugin_xml.parent.mkdir(parents=True, exist_ok=True)
    plan_yaml.parent.mkdir(parents=True, exist_ok=True)
    plugin_xml.write_text(PLUGIN_XML, encoding="utf-8")
    plan_yaml.write_text(
        f"""schema_version: "1.3"
migration:
  id: "{run_id}"
  name: "Spring Boot validation"
workspaces:
  target:
    path: "{app.as_posix()}"
    migration_dir: ".migration"
    ledger_file: ".migration/ledger.json"
migration_units:
  - id: "spring-boot-3-5"
    title: "Spring Boot target"
    expected_files:
      - "pom.xml"
    transformations:
      - type: "spring_boot_version"
        old_value: "3.5.14"
        new_value: "3.5.6"
        required: true
    checks:
      - id: "build"
        command: "mvn clean test -DskipITs"
        required: true
""",
        encoding="utf-8",
    )
    return {"plugin_xml": plugin_xml, "plan_yaml": plan_yaml}


if __name__ == "__main__":
    unittest.main()
