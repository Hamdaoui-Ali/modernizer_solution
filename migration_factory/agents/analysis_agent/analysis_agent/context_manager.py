import os

class SecurityViolationError(Exception):
    """Exception critique levée si l'agent tente une opération illégale sur les chemins."""
    pass

class MigrationContext:
    def __init__(self, run_id, legacy_path, modernized_path, ai_hub_path=None, profile=None):
        self.run_id = run_id
        
        # 1. Normalisation absolue des chemins (empêche les ambiguïtés)
        self.legacy_app_path = os.path.abspath(legacy_path)
        self.modernized_app_path = os.path.abspath(modernized_path)
        self.ai_hub_path = os.path.abspath(ai_hub_path) if ai_hub_path else None
        self.profile = profile
        
        # 2. Définition stricte de l'ALLOWLIST (La seule zone de droite)
        self.output_dir = os.path.abspath(os.path.join(
            self.modernized_app_path, 
            ".migration", "runs", self.run_id, "analysis"
        ))
        self._output_dir_prefix = self.output_dir + os.sep
        
        # HARD GUARD : Le dossier de sortie ne peut JAMAIS être dans les sources legacy
        if self.output_dir.startswith(self.legacy_app_path + os.sep) or self.output_dir == self.legacy_app_path:
            raise SecurityViolationError(
                "CRITIQUE : Configuration illégale. Le dossier de sortie chevauche le code source protégé."
            )

        os.makedirs(self.output_dir, exist_ok=True)

    def get_output_path(self, filename):
        """Génère un chemin et le valide contre la Allowlist et la Denylist (Problème 7)."""
        
        # 1. Anti-Path Traversal (Bloque les attaques de type "../../../etc/passwd")
        if ".." in filename:
            raise SecurityViolationError(f"CRITIQUE : Tentative de Path Traversal bloquée : {filename}")
            
        final_path = os.path.abspath(os.path.join(self.output_dir, filename))

        # 2. ALLOWLIST : Le fichier DOIT être dans l'espace de sortie désigné
        if not (final_path == self.output_dir or final_path.startswith(self._output_dir_prefix)):
            raise SecurityViolationError(f"CRITIQUE : Écriture hors de l'Allowlist bloquée : {final_path}")

        # 3. DENYLIST : Protection explicite des sources/configs même si l'allowlist est déjà stricte.
        rel_path = os.path.relpath(final_path, self.output_dir).replace("\\", "/")
        protected_entries = {
            "pom.xml",
            "application.properties",
            "application.yml",
        }
        if rel_path in protected_entries or rel_path.startswith("src/"):
            raise SecurityViolationError(f"CRITIQUE : Écriture sur un motif interdit (Denylist) : {final_path}")

        return final_path
        
    def validate_read_path(self, target_path):
        """Garantit qu'on ne lit que dans les dossiers du projet (Problème 6)."""
        abs_target = os.path.abspath(target_path)
        legacy_prefix = self.legacy_app_path + os.sep
        modernized_prefix = self.modernized_app_path + os.sep
        if not (
            abs_target == self.legacy_app_path
            or abs_target == self.modernized_app_path
            or abs_target.startswith(legacy_prefix)
            or abs_target.startswith(modernized_prefix)
        ):
            raise SecurityViolationError(f"CRITIQUE : Tentative de lecture d'un fichier externe bloquée : {abs_target}")
        return abs_target
