"""Model profile domain types for V1 Azure model profiles registry.

Model profiles store only environment references, never raw secrets,
prompts, or deployment IDs directly. Provider kind defaults to 'fake'
for development; 'azure_openai' is used when live Azure connectivity
is explicitly configured.
"""

from __future__ import annotations

from dataclasses import dataclass

from migration_factory.control_tower.domain.events import V1EventType


@dataclass(frozen=True, slots=True)
class V1ModelProfileRecord:
    """Immutable record for a registered V1 model profile.

    All credential/sensitive references are stored as env var names,
    not the raw values. This prevents secrets from appearing in the
    database or API responses.
    """

    profile_id: str
    display_name: str
    provider_kind: str  # 'fake' or 'azure_openai'
    model_env_ref: str
    endpoint_env_ref: str
    deployment_env_ref: str
    is_active: bool
    created_at: str
    created_by: str


@dataclass(frozen=True, slots=True)
class V1ModelProfileEventRecord:
    """Immutable record for a model profile registration event."""

    event_id: str
    profile_id: str
    event_type: str
    provider_kind: str
    actor_type: str
    actor_id: str
    payload_json: str
    payload_checksum: str
    created_at: str
    correlation_id: str | None = None
    causation_id: str | None = None

    def __post_init__(self) -> None:
        _assert_valid_event_type(self.event_type)


_MODEL_PROFILE_EVENT_TYPES = frozenset(
    {
        V1EventType.RUNNER_VALIDATION.value,
    }
)


def _assert_valid_event_type(event_type: str) -> None:
    if event_type not in _MODEL_PROFILE_EVENT_TYPES:
        raise ValueError(
            f"Unsupported model profile event type {event_type!r}. "
            f"Only {sorted(_MODEL_PROFILE_EVENT_TYPES)} are valid for V1-09."
        )
