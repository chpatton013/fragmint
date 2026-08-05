"""Target resolution and precedence tests. See README.md "Authoring
inventory", "Fragment order", and "Variable precedence".

Document loading — parsing, schema validation, the import closure, name
collision/aliasing/cycle detection, and directory inference — is tested in
``test_modules.py``; this file exercises :func:`inventory.resolve_target`
directly against synthetic :class:`~yaml_frag.models.Project` values, since
resolution itself needs no files on disk.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from yaml_frag import modules
from yaml_frag.errors import InventoryError, UnknownFragmentError, UnknownTargetError
from yaml_frag.fragments import load_fragment
from yaml_frag.inventory import resolve_target
from yaml_frag.models import GroupDefinition, Project, Ref, TargetDefinition


def _ref(path: str) -> Ref:
    return Ref(module=None, path=path)


def _sample_project() -> Project:
    return Project(
        version=1,
        outputs={},
        default_output=None,
        validators={},
        default_variables={"a": "default-a", "b": "default-b"},
        default_output_fragments={"out": (_ref("frag/default"),)},
        groups={
            "g1": GroupDefinition(
                name="g1", variables={"a": "g1-a"}, output_fragments={"out": (_ref("frag/g1"),)}
            ),
            "g2": GroupDefinition(
                name="g2", variables={"b": "g2-b"}, output_fragments={"out": (_ref("frag/g2"),)}
            ),
        },
        targets={
            "t1": TargetDefinition(
                name="t1",
                groups=("g1", "g2"),
                output_fragments={"out": (_ref("frag/t1"),)},
                variables={"a": "t1-a"},
            ),
            "t2": TargetDefinition(name="t2"),
        },
    )


def test_default_variables_applied() -> None:
    """Layered `defaults.variables` (module + inventory, already merged by
    modules.flatten) appear in a target's resolved vars."""
    project = _sample_project()
    resolved = resolve_target(project, "t2")
    assert resolved.variables["a"] == "default-a"
    assert resolved.variables["b"] == "default-b"


def test_group_variable_precedence() -> None:
    """Group vars override defaults, in target group order."""
    project = _sample_project()
    resolved = resolve_target(project, "t1")
    # g1 sets a=g1-a (overrides default), g2 sets b=g2-b (overrides default);
    # target then overrides a=t1-a.
    assert resolved.variables["b"] == "g2-b"


def test_target_variable_precedence() -> None:
    """Target vars override group and default vars."""
    project = _sample_project()
    resolved = resolve_target(project, "t1")
    assert resolved.variables["a"] == "t1-a"


def test_cli_variable_precedence() -> None:
    """CLI --var overrides all other variable sources."""
    project = _sample_project()
    resolved = resolve_target(project, "t1", cli_variables={"a": "cli-a"})
    assert resolved.variables["a"] == "cli-a"


def test_target_variable_is_reserved_and_set() -> None:
    """`resolved.variables["target"]` is always the target's own name, so
    fragments can reference `{{ target }}`."""
    project = _sample_project()
    resolved = resolve_target(project, "t1")
    assert resolved.variables["target"] == "t1"

    resolved_t2 = resolve_target(project, "t2")
    assert resolved_t2.variables["target"] == "t2"


def test_defining_reserved_target_variable_raises() -> None:
    """Declaring a `target` variable anywhere (defaults/group/target/--var) is
    a fail-closed InventoryError, since it would otherwise be silently
    discarded by the reserved-name injection."""
    project = Project(
        version=1,
        default_variables={"target": "not-allowed"},
        targets={"t1": TargetDefinition(name="t1")},
    )
    with pytest.raises(InventoryError):
        resolve_target(project, "t1")

    project_cli = Project(version=1, targets={"t1": TargetDefinition(name="t1")})
    with pytest.raises(InventoryError):
        resolve_target(project_cli, "t1", cli_variables={"target": "not-allowed"})


def test_defining_reserved_output_variable_raises() -> None:
    """`output` is reserved the same way as `target`, even though it isn't
    actually set until render_target renders each output (see
    inventory.RESERVED_VARIABLE_NAMES)."""
    project = Project(
        version=1,
        default_variables={"output": "not-allowed"},
        targets={"t1": TargetDefinition(name="t1")},
    )
    with pytest.raises(InventoryError):
        resolve_target(project, "t1")


def test_group_order_preserved() -> None:
    """Groups are applied in the target's declared order, not sorted."""
    project = _sample_project()
    resolved = resolve_target(project, "t1")
    assert resolved.groups == ("g1", "g2")


def test_fragment_order() -> None:
    """Final fragment order, per output, is defaults -> groups (in order) -> target."""
    project = _sample_project()
    resolved = resolve_target(project, "t1")
    assert resolved.output_fragments["out"] == (
        _ref("frag/default"),
        _ref("frag/g1"),
        _ref("frag/g2"),
        _ref("frag/t1"),
    )


def test_output_with_no_contributing_fragments_is_absent() -> None:
    """An output name no layer contributes fragments to is not produced."""
    project = _sample_project()
    resolved = resolve_target(project, "t2")
    assert resolved.output_fragments == {"out": (_ref("frag/default"),)}
    assert "other-output" not in resolved.output_fragments


def test_missing_group_reference_fails() -> None:
    """Referencing an undefined group raises InventoryError."""
    project = Project(
        version=1,
        targets={
            "t1": TargetDefinition(name="t1", groups=("nope",)),
        },
    )
    with pytest.raises(InventoryError):
        resolve_target(project, "t1")


def test_unknown_target_fails() -> None:
    """resolve_target raises UnknownTargetError for an undefined target."""
    project = _sample_project()
    with pytest.raises(UnknownTargetError):
        resolve_target(project, "does-not-exist")


def test_missing_fragment_reference_fails(example_root: Path) -> None:
    """Referencing a nonexistent fragment raises UnknownFragmentError."""
    with pytest.raises(UnknownFragmentError):
        load_fragment(example_root / "fragments" / "does" / "not" / "exist.yaml", "does/not/exist")


def test_resolved_variables_are_left_unresolved() -> None:
    """resolve_target layers definitions but does not resolve `from:` sources;
    that happens later in sources.resolve_variables (see test_sources.py)."""
    project = Project(
        version=1,
        default_variables={
            "identity_password_hash": {
                "from": "capture",
                "command": ["openssl", "passwd", "-6", "-stdin"],
            }
        },
        targets={"t1": TargetDefinition(name="t1")},
    )
    resolved = resolve_target(project, "t1")
    assert resolved.variables["identity_password_hash"] == {
        "from": "capture",
        "command": ["openssl", "passwd", "-6", "-stdin"],
    }


def test_real_inventory_loads(inventory_path: Path) -> None:
    """The repo's example inventory loads and resolves without error."""
    closure = modules.load_closure(inventory_path)
    project = modules.flatten(closure)
    resolved = resolve_target(project, "gb10-01")
    assert any(ref.path == "hosts/gb10-01" for ref in resolved.output_fragments["user-data"])
    assert any(ref.path == "meta/instance-id" for ref in resolved.output_fragments["meta-data"])
    assert resolved.variables["identity_hostname"] == "gb10-01"


def test_real_inventory_missing_target(inventory_path: Path) -> None:
    closure = modules.load_closure(inventory_path)
    project = modules.flatten(closure)
    with pytest.raises(UnknownTargetError):
        resolve_target(project, "no-such-target")
