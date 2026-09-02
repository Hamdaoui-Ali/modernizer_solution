import pytest
from maven_scanner import scan_root_pom

def test_scan_root_pom_extracts_correct_versions(tmp_path):
    """Prouve que le scanner lit parfaitement les versions Java et Spring Boot."""
    
    # 1. ARRANGE : On crée un faux fichier POM temporaire pour le test
    fake_pom = tmp_path / "pom.xml"
    fake_pom.write_text("""<?xml version="1.0" encoding="UTF-8"?>
    <project xmlns="http://maven.apache.org/POM/4.0.0">
        <parent>
            <version>2.7.18</version>
        </parent>
        <properties>
            <java.version>11</java.version>
        </properties>
        <modules>
            <module>shoppoc-core</module>
            <module>shoppoc-api</module>
        </modules>
    </project>
    """, encoding="utf-8")

    # 2. ACT : On lance notre scanner sur ce faux fichier
    result = scan_root_pom(str(fake_pom))

    # 3. ASSERT : On vérifie mathématiquement le résultat
    assert result["source_stack"]["java"] == "11"
    assert result["source_stack"]["spring_boot"] == "2.7.18"
    assert result["project_structure"]["module_count"] == 2
    
    # On vérifie aussi le contrat attendu pour l'agent de transformation
    assert result["target_stack"]["java"] == "17"
    assert result["target_stack"]["spring_boot"] == "3.5.14"


def test_scan_root_pom_uses_profile_target_and_boot4_warnings(tmp_path):
    fake_pom = tmp_path / "pom.xml"
    fake_pom.write_text("""<?xml version="1.0" encoding="UTF-8"?>
    <project xmlns="http://maven.apache.org/POM/4.0.0">
        <parent>
            <version>2.7.18</version>
        </parent>
        <properties>
            <java.version>1.8</java.version>
        </properties>
    </project>
    """, encoding="utf-8")

    result = scan_root_pom(
        str(fake_pom),
        target_stack={
            "java": "21",
            "spring_boot": "4.0.0",
            "spring_framework": "7.x",
            "build": "maven",
        },
    )

    assert result["target_stack"]["java"] == "21"
    assert result["target_stack"]["spring_boot"] == "4.0.0"
    assert result["target_stack"]["spring_framework"] == "7.x"
    assert any("Spring Framework 7" in warning for warning in result["warnings"])
    assert any("Servlet 6.1" in warning for warning in result["warnings"])


def test_scan_root_pom_resolves_spring_boot_bom_property(tmp_path):
    fake_pom = tmp_path / "pom.xml"
    fake_pom.write_text("""<?xml version="1.0" encoding="UTF-8"?>
    <project xmlns="http://maven.apache.org/POM/4.0.0">
        <properties>
            <java.version>11</java.version>
            <spring-boot.version>2.1.6.RELEASE</spring-boot.version>
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
    """, encoding="utf-8")

    result = scan_root_pom(str(fake_pom))

    assert result["source_stack"]["spring_boot"] == "2.1.6.RELEASE"


def test_scan_root_pom_parse_failure_keeps_analysis_contract(tmp_path):
    fake_pom = tmp_path / "pom.xml"
    fake_pom.write_text("<project>", encoding="utf-8")

    result = scan_root_pom(str(fake_pom), target_stack={"java": "21", "spring_boot": "4.0.0"})

    assert result["source_stack"] == {
        "java": "unknown",
        "spring_boot": "unknown",
        "build_tool": "maven",
    }
    assert result["target_stack"]["java"] == "21"
    assert result["target_stack"]["spring_boot"] == "4.0.0"
    assert result["project_structure"]["module_count"] == 0
    assert any("Unable to parse root pom.xml" in warning for warning in result["warnings"])
