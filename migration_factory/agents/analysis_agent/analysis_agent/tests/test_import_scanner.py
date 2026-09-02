from import_scanner import scan_java_imports


def test_scan_java_imports_counts_javax_jakarta_spring(tmp_path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()

    java_file = src_dir / "Sample.java"
    java_file.write_text(
        """
package demo;
import javax.servlet.Filter;
import javax.validation.Valid;
import jakarta.persistence.Entity;
import org.springframework.context.ApplicationContext;
import org.springframework.beans.factory.annotation.Autowired;
public class Sample {}
""".strip()
    )

    result = scan_java_imports(str(tmp_path))

    assert result["javax_imports"] == 2
    assert result["jakarta_imports"] == 1
    assert result["spring_imports"] == 2
    assert str(java_file) in result["files_with_javax"]
