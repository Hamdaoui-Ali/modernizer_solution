from rewrite_impact_analyzer import analyze_rewrite_patch


LOW_PATCH = """diff --git a/src/test/java/A.java b/src/test/java/A.java
--- a/src/test/java/A.java
+++ b/src/test/java/A.java
+import jakarta.foo.Bar;
-import javax.foo.Bar;
"""


HIGH_PATCH = """diff --git a/pom.xml b/pom.xml
--- a/pom.xml
+++ b/pom.xml
+<dependency>new</dependency>
-dummy
""" + "\n".join([f"+line{i}" for i in range(260)])


def test_low_impact_patch_summary():
    out = analyze_rewrite_patch(LOW_PATCH)
    assert out["overall_impact"] == "LOW"
    assert "impact" not in out
    assert out["changed_file_count"] == 1
    assert out["changed_files"] == ["src/test/java/A.java"]
    assert out["test_files_changed"] == 1
    assert out["migration_signals"] == {
        "api_or_boot_upgrade": True,
        "javax_removed": True,
        "boot_2_to_3_gap": False,
        "java_11_to_17_gap": False,
        "javax_present": False,
        "boot_2_to_4_gap": False,
        "boot4_target": False,
        "java_8_to_21_gap": False,
        "java_21_target": False,
        "security_config_touched": False,
        "datasource_config_touched": False,
    }


def test_high_impact_patch_summary():
    out = analyze_rewrite_patch(HIGH_PATCH)
    assert out["overall_impact"] == "HIGH"
    assert "impact" not in out
    assert out["pom_files_changed"] == 1


def test_boot4_java21_fact_summary_is_high_risk():
    out = analyze_rewrite_patch(
        "",
        analysis_facts={
            "source_stack": {"java": "8", "spring_boot": "2.7.18"},
            "target_stack": {"java": "21", "spring_boot": "4.0.0"},
            "javax_count": 3,
        },
    )

    assert out["overall_impact"] == "HIGH"
    assert out["migration_signals"]["boot_2_to_4_gap"] is True
    assert out["migration_signals"]["boot4_target"] is True
    assert out["migration_signals"]["java_8_to_21_gap"] is True
    assert out["migration_signals"]["java_21_target"] is True
