"""V1 contract fixture builders for the Spring Boot 2.1.6 → 3.5.6 Java 21 pipeline.

These fixtures define the exact V1 pipeline definition, runner profile, and job
payload used by V1 integration tests. They enforce the V1 route contract:

  * Pipeline ID: springboot-216-to-356-java21-three-stage
  * Stage 1: Spring Boot 2.7.18 / Java 11 / legacy_source input
  * Stage 2: Spring Boot 3.5.6 / Java 17 / Stage 1 sandbox input
  * Stage 3: Spring Boot 3.5.6 / Java 21 / Stage 2 sandbox input
  * No raw executable paths, Maven goals, shell commands, working directories,
    or model deployment IDs available for browser selection.
  * Boot 4 and 3.5.14 are NOT present as execution targets.
"""

from __future__ import annotations


def make_v1_pipeline_definition() -> dict:
    """Return the canonical V1 pipeline definition payload.

    The returned dict can be validated against the PipelineDefinition schema and
    used to register the V1 migration route.
    """
    return {
        "schema_version": "1.0.0",
        "pipeline_id": "springboot-216-to-356-java21-three-stage",
        "pipeline_version": "2026.06",
        "display_name": "Spring Boot 2.1.6 → 3.5.6 · Java 21 · Three-Stage",
        "graph_version": "1.0",
        "graph_state_schema_version": "1.0",
        "stages": (
            {
                "stage_index": 1,
                "stage_id": "springboot-216-to-27-java11",
                "profile_id": "v1-stage1-profile",
                "command_jdk": "java11",
                "input_source": {"kind": "legacy_source"},
                "continuation_policy_id": "stage1-build-test-policy",
                "target": {"spring_boot": "2.7.18", "java": 11},
            },
            {
                "stage_index": 2,
                "stage_id": "springboot-27-to-35-java17",
                "profile_id": "v1-stage2-profile",
                "command_jdk": "java17",
                "input_source": {
                    "kind": "previous_stage",
                    "previous_stage_index": 1,
                },
                "continuation_policy_id": "default",
                "target": {"spring_boot": "3.5.6", "java": 17},
            },
            {
                "stage_index": 3,
                "stage_id": "springboot-35-java17-to-java21",
                "profile_id": "v1-stage3-profile",
                "command_jdk": "java21",
                "input_source": {
                    "kind": "previous_stage",
                    "previous_stage_index": 2,
                },
                "continuation_policy_id": "final-build-test-policy",
                "target": {"spring_boot": "3.5.6", "java": 21},
            },
        ),
    }


def make_v2_pipeline_definition() -> dict:
    """Return the canonical V2 four-stage pipeline definition payload."""
    return {
        "schema_version": "1.0.0",
        "pipeline_id": "springboot-216-to-400-java21-four-stage",
        "pipeline_version": "2026.06",
        "display_name": "V2 migration pipeline (4-stage with Boot 4)",
        "graph_version": "1.0",
        "graph_state_schema_version": "1.0",
        "stages": (
            {
                "stage_index": 1,
                "stage_id": "analysis",
                "profile_id": "analysis-profile",
                "command_jdk": "jdk-17",
                "input_source": {"kind": "legacy_source"},
                "continuation_policy_id": "default",
                "target": {"spring_boot": "3.5.14", "java": 17},
            },
            {
                "stage_index": 2,
                "stage_id": "planning",
                "profile_id": "planning-profile",
                "command_jdk": "jdk-21",
                "input_source": {"kind": "previous_stage", "previous_stage_index": 1},
                "continuation_policy_id": "default",
                "target": {"spring_boot": "3.5.14", "java": 21},
            },
            {
                "stage_index": 3,
                "stage_id": "finalize",
                "profile_id": "finalize-profile",
                "command_jdk": "jdk-21",
                "input_source": {"kind": "previous_stage", "previous_stage_index": 2},
                "continuation_policy_id": "default",
                "target": {"spring_boot": "3.5.14", "java": 21},
            },
            {
                "stage_index": 4,
                "stage_id": "boot4-migration",
                "profile_id": "springboot-3.5-java21-to-4.0-java21",
                "command_jdk": "jdk-21",
                "input_source": {"kind": "previous_stage", "previous_stage_index": 3},
                "continuation_policy_id": "default",
                "target": {"spring_boot": "4.0.0", "java": 21},
            },
        ),
    }


def make_v1_runner_profile() -> dict:
    """Return a V1 runner profile payload.

    The profile defines the execution environment for the V1 pipeline. It does
    NOT expose raw paths, Maven goals, shell commands, working directories, or
    model deployment IDs for browser selection — those are backend-owned fields.
    """
    return {
        "schema_version": "1.0.0",
        "runner_profile_id": "runner-v1",
        "runner_profile_version": "2026.06",
        "display_name": "V1 runner",
        "python_executable": "/usr/local/bin/python3",
        "ai_hub_path": "/opt/ai-hub",
        "maven": {
            "executable_path": "/usr/share/maven/bin/mvn",
            "expected_version": "3.9.9",
            "allow_wrapper": False,
        },
        "jdks": (
            {
                "jdk_id": "java11",
                "java_home": "/usr/lib/jvm/java-11-openjdk",
                "expected_major": 11,
                "role": "source",
            },
            {
                "jdk_id": "java17",
                "java_home": "/usr/lib/jvm/java-17-openjdk",
                "expected_major": 17,
                "role": "source",
            },
            {
                "jdk_id": "java21",
                "java_home": "/usr/lib/jvm/java-21-openjdk",
                "expected_major": 21,
                "role": "target",
            },
        ),
        "filesystem": {
            "roots": (
                {
                    "root_id": "source-root",
                    "kind": "source",
                    "path": "/var/workspace/source",
                },
                {
                    "root_id": "output-root",
                    "kind": "output",
                    "path": "/var/workspace/output",
                },
            )
        },
        "network": {
            "mode": "allowlisted",
            "allowed_hosts": ("repo.local",),
        },
        "ai_profile": {
            "profile_id": "v1-llm-profile",
        },
    }


def make_v1_job_payload() -> dict:
    """Return a minimal V1 job payload suitable for creating a migration job.

    The payload contains no raw paths, Maven goals, shell commands, working
    directories, or model deployment IDs — fields that the browser or LLM must
    not be able to choose in V1.
    """
    return {
        "job_id": "v1-migration-job-001",
        "pipeline_id": "springboot-216-to-356-java21-three-stage",
        "pipeline_version": "2026.06",
        "runner_profile_id": "runner-v1",
        "runner_profile_version": "2026.06",
        "target_proof_level": "full",
        "requested_by": "developer@example.com",
    }
