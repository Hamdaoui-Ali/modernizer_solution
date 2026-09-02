"""Tests for F14 POM dependency policy layer.

Validates generic dependency classification, control mode detection,
risk evaluation, and user wording policy.
"""

from __future__ import annotations

import pytest

from migration_factory.control_tower.application.pom_dependency_policy import (
    PomDependencyPolicy,
    DependencyControlMode,
    RiskLevel,
    DependencyPolicyDecision,
    _is_vague_request,
    _is_latest_request,
    _is_explicit_high_risk_request,
)


# ── POM data fixtures ──────────────────────────────────────────────

SAMPLE_POM_DEPS = {
    "properties": {
        "java.version": "17",
        "jjwt.version": "0.12.6",
        "spring-boot.version": "3.5.14",
    },
    "dependencies": [
        {"groupId": "com.google.code.gson", "artifactId": "gson", "version": "2.8.9", "scope": "compile"},
        {"groupId": "io.jsonwebtoken", "artifactId": "jjwt-api", "version": "${jjwt.version}", "scope": "compile"},
        {"groupId": "org.springframework.boot", "artifactId": "spring-boot-starter-web", "version": "", "scope": "compile"},
    ],
    "dependency_management": [
        {"groupId": "org.springframework.boot", "artifactId": "spring-boot-dependencies", "version": "3.5.14", "scope": "import", "type": "pom"},
    ],
    "plugins": [
        {"groupId": "org.apache.maven.plugins", "artifactId": "maven-compiler-plugin", "version": "3.11.0"},
        {"groupId": "org.springframework.boot", "artifactId": "spring-boot-maven-plugin", "version": ""},
    ],
    "parent": {
        "groupId": "org.springframework.boot",
        "artifactId": "spring-boot-starter-parent",
        "version": "3.5.14",
    },
}


# ── Control mode detection tests ───────────────────────────────────

class TestControlModeDetection:

    def test_direct_dependency_version(self):
        """Direct dependency with explicit version should be DIRECT."""
        policy = PomDependencyPolicy(pom_deps_data=SAMPLE_POM_DEPS)
        mode = policy._detect_control_mode(
            target_kind="dependency",
            group_id="com.google.code.gson",
            artifact_id="gson",
            property_name=None,
        )
        assert mode == DependencyControlMode.DIRECT_DEPENDENCY_VERSION

    def test_property_managed_version(self):
        """Dependency with ${property} version should be PROPERTY_MANAGED."""
        policy = PomDependencyPolicy(pom_deps_data=SAMPLE_POM_DEPS)
        mode = policy._detect_control_mode(
            target_kind="dependency",
            group_id="io.jsonwebtoken",
            artifact_id="jjwt-api",
            property_name=None,
        )
        assert mode == DependencyControlMode.PROPERTY_MANAGED_VERSION

    def test_bom_managed_version(self):
        """Dependency without version should be BOM-managed."""
        policy = PomDependencyPolicy(pom_deps_data=SAMPLE_POM_DEPS)
        mode = policy._detect_control_mode(
            target_kind="dependency",
            group_id="org.springframework.boot",
            artifact_id="spring-boot-starter-web",
            property_name=None,
        )
        assert mode == DependencyControlMode.SPRING_BOOT_BOM_MANAGED

    def test_property_direct(self):
        """Property target should be PROPERTY_MANAGED."""
        policy = PomDependencyPolicy(pom_deps_data=SAMPLE_POM_DEPS)
        mode = policy._detect_control_mode(
            target_kind="property",
            group_id=None,
            artifact_id=None,
            property_name="java.version",
        )
        assert mode == DependencyControlMode.PROPERTY_MANAGED_VERSION

    def test_plugin_version(self):
        """Plugin target should be PLUGIN_VERSION."""
        policy = PomDependencyPolicy(pom_deps_data=SAMPLE_POM_DEPS)
        mode = policy._detect_control_mode(
            target_kind="plugin",
            group_id="org.apache.maven.plugins",
            artifact_id="maven-compiler-plugin",
            property_name=None,
        )
        assert mode == DependencyControlMode.PLUGIN_VERSION

    def test_dependency_management_entry(self):
        """Dependency management target should be DEPENDENCY_MANAGEMENT_ENTRY."""
        policy = PomDependencyPolicy(pom_deps_data=SAMPLE_POM_DEPS)
        mode = policy._detect_control_mode(
            target_kind="dependency_management",
            group_id="org.springframework.boot",
            artifact_id="spring-boot-dependencies",
            property_name=None,
        )
        assert mode == DependencyControlMode.DEPENDENCY_MANAGEMENT_ENTRY

    def test_not_present(self):
        """Dependency not in POM should be NOT_PRESENT."""
        policy = PomDependencyPolicy(pom_deps_data=SAMPLE_POM_DEPS)
        mode = policy._detect_control_mode(
            target_kind="dependency",
            group_id="com.example",
            artifact_id="nonexistent",
            property_name=None,
        )
        assert mode == DependencyControlMode.NOT_PRESENT


# ── Risk classification tests ──────────────────────────────────────

class TestRiskClassification:

    def test_low_risk_direct_dependency(self):
        """Exact app-specific direct dependency version update = LOW."""
        policy = PomDependencyPolicy(pom_deps_data=SAMPLE_POM_DEPS)
        decision = policy.evaluate_change(
            target_kind="dependency",
            group_id="com.google.code.gson",
            artifact_id="gson",
            property_name=None,
            requested_version="2.11.0",
            user_request="change gson to 2.11.0",
            stage=3,
        )
        assert decision.risk == RiskLevel.LOW.value
        assert decision.can_apply is True
        assert decision.control_mode == DependencyControlMode.DIRECT_DEPENDENCY_VERSION

    def test_medium_risk_plugin(self):
        """Plugin update = MEDIUM."""
        policy = PomDependencyPolicy(pom_deps_data=SAMPLE_POM_DEPS)
        decision = policy.evaluate_change(
            target_kind="plugin",
            group_id="org.apache.maven.plugins",
            artifact_id="maven-compiler-plugin",
            property_name=None,
            requested_version="3.12.0",
            user_request="update maven-compiler-plugin to 3.12.0",
            stage=3,
        )
        assert decision.risk == RiskLevel.MEDIUM.value

    def test_high_risk_parent_bom(self):
        """Parent/BOM change = HIGH."""
        policy = PomDependencyPolicy(pom_deps_data=SAMPLE_POM_DEPS)
        decision = policy.evaluate_change(
            target_kind="parent",
            group_id="org.springframework.boot",
            artifact_id="spring-boot-starter-parent",
            property_name=None,
            requested_version="4.0.0",
            user_request="upgrade spring boot parent to 4.0.0",
            stage=3,
        )
        assert decision.risk == RiskLevel.HIGH.value

    def test_blocked_vague_request(self):
        """Vague request blocks apply."""
        policy = PomDependencyPolicy(pom_deps_data=SAMPLE_POM_DEPS)
        decision = policy.evaluate_change(
            target_kind="dependency",
            group_id="com.google.code.gson",
            artifact_id="gson",
            property_name=None,
            requested_version="latest",
            user_request="fix all dependencies and make everything better",
            stage=3,
        )
        assert decision.risk == RiskLevel.BLOCKED.value
        assert decision.can_apply is False

    def test_latest_version_blocked(self):
        """'latest' without evidence = BLOCKED."""
        policy = PomDependencyPolicy(pom_deps_data=SAMPLE_POM_DEPS)
        decision = policy.evaluate_change(
            target_kind="dependency",
            group_id="com.google.code.gson",
            artifact_id="gson",
            property_name=None,
            requested_version="latest",
            user_request="update gson to latest",
            stage=3,
        )
        assert decision.risk == RiskLevel.BLOCKED.value


# ── Stage gating tests ─────────────────────────────────────────────

class TestStageGating:

    def test_stage_1_blocks_apply(self):
        """Stage 1 should not allow apply."""
        policy = PomDependencyPolicy(pom_deps_data=SAMPLE_POM_DEPS)
        decision = policy.evaluate_change(
            target_kind="dependency",
            group_id="com.google.code.gson",
            artifact_id="gson",
            property_name=None,
            requested_version="2.11.0",
            user_request="change gson to 2.11.0",
            stage=1,
        )
        assert decision.can_apply is False
        assert decision.risk == RiskLevel.BLOCKED.value

    def test_stage_2_blocks_apply(self):
        """Stage 2 should not allow apply."""
        policy = PomDependencyPolicy(pom_deps_data=SAMPLE_POM_DEPS)
        decision = policy.evaluate_change(
            target_kind="dependency",
            group_id="com.google.code.gson",
            artifact_id="gson",
            property_name=None,
            requested_version="2.11.0",
            user_request="change gson to 2.11.0",
            stage=2,
        )
        assert decision.can_apply is False
        assert decision.risk == RiskLevel.BLOCKED.value

    def test_stage_3_allows_apply(self):
        """Stage 3 should allow low-risk apply."""
        policy = PomDependencyPolicy(pom_deps_data=SAMPLE_POM_DEPS)
        decision = policy.evaluate_change(
            target_kind="dependency",
            group_id="com.google.code.gson",
            artifact_id="gson",
            property_name=None,
            requested_version="2.11.0",
            user_request="change gson to 2.11.0",
            stage=3,
        )
        assert decision.can_apply is True


# ── User wording policy tests ──────────────────────────────────────────

class TestUserWordingPolicy:

    def test_vague_request_detected(self):
        assert _is_vague_request("fix all dependencies") is True
        assert _is_vague_request("fix everything") is True
        assert _is_vague_request("upgrade all dependencies") is True
        assert _is_vague_request("make things better") is True

    def test_exact_request_not_vague(self):
        assert _is_vague_request("change gson to 2.11.0") is False
        assert _is_vague_request("update gson to 2.11.0") is False

    def test_latest_request_detected(self):
        assert _is_latest_request("latest") is True
        assert _is_latest_request("LATEST") is True
        assert _is_latest_request("latest.release") is True

    def test_version_not_latest(self):
        assert _is_latest_request("2.11.0") is False
        assert _is_latest_request("3.5.14") is False

    def test_high_risk_confirmation(self):
        assert _is_explicit_high_risk_request("i understand the risk") is True
        assert _is_explicit_high_risk_request("apply high-risk change") is True
        assert _is_explicit_high_risk_request("accept risk and apply") is True

    def test_no_high_risk_confirmation(self):
        assert _is_explicit_high_risk_request("change gson to 2.11.0") is False
        assert _is_explicit_high_risk_request("update version") is False


# ── Dependency bucket classification tests ─────────────────────────

class TestDependencyBuckets:

    def test_spring_boot_bucket(self):
        policy = PomDependencyPolicy(pom_deps_data=SAMPLE_POM_DEPS)
        bucket = policy.get_dependency_bucket(
            group_id="org.springframework.boot",
            artifact_id="spring-boot-starter-web",
        )
        assert bucket == "boot_managed"

    def test_jakarta_platform_bucket(self):
        policy = PomDependencyPolicy(pom_deps_data=SAMPLE_POM_DEPS)
        bucket = policy.get_dependency_bucket(
            group_id="jakarta.servlet",
            artifact_id="jakarta.servlet-api",
        )
        assert bucket == "jakarta_platform"

    def test_build_plugin_bucket(self):
        policy = PomDependencyPolicy(pom_deps_data=SAMPLE_POM_DEPS)
        bucket = policy.get_dependency_bucket(
            group_id="org.apache.maven.plugins",
            artifact_id="maven-compiler-plugin",
        )
        assert bucket == "build_plugins"

    def test_app_specific_bucket(self):
        policy = PomDependencyPolicy(pom_deps_data=SAMPLE_POM_DEPS)
        bucket = policy.get_dependency_bucket(
            group_id="com.google.code.gson",
            artifact_id="gson",
        )
        assert bucket == "app_specific_third_party"


# ── No hardcoded versions test ─────────────────────────────────────

class TestNoHardcodedVersions:

    def test_policy_accepts_any_java_version(self):
        """Policy should not reject specific Java versions — baseline comes from evidence."""
        deps_with_java21 = dict(SAMPLE_POM_DEPS)
        deps_with_java21["properties"] = {**deps_with_java21["properties"], "java.version": "21"}

        policy = PomDependencyPolicy(pom_deps_data=deps_with_java21)
        decision = policy.evaluate_change(
            target_kind="property",
            group_id=None,
            artifact_id=None,
            property_name="java.version",
            requested_version="21",
            user_request="set java.version to 21",
            stage=3,
        )
        # Should be low risk — just changing a property
        assert decision.risk in (RiskLevel.LOW.value, RiskLevel.MEDIUM.value)

    def test_policy_accepts_any_spring_boot_version(self):
        """Policy should not reject specific Spring Boot versions."""
        deps_with_boot4 = dict(SAMPLE_POM_DEPS)
        deps_with_boot4["parent"] = {
            "groupId": "org.springframework.boot",
            "artifactId": "spring-boot-starter-parent",
            "version": "4.0.0",
        }

        policy = PomDependencyPolicy(pom_deps_data=deps_with_boot4)
        # Just check that no hardcoded version rejection occurs
        decision = policy.evaluate_change(
            target_kind="dependency",
            group_id="com.google.code.gson",
            artifact_id="gson",
            property_name=None,
            requested_version="2.11.0",
            user_request="change gson to 2.11.0",
            stage=3,
        )
        assert decision.risk == RiskLevel.LOW.value  # Not blocked by Boot version
