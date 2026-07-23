"""Inventory resolution and precedence tests. Covers PLAN.md "Inventory tests"."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.skip(reason="TODO: implement — see PLAN.md 'Inventory tests'")


def test_default_variables_applied() -> None:
    """Inventory `defaults.variables` appear in a machine's resolved vars."""
    raise NotImplementedError


def test_group_variable_precedence() -> None:
    """Group vars override defaults, in machine group order."""
    raise NotImplementedError


def test_machine_variable_precedence() -> None:
    """Machine vars override group and default vars."""
    raise NotImplementedError


def test_cli_variable_precedence() -> None:
    """CLI --var overrides all other variable sources."""
    raise NotImplementedError


def test_group_order_preserved() -> None:
    """Groups are applied in the machine's declared order, not sorted."""
    raise NotImplementedError


def test_fragment_order() -> None:
    """Final fragment order is defaults -> groups (in order) -> machine."""
    raise NotImplementedError


def test_missing_group_reference_fails() -> None:
    """Referencing an undefined group raises InventoryError."""
    raise NotImplementedError


def test_missing_fragment_reference_fails() -> None:
    """Referencing a nonexistent fragment raises UnknownFragmentError."""
    raise NotImplementedError


def test_duplicate_machine_definition_fails() -> None:
    """Duplicate machine names are rejected during inventory validation."""
    raise NotImplementedError


def test_secret_overlay_precedence() -> None:
    """Secrets override machine vars but are overridden by CLI --var."""
    raise NotImplementedError
