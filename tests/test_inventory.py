"""Inventory resolution and precedence tests. See README.md "Authoring inventory"."""

from __future__ import annotations

from pathlib import Path

import pytest

from yaml_frag.errors import InventoryError, UnknownFragmentError, UnknownTargetError
from yaml_frag.fragments import load_fragment
from yaml_frag.inventory import load_inventory, resolve_target
from yaml_frag.models import GroupDefinition, Inventory, TargetDefinition


def _sample_inventory() -> Inventory:
    return Inventory(
        version=1,
        default_variables={"a": "default-a", "b": "default-b"},
        default_output_fragments={"out": ("frag/default",)},
        groups={
            "g1": GroupDefinition(
                name="g1", variables={"a": "g1-a"}, output_fragments={"out": ("frag/g1",)}
            ),
            "g2": GroupDefinition(
                name="g2", variables={"b": "g2-b"}, output_fragments={"out": ("frag/g2",)}
            ),
        },
        targets={
            "t1": TargetDefinition(
                name="t1",
                groups=("g1", "g2"),
                output_fragments={"out": ("frag/t1",)},
                variables={"a": "t1-a"},
            ),
            "t2": TargetDefinition(name="t2"),
        },
    )


def test_default_variables_applied() -> None:
    """Inventory `defaults.variables` appear in a target's resolved vars."""
    inventory = _sample_inventory()
    resolved = resolve_target(inventory, "t2")
    assert resolved.variables["a"] == "default-a"
    assert resolved.variables["b"] == "default-b"


def test_group_variable_precedence() -> None:
    """Group vars override defaults, in target group order."""
    inventory = _sample_inventory()
    resolved = resolve_target(inventory, "t1")
    # g1 sets a=g1-a (overrides default), g2 sets b=g2-b (overrides default);
    # target then overrides a=t1-a.
    assert resolved.variables["b"] == "g2-b"


def test_target_variable_precedence() -> None:
    """Target vars override group and default vars."""
    inventory = _sample_inventory()
    resolved = resolve_target(inventory, "t1")
    assert resolved.variables["a"] == "t1-a"


def test_cli_variable_precedence() -> None:
    """CLI --var overrides all other variable sources."""
    inventory = _sample_inventory()
    resolved = resolve_target(inventory, "t1", cli_variables={"a": "cli-a"})
    assert resolved.variables["a"] == "cli-a"


def test_group_order_preserved() -> None:
    """Groups are applied in the target's declared order, not sorted."""
    inventory = _sample_inventory()
    resolved = resolve_target(inventory, "t1")
    assert resolved.groups == ("g1", "g2")


def test_fragment_order() -> None:
    """Final fragment order, per output, is defaults -> groups (in order) -> target."""
    inventory = _sample_inventory()
    resolved = resolve_target(inventory, "t1")
    assert resolved.output_fragments["out"] == (
        "frag/default",
        "frag/g1",
        "frag/g2",
        "frag/t1",
    )


def test_output_with_no_contributing_fragments_is_absent() -> None:
    """An output name no layer contributes fragments to is not produced."""
    inventory = _sample_inventory()
    resolved = resolve_target(inventory, "t2")
    assert resolved.output_fragments == {"out": ("frag/default",)}
    assert "other-output" not in resolved.output_fragments


def test_missing_group_reference_fails() -> None:
    """Referencing an undefined group raises InventoryError."""
    inventory = Inventory(
        version=1,
        targets={
            "t1": TargetDefinition(name="t1", groups=("nope",)),
        },
    )
    with pytest.raises(InventoryError):
        resolve_target(inventory, "t1")


def test_unknown_target_fails() -> None:
    """resolve_target raises UnknownTargetError for an undefined target."""
    inventory = _sample_inventory()
    with pytest.raises(UnknownTargetError):
        resolve_target(inventory, "does-not-exist")


def test_missing_fragment_reference_fails(fragments_dir: Path) -> None:
    """Referencing a nonexistent fragment raises UnknownFragmentError."""
    with pytest.raises(UnknownFragmentError):
        load_fragment(fragments_dir, "does/not/exist")


def test_duplicate_target_definition_fails(tmp_path: Path) -> None:
    """Duplicate target names are rejected during inventory validation."""
    inventory_file = tmp_path / "targets.yaml"
    inventory_file.write_text(
        "version: 1\n"
        "targets:\n"
        "  t1:\n"
        "    fragments: []\n"
        "  t1:\n"
        "    fragments: []\n"
    )
    with pytest.raises(InventoryError):
        load_inventory(inventory_file)


def test_resolved_variables_are_left_unresolved() -> None:
    """resolve_target layers definitions but does not resolve `from:` sources;
    that happens later in sources.resolve_variables (see test_sources.py)."""
    inventory = Inventory(
        version=1,
        default_variables={
            "identity_password_hash": {
                "from": "capture",
                "command": ["openssl", "passwd", "-6", "-stdin"],
            }
        },
        targets={"t1": TargetDefinition(name="t1")},
    )
    resolved = resolve_target(inventory, "t1")
    assert resolved.variables["identity_password_hash"] == {
        "from": "capture",
        "command": ["openssl", "passwd", "-6", "-stdin"],
    }


def test_real_inventory_loads(inventory_path: Path) -> None:
    """The repo's example inventory loads and resolves without error."""
    inventory = load_inventory(inventory_path)
    resolved = resolve_target(inventory, "gb10-01")
    assert "hosts/gb10-01" in resolved.output_fragments["user-data"]
    assert "meta/instance-id" in resolved.output_fragments["meta-data"]
    assert resolved.variables["identity_hostname"] == "gb10-01"


def test_real_inventory_missing_target(inventory_path: Path) -> None:
    inventory = load_inventory(inventory_path)
    with pytest.raises(UnknownTargetError):
        resolve_target(inventory, "no-such-target")
