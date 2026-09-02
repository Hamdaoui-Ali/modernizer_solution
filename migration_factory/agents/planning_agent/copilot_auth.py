import os
import subprocess
from dataclasses import dataclass, field
from typing import Literal

from migration_factory.agents.planning_agent.assist_config import PlanningAssistConfig

CopilotAuthMode = Literal["github_signed_in_user", "oauth_github_app", "token", "unknown"]


@dataclass(frozen=True)
class CopilotAuthResult:
    ok: bool
    auth_mode: CopilotAuthMode
    token: str | None = None
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _first_present_env(names: tuple[str, ...]) -> str | None:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return None


def _gh_auth_ready() -> bool:
    try:
        completed = subprocess.run(
            ["gh", "auth", "status"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return False
    return completed.returncode == 0


def resolve_copilot_auth(config: PlanningAssistConfig | None = None) -> CopilotAuthResult:
    assist_config = config or PlanningAssistConfig()
    auth_mode_raw = (
        os.getenv("MF_PLANNING_ASSIST_AUTH_MODE", "").strip().lower()
        or assist_config.auth_mode.strip().lower()
    )
    auth_mode = auth_mode_raw or "github_signed_in_user"

    if auth_mode == "github_signed_in_user":
        if not _gh_auth_ready():
            return CopilotAuthResult(
                ok=False,
                auth_mode="github_signed_in_user",
                errors=["GitHub CLI signed-in user auth is not ready for planning assist."],
            )
        return CopilotAuthResult(
            ok=True,
            auth_mode="github_signed_in_user",
            warnings=["Using signed-in GitHub user auth context."],
        )

    if auth_mode in {"oauth_github_app", "token"}:
        token = _first_present_env(assist_config.token_env_vars)
        if not token:
            label = "GitHub app OAuth" if auth_mode == "oauth_github_app" else "token"
            return CopilotAuthResult(
                ok=False,
                auth_mode=auth_mode,  # type: ignore[arg-type]
                errors=[f"Missing {label} token for planning assist."],
            )
        return CopilotAuthResult(
            ok=True,
            auth_mode=auth_mode,  # type: ignore[arg-type]
            token=token,
        )

    return CopilotAuthResult(
        ok=False,
        auth_mode="unknown",
        errors=[f"Unsupported planning assist auth mode: {auth_mode}."],
    )
