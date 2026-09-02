"""F0 closure: prove deleted TUI and CLI packages cannot be imported."""

from __future__ import annotations

import sys

import pytest


def test_tui_package_import_raises_module_not_found() -> None:
    with pytest.raises(ModuleNotFoundError):
        __import__("migration_factory.tui")


def test_cli_module_import_raises_module_not_found() -> None:
    with pytest.raises(ModuleNotFoundError):
        __import__("migration_factory.cli")


def test_tui_app_submodule_import_raises_module_not_found() -> None:
    with pytest.raises(ModuleNotFoundError):
        __import__("migration_factory.tui.app")


def test_tui_runner_adapter_submodule_import_raises_module_not_found() -> None:
    with pytest.raises(ModuleNotFoundError):
        __import__("migration_factory.tui.runner_adapter")


def test_cli_is_not_in_sys_modules_and_cannot_be_imported() -> None:
    assert "migration_factory.cli" not in sys.modules
    with pytest.raises(ModuleNotFoundError):
        import migration_factory.cli  # noqa: F401
