"""F5-T7: Policy Validation Before Gate Presentation — unit tests.

Tests cover:
  - is_unified_diff (patch_gate)
  - extract_touched_paths
  - validate_patch_paths
  - evaluate_patch_proposal
  - _is_unified_diff (repair_review_chain)
  - _check_forbidden_paths_in_diff
  - _check_forbidden_keys
  - evaluate_rule / RuleDecision
  - PatchGateResult statuses
"""

from __future__ import annotations

import pytest

from migration_factory.repair_loop.patch_gate import (
    BLOCKED_FILE_NAMES,
    BLOCKED_PARTS,
    BLOCKED_PREFIXES,
    PatchGateResult,
    evaluate_patch_proposal,
    extract_touched_paths,
    is_unified_diff,
    validate_patch_paths,
)
from migration_factory.repair_loop.rule_registry import (
    ALLOWED_RULE_IDS,
    RuleDecision,
    evaluate_rule,
)
from migration_factory.orchestrator.repair_review_chain import (
    _check_forbidden_keys,
    _check_forbidden_paths_in_diff,
    _is_unified_diff,
)


# ── Shared helpers ──────────────────────────────────────────────────

VALID_DIFF = """\
diff --git a/src/main/java/com/example/App.java b/src/main/java/com/example/App.java
--- a/src/main/java/com/example/App.java
+++ b/src/main/java/com/example/App.java
@@ -10,6 +10,7 @@
 package com.example;
+import jakarta.validation.Valid;
 public class App {
     public static void main(String[] args) {
-        System.out.println("Hello");
+        System.out.println("Hello Jakarta");
     }
 }
"""

DIFF_NO_HEADER = "just some text\n+added line\n-old line\n"

DIFF_NO_PLUS_MINUS = """\
diff --git a/foo.java b/foo.java
--- a/foo.java
+++ b/foo.java
@@ -1,1 +1,1 @@
 unchanged context
"""

BINARY_DIFF = """\
diff --git a/foo.bin b/foo.bin
GIT binary patch
some binary
"""


def _make_proposal(**overrides):
    base = {
        "deterministic_rule_id": "DEPENDENCY_ADD_H2_RUNTIME",
        "risk": "LOW",
        "unified_diff": VALID_DIFF,
        "requires_human_review": False,
        "description": "",
        "expected_validation": [],
        "limitations": [],
    }
    base.update(overrides)
    return base


def _setup_sandbox(tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    return sandbox, run_dir, legacy


# ── 1. is_unified_diff — valid diff ─────────────────────────────────

def test_is_unified_diff_returns_true_for_valid_diff():
    assert is_unified_diff(VALID_DIFF) is True


# ── 2. is_unified_diff — invalid diffs ──────────────────────────────

def test_is_unified_diff_returns_false_for_plain_text():
    assert is_unified_diff(DIFF_NO_HEADER) is False


def test_is_unified_diff_returns_false_for_empty():
    assert is_unified_diff("") is False


def test_is_unified_diff_returns_false_for_whitespace_only():
    assert is_unified_diff("   \n  \n ") is False


def test_is_unified_diff_returns_false_for_binary():
    assert is_unified_diff(BINARY_DIFF) is False


def test_is_unified_diff_returns_false_for_missing_at():
    diff = "diff --git a/x.java b/x.java\n--- a/x.java\n+++ b/x.java\nno change\n"
    assert is_unified_diff(diff) is False


# ── 3. extract_touched_paths ────────────────────────────────────────

def test_extract_touched_paths_from_valid_diff():
    paths, errors = extract_touched_paths(VALID_DIFF)
    assert not errors
    assert "src/main/java/com/example/App.java" in paths


def test_extract_touched_paths_strips_ab_prefix():
    diff = """\
diff --git a/src/main/Foo.java b/src/main/Foo.java
--- a/src/main/Foo.java
+++ b/src/main/Foo.java
@@ -1,1 +1,1 @@
"""
    paths, errors = extract_touched_paths(diff)
    assert not errors
    assert paths == ["src/main/Foo.java"]


def test_extract_touched_paths_skips_devnull():
    diff = """\
diff --git a/old.txt /dev/null
--- a/old.txt
+++ /dev/null
@@ -1,1 +0,0 @@
"""
    paths, errors = extract_touched_paths(diff)
    assert "old.txt" in paths
    assert "/dev/null" not in paths


def test_extract_touched_paths_malformed_header():
    diff = "diff --git a/only\nno other lines\n"
    paths, errors = extract_touched_paths(diff)
    assert "malformed diff --git header" in errors


def test_extract_touched_paths_empty_returns_error():
    paths, errors = extract_touched_paths("")
    assert not paths
    assert any("no touched paths" in err for err in errors)


# ── 4. validate_patch_paths rejects absolute paths ──────────────────

def test_validate_patch_paths_rejects_absolute(tmp_path):
    sandbox, run_dir, legacy = _setup_sandbox(tmp_path)
    errors = validate_patch_paths(
        ["/etc/passwd", "C:\\Windows\\System32\\evil.bat"],
        sandbox_path=sandbox,
        run_dir=run_dir,
        legacy_path=legacy,
    )
    assert len(errors) == 2
    assert any("absolute" in err.lower() for err in errors)


# ── 5. validate_patch_paths rejects .. traversal ────────────────────

def test_validate_patch_paths_rejects_dotdot(tmp_path):
    sandbox, run_dir, legacy = _setup_sandbox(tmp_path)
    errors = validate_patch_paths(
        ["../etc/conf.txt", "src/../../lib/util.txt"],
        sandbox_path=sandbox,
        run_dir=run_dir,
        legacy_path=legacy,
    )
    assert len(errors) == 2
    assert all("traversal" in err for err in errors)


# ── 6. validate_patch_paths rejects blocked dirs ────────────────────

@pytest.mark.parametrize("blocked", sorted(BLOCKED_PARTS))
def test_validate_patch_paths_rejects_blocked_dirs(tmp_path, blocked):
    sandbox, run_dir, legacy = _setup_sandbox(tmp_path)
    errors = validate_patch_paths(
        [f"{blocked}/config.json", f"some/nested/{blocked}/data.txt"],
        sandbox_path=sandbox,
        run_dir=run_dir,
        legacy_path=legacy,
    )
    assert len(errors) == 2
    assert all("blocked generated/internal" in err for err in errors)


# ── 7. validate_patch_paths rejects blocked filenames ───────────────

@pytest.mark.parametrize("blocked_name", sorted(BLOCKED_FILE_NAMES))
def test_validate_patch_paths_rejects_blocked_filenames(tmp_path, blocked_name):
    sandbox, run_dir, legacy = _setup_sandbox(tmp_path)
    errors = validate_patch_paths(
        [blocked_name, f"config/{blocked_name}"],
        sandbox_path=sandbox,
        run_dir=run_dir,
        legacy_path=legacy,
    )
    assert len(errors) == 2
    assert all("blocked deployment/env" in err for err in errors)


# ── 8. validate_patch_paths rejects deployment/ prefix ──────────────

@pytest.mark.parametrize("prefix", BLOCKED_PREFIXES)
def test_validate_patch_paths_rejects_blocked_prefixes(tmp_path, prefix):
    sandbox, run_dir, legacy = _setup_sandbox(tmp_path)
    errors = validate_patch_paths(
        [f"{prefix}config.yml", f"{prefix}kube/admin.yaml"],
        sandbox_path=sandbox,
        run_dir=run_dir,
        legacy_path=legacy,
    )
    assert len(errors) == 2
    assert all("blocked deployment/release" in err for err in errors)


# ── 9. validate_patch_paths allows safe paths ───────────────────────

def test_validate_patch_paths_allows_safe_paths(tmp_path):
    sandbox, run_dir, legacy = _setup_sandbox(tmp_path)
    errors = validate_patch_paths(
        [
            "src/main/java/com/example/App.java",
            "src/main/resources/application.properties",
            "pom.xml",
            "src/test/java/com/example/AppTest.java",
        ],
        sandbox_path=sandbox,
        run_dir=run_dir,
        legacy_path=legacy,
    )
    assert not errors


# ── 10. evaluate_patch_proposal blocks missing rule_id ─────────────

def test_evaluate_patch_proposal_blocks_missing_rule_id(tmp_path):
    sandbox, run_dir, legacy = _setup_sandbox(tmp_path)
    proposal = _make_proposal(deterministic_rule_id="")
    result = evaluate_patch_proposal(
        proposal=proposal,
        sandbox_path=sandbox,
        run_dir=run_dir,
        legacy_path=legacy,
    )
    assert result.status == "INVALID_PATCH"
    assert "missing deterministic_rule_id" in result.reason.lower()


def test_evaluate_patch_proposal_blocks_none_rule_id(tmp_path):
    sandbox, run_dir, legacy = _setup_sandbox(tmp_path)
    proposal = _make_proposal()
    del proposal["deterministic_rule_id"]
    result = evaluate_patch_proposal(
        proposal=proposal,
        sandbox_path=sandbox,
        run_dir=run_dir,
        legacy_path=legacy,
    )
    assert result.status == "INVALID_PATCH"


# ── 11. evaluate_patch_proposal blocks high risk ────────────────────

def test_evaluate_patch_proposal_blocks_high_risk(tmp_path):
    sandbox, run_dir, legacy = _setup_sandbox(tmp_path)
    proposal = _make_proposal(risk="HIGH")
    result = evaluate_patch_proposal(
        proposal=proposal,
        sandbox_path=sandbox,
        run_dir=run_dir,
        legacy_path=legacy,
    )
    assert result.status == "HUMAN_REVIEW_REQUIRED"
    assert result.human_review_required is True
    assert "not low" in result.reason.lower()


def test_evaluate_patch_proposal_blocks_medium_risk(tmp_path):
    sandbox, run_dir, legacy = _setup_sandbox(tmp_path)
    proposal = _make_proposal(risk="MEDIUM")
    result = evaluate_patch_proposal(
        proposal=proposal,
        sandbox_path=sandbox,
        run_dir=run_dir,
        legacy_path=legacy,
    )
    assert result.status == "HUMAN_REVIEW_REQUIRED"


def test_evaluate_patch_proposal_blocks_requires_human_review_flag(tmp_path):
    sandbox, run_dir, legacy = _setup_sandbox(tmp_path)
    proposal = _make_proposal(risk="LOW", requires_human_review=True)
    result = evaluate_patch_proposal(
        proposal=proposal,
        sandbox_path=sandbox,
        run_dir=run_dir,
        legacy_path=legacy,
    )
    assert result.status == "HUMAN_REVIEW_REQUIRED"
    assert "requires human review" in result.reason.lower()


# ── 12. evaluate_patch_proposal blocks non-unified diff ────────────

def test_evaluate_patch_proposal_blocks_non_unified_diff(tmp_path):
    sandbox, run_dir, legacy = _setup_sandbox(tmp_path)
    proposal = _make_proposal(unified_diff="just some text without diff headers")
    result = evaluate_patch_proposal(
        proposal=proposal,
        sandbox_path=sandbox,
        run_dir=run_dir,
        legacy_path=legacy,
    )
    assert result.status == "INVALID_PATCH"
    assert "not a unified diff" in result.reason.lower()


# ── 13. evaluate_patch_proposal allows valid proposal ──────────────

def test_evaluate_patch_proposal_allows_valid_proposal(tmp_path):
    sandbox, run_dir, legacy = _setup_sandbox(tmp_path)
    pom = sandbox / "pom.xml"
    pom.write_text(
        """<project>
  <modelVersion>4.0.0</modelVersion>
  <groupId>com.example</groupId>
  <artifactId>demo</artifactId>
  <version>1.0</version>
  <parent>
    <groupId>org.springframework.boot</groupId>
    <artifactId>spring-boot-starter-parent</artifactId>
    <version>3.2.0</version>
  </parent>
  <properties><spring-boot.version>3.2.0</spring-boot.version></properties>
</project>"""
    )

    diff = """\
diff --git a/pom.xml b/pom.xml
--- a/pom.xml
+++ b/pom.xml
@@ -10,6 +10,11 @@
   <version>1.0</version>
+  <dependency>
+    <groupId>com.h2database</groupId>
+    <artifactId>h2</artifactId>
+    <scope>runtime</scope>
+  </dependency>
 </project>
"""
    proposal = _make_proposal(unified_diff=diff, deterministic_rule_id="DEPENDENCY_ADD_H2_RUNTIME")
    result = evaluate_patch_proposal(
        proposal=proposal,
        sandbox_path=sandbox,
        run_dir=run_dir,
        legacy_path=legacy,
        h2_required=True,
    )
    assert result.status == "ALLOWED"


# ── 14. _check_forbidden_paths_in_diff catches sandbox_path ────────

def test_check_forbidden_paths_in_diff_catches_sandbox_path():
    diff = "sandbox_path: /some/path/in/patch\n+some code\n"
    failures = _check_forbidden_paths_in_diff(diff)
    assert len(failures) >= 1
    assert any("sandbox_path" in f for f in failures)


def test_check_forbidden_paths_in_diff_catches_migration():
    diff = "--- a/.migration/stage.json\n+++ b/.migration/stage.json\n@@ -1,1 +1,1 @@"
    failures = _check_forbidden_paths_in_diff(diff)
    assert len(failures) >= 1
    assert any(".migration" in f for f in failures)


def test_check_forbidden_paths_in_diff_clean_diff():
    diff = VALID_DIFF
    failures = _check_forbidden_paths_in_diff(diff)
    assert not failures


# ── 15. _check_forbidden_paths_in_diff catches .migration ──────────

def test_check_forbidden_paths_in_diff_catches_git():
    diff = "--- a/.git/config\n+++ b/.git/config\n@@ -1,1 +1,1 @@"
    failures = _check_forbidden_paths_in_diff(diff)
    assert any(".git" in f for f in failures)


def test_check_forbidden_paths_in_diff_catches_dockerfile():
    diff = "--- a/Dockerfile\n+++ b/Dockerfile\n@@ -1,1 +1,1 @@"
    failures = _check_forbidden_paths_in_diff(diff)
    assert any("Dockerfile" in f for f in failures)


def test_check_forbidden_paths_in_diff_catches_env():
    diff = "--- a/.env\n+++ b/.env\n@@ -1,1 +1,1 @@"
    failures = _check_forbidden_paths_in_diff(diff)
    assert any(".env" in f for f in failures)


def test_check_forbidden_paths_in_diff_catches_deploy_prefix():
    diff = "--- a/deploy/k8s/config.yml\n+++ b/deploy/k8s/config.yml"
    failures = _check_forbidden_paths_in_diff(diff)
    assert any("deploy/" in f for f in failures)


# ── 16. evaluate_rule with unknown rule_id ──────────────────────────

def test_evaluate_rule_unknown_rule_id(tmp_path):
    sandbox, run_dir, legacy = _setup_sandbox(tmp_path)
    decision = evaluate_rule(
        rule_id="NOT_A_REAL_RULE",
        sandbox_path=sandbox,
        touched_paths=["pom.xml"],
        unified_diff="",
    )
    assert decision.allowed is False
    assert decision.human_review_required is True
    assert "not allowlisted" in decision.reason.lower()


# ── 17. evaluate_rule with known rule_id ────────────────────────────

def test_evaluate_rule_h2_missing_pom(tmp_path):
    sandbox, run_dir, legacy = _setup_sandbox(tmp_path)
    decision = evaluate_rule(
        rule_id="DEPENDENCY_ADD_H2_RUNTIME",
        sandbox_path=sandbox,
        touched_paths=["pom.xml"],
        unified_diff="+<groupId>com.h2database</groupId>\n+<artifactId>h2</artifactId>\n+<scope>runtime</scope>",
        h2_required=False,
    )
    assert decision.allowed is False
    assert "h2 smoke" in decision.reason.lower()


def test_evaluate_rule_unknown_returns_decision():
    assert "DEPENDENCY_ADD_H2_RUNTIME" in ALLOWED_RULE_IDS


# ── 18. Gate result statuses ────────────────────────────────────────

def test_patch_gate_result_allowed():
    result = PatchGateResult("ALLOWED", "valid patch", "RULE_X", "LOW", ())
    assert result.status == "ALLOWED"
    assert not result.human_review_required


def test_patch_gate_result_blocked():
    result = PatchGateResult("BLOCKED", "security risk", "RULE_X", "LOW", ())
    assert result.status == "BLOCKED"


def test_patch_gate_result_invalid_patch():
    result = PatchGateResult("INVALID_PATCH", "bad diff", "RULE_X", "LOW", ())
    assert result.status == "INVALID_PATCH"


def test_patch_gate_result_human_review():
    result = PatchGateResult("HUMAN_REVIEW_REQUIRED", "needs review", "RULE_X", "HIGH", (), True)
    assert result.status == "HUMAN_REVIEW_REQUIRED"
    assert result.human_review_required is True


def test_patch_gate_result_defaults():
    result = PatchGateResult("ALLOWED", "ok")
    assert result.rule_id == ""
    assert result.risk == "BLOCKED"
    assert result.touched_paths == ()
    assert result.human_review_required is False


# ── _is_unified_diff (repair_review_chain variant) ───────────────────

def test_repair_review_is_unified_diff_valid():
    assert _is_unified_diff(VALID_DIFF) is True


def test_repair_review_is_unified_diff_plain_text():
    assert _is_unified_diff(DIFF_NO_HEADER) is False  # no ---/+++/@@ headers


def test_repair_review_is_unified_diff_only_headers():
    assert _is_unified_diff(DIFF_NO_PLUS_MINUS) is True  # headers + +/- in the +++/--- lines


def test_repair_review_is_unified_diff_empty():
    assert _is_unified_diff("") is False


# ── _check_forbidden_keys ───────────────────────────────────────────

def test_check_forbidden_keys_sandbox_path():
    data = {"sandbox_path": "/tmp/some", "key": "value"}
    failures = _check_forbidden_keys(data)
    assert len(failures) >= 1
    assert any("sandbox_path" in f for f in failures)


def test_check_forbidden_keys_deployment():
    data = {"deployment": "staging", "key": "value"}
    failures = _check_forbidden_keys(data)
    assert len(failures) >= 1
    assert any("deployment" in f for f in failures)


def test_check_forbidden_keys_provider():
    data = {"provider": "aws", "key": "value"}
    failures = _check_forbidden_keys(data)
    assert len(failures) >= 1
    assert any("provider" in f for f in failures)


def test_check_forbidden_keys_multiple():
    data = {"sandbox_path": "/x", "argv": ["/bin/sh"], "env": {"SECRET": "1"}, "endpoint": "http://evil"}
    failures = _check_forbidden_keys(data)
    assert len(failures) >= 4


def test_check_forbidden_keys_clean():
    data = {"key": "value", "nested": {"inner": "ok"}}
    failures = _check_forbidden_keys(data)
    assert not failures


def test_check_forbidden_keys_empty_value_ignored():
    data = {"sandbox_path": "", "key": "value"}
    failures = _check_forbidden_keys(data)
    assert not failures


# ── RuleDecision dataclass ──────────────────────────────────────────

def test_rule_decision_allowed():
    d = RuleDecision(allowed=True, reason="ok")
    assert d.allowed is True
    assert d.human_review_required is False


def test_rule_decision_not_allowed():
    d = RuleDecision(allowed=False, reason="blocked!", human_review_required=True)
    assert d.allowed is False
    assert d.human_review_required is True


# ── Additional evaluate_patch_proposal edge cases ───────────────────

def test_evaluate_patch_proposal_rejects_absolute_paths_in_diff(tmp_path):
    sandbox, run_dir, legacy = _setup_sandbox(tmp_path)
    diff = """\
diff --git a/src/main/App.java b//etc/passwd
--- a/src/main/App.java
+++ b//etc/passwd
@@ -1,1 +1,1 @@
"""
    proposal = _make_proposal(unified_diff=diff)
    result = evaluate_patch_proposal(
        proposal=proposal,
        sandbox_path=sandbox,
        run_dir=run_dir,
        legacy_path=legacy,
    )
    assert result.status == "INVALID_PATCH"
    assert "absolute" in result.reason.lower()


def test_evaluate_patch_proposal_rejects_dotdot_in_diff(tmp_path):
    sandbox, run_dir, legacy = _setup_sandbox(tmp_path)
    diff = """\
diff --git a/../secrets/keys b/../secrets/keys
--- a/../secrets/keys
+++ b/../secrets/keys
@@ -1,1 +1,1 @@
"""
    proposal = _make_proposal(unified_diff=diff)
    result = evaluate_patch_proposal(
        proposal=proposal,
        sandbox_path=sandbox,
        run_dir=run_dir,
        legacy_path=legacy,
    )
    assert result.status == "INVALID_PATCH"
    assert "traversal" in result.reason.lower()
