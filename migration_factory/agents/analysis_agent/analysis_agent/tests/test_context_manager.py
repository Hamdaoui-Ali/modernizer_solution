import pytest
from context_manager import MigrationContext, SecurityViolationError


def test_context_manager_blocks_legacy_overlap(tmp_path):
    """Prouve que l'agent refuse de configurer sa zone de sortie DANS les sources du client."""
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    with pytest.raises(SecurityViolationError, match="chevauche le code source protégé"):
        MigrationContext(run_id="run_1", legacy_path=str(legacy), modernized_path=str(legacy))


def test_context_manager_blocks_path_traversal(tmp_path):
    """Prouve que l'agent bloque les attaques de type '../' pour remonter l'arborescence."""
    legacy = tmp_path / "legacy"
    modernized = tmp_path / "modernized"
    legacy.mkdir()
    modernized.mkdir()
    ctx = MigrationContext("run_1", str(legacy), str(modernized))

    with pytest.raises(SecurityViolationError, match="Path Traversal"):
        ctx.get_output_path("../../../etc/passwd")


def test_context_manager_enforces_denylist(tmp_path):
    """Prouve que l'agent refuse formellement d'écraser un fichier pom.xml ou des sources."""
    legacy = tmp_path / "legacy"
    modernized = tmp_path / "modernized"
    legacy.mkdir()
    modernized.mkdir()
    ctx = MigrationContext("run_1", str(legacy), str(modernized))

    with pytest.raises(SecurityViolationError, match="motif interdit"):
        ctx.get_output_path("pom.xml")
