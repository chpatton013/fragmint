"""Inventory resolution and precedence tests. Covers PLAN.md "Inventory tests"."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.skip(reason="TODO: implement — see PLAN.md 'Inventory tests'")


def test_default_variables_applied() -> None:
    """Inventory `defaults.variables` appear in a target's resolved vars."""
    raise NotImplementedError


def test_group_variable_precedence() -> None:
    """Group vars override defaults, in target group order."""
    raise NotImplementedError


def test_target_variable_precedence() -> None:
    """Target vars override group and default vars."""
    raise NotImplementedError


def test_cli_variable_precedence() -> None:
    """CLI --var overrides all other variable sources."""
    raise NotImplementedError


def test_group_order_preserved() -> None:
    """Groups are applied in the target's declared order, not sorted."""
    raise NotImplementedError


def test_fragment_order() -> None:
    """Final fragment order is defaults -> groups (in order) -> target."""
    raise NotImplementedError


def test_missing_group_reference_fails() -> None:
    """Referencing an undefined group raises InventoryError."""
    raise NotImplementedError


def test_missing_fragment_reference_fails() -> None:
    """Referencing a nonexistent fragment raises UnknownFragmentError."""
    raise NotImplementedError


def test_duplicate_target_definition_fails() -> None:
    """Duplicate target names are rejected during inventory validation."""
    raise NotImplementedError


def test_resolved_variables_are_left_unresolved() -> None:
    """resolve_target layers definitions but does not resolve `from:` sources;
    that happens later in sources.resolve_variables (see test_sources.py)."""
    raise NotImplementedError
