"""Copilot assist provider contracts."""

from migration_factory.copilot_assist.providers.cli_provider import CopilotCliProvider
from migration_factory.copilot_assist.providers.deterministic_provider import (
    DeterministicCopilotProvider,
    ProviderResult,
)

__all__ = ["CopilotCliProvider", "DeterministicCopilotProvider", "ProviderResult"]
