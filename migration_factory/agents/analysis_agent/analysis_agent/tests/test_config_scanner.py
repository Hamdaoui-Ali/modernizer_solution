from config_scanner import scan_config_files


def test_scan_config_files_inventory_flags_port_profiles(tmp_path):
    resources = tmp_path / "src" / "main" / "resources"
    resources.mkdir(parents=True)

    (resources / "application.properties").write_text(
        """
spring.datasource.url=jdbc:h2:mem:testdb
management.endpoints.web.exposure.include=health,info
spring.security.user.name=admin
server.port=9090
spring.profiles.active=dev,qa
""".strip()
    )

    result = scan_config_files(str(tmp_path))

    assert result["has_datasource"] is True
    assert result["has_actuator"] is True
    assert result["has_security"] is True
    assert result["port"] == "9090"
    assert result["profiles"] == ["dev", "qa"]
    assert "application.properties" in result["config_files_found"]
