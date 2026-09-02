"""FastAPI adapter for Control Tower."""

from migration_factory.control_tower.adapters.fastapi.app import create_app
from migration_factory.control_tower.adapters.fastapi.security import (
    DEFAULT_FRONTEND_CLIENT_ID,
    ActorIdentity,
    LocalApiSecuritySettings,
    OperatingSystemActorProvider,
)
from migration_factory.control_tower.infrastructure.singleton import (
    FakeControllerOwnership,
)

__all__ = [
    "ActorIdentity",
    "DEFAULT_FRONTEND_CLIENT_ID",
    "FakeControllerOwnership",
    "LocalApiSecuritySettings",
    "OperatingSystemActorProvider",
    "create_app",
]
