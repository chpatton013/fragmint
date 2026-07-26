"""Inventory loading, validation, and per-target resolution.

See README.md "Authoring inventory", "Fragment order", "Variable precedence",
"Secrets", and "Validation".

Responsibilities:
1. Parse the inventory YAML into an :class:`~models.Inventory`. Each of
   ``defaults``/a group/a target declares its own per-output fragment
   contribution under an ``outputs:`` map (output name -> ``{fragments: [...]}``);
   output names are project-defined and not validated against the project
   config here (that cross-file check happens in :mod:`render`, which has both).
2. Validate it against ``schemas/inventory.schema.json`` and the structural
   rules in README.md (unique target/group names, referenced groups exist,
   fragments are lists of strings, variables are mappings, order preserved, no
   group cycles if nested groups are added).
3. Resolve a single target into a :class:`~models.ResolvedTarget` with the
   final ordered per-output fragment lists and fully layered variable map.

This module must contain NO domain-specific knowledge (README.md "Design
principles": separation of data and rendering logic).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import jsonschema

from .errors import InventoryError, UnknownTargetError
from .models import (
    GroupDefinition,
    Inventory,
    OutputFragments,
    ResolvedTarget,
    SecretStore,
    TargetDefinition,
    Variables,
)
from .yamlio import load_file

#: Variable names the renderer sets automatically; a project must not define
#: them itself (README.md "Variable precedence"). ``target`` is injected here,
#: in :func:`resolve_target`, since it's the same for every output. ``output``
#: is injected later, once per output, in :func:`yaml_frag.render.render_target`
#: (it differs per output within the same target) — but conflicts with it are
#: still rejected here, at the single point where all variable layers merge.
RESERVED_VARIABLE_NAMES = frozenset({"target", "output"})


def _parse_output_fragments(raw: dict[str, Any] | None) -> OutputFragments:
    """Parse an ``outputs:`` block (defaults/group/target) into name ->
    ordered fragment tuple."""
    result: OutputFragments = {}
    for output_name, output_raw in (raw or {}).items():
        output_raw = output_raw or {}
        result[output_name] = tuple(output_raw.get("fragments", []) or [])
    return result


def _schema_path(name: str) -> Path:
    """Resolve a project-shipped tool-format schema under ``<repo>/schemas/``."""
    return Path(__file__).resolve().parents[2] / "schemas" / name


def load_inventory(path: Path) -> Inventory:
    """Load and validate the inventory file.

    Raise :class:`~yaml_frag.errors.InventoryError` (with file/field context)
    on any schema or structural violation from README.md "Validation".
    """
    if not path.is_file():
        raise InventoryError(f"inventory file not found: {path}")

    try:
        raw = load_file(path)
    except Exception as exc:  # noqa: BLE001 - re-raise with inventory context
        raise InventoryError(f"cannot parse inventory {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise InventoryError(f"inventory {path} must be a mapping")

    schema: dict[str, Any] = cast(dict[str, Any], load_file(_schema_path("inventory.schema.json")))
    try:
        jsonschema.validate(instance=raw, schema=schema)
    except jsonschema.ValidationError as exc:
        raise InventoryError(
            f"inventory {path} failed schema validation: {exc.message} "
            f"(at {'/'.join(str(part) for part in exc.path)})"
        ) from exc

    # Schema validation above guarantees the shape README.md documents; treat the
    # parsed document as loosely-typed data from here on rather than fighting
    # the recursive YamlValue union.
    doc = cast(dict[str, Any], raw)

    version = doc.get("version")
    if version != 1:
        raise InventoryError(f"inventory {path}: unsupported version {version!r}, expected 1")

    defaults_raw = doc.get("defaults", {}) or {}
    default_variables: Variables = dict(defaults_raw.get("variables", {}) or {})
    default_output_fragments = _parse_output_fragments(defaults_raw.get("outputs"))

    groups: dict[str, GroupDefinition] = {}
    for name, group_raw in (doc.get("groups", {}) or {}).items():
        group_raw = group_raw or {}
        groups[name] = GroupDefinition(
            name=name,
            variables=dict(group_raw.get("variables", {}) or {}),
            output_fragments=_parse_output_fragments(group_raw.get("outputs")),
        )

    targets: dict[str, TargetDefinition] = {}
    targets_raw = doc.get("targets", {}) or {}
    for name, target_raw in targets_raw.items():
        target_raw = target_raw or {}
        target_groups = tuple(target_raw.get("groups", []) or [])
        for group_name in target_groups:
            if group_name not in groups:
                raise InventoryError(
                    f"inventory {path}: target {name!r} references undefined group "
                    f"{group_name!r}"
                )
        targets[name] = TargetDefinition(
            name=name,
            groups=target_groups,
            output_fragments=_parse_output_fragments(target_raw.get("outputs")),
            variables=dict(target_raw.get("variables", {}) or {}),
        )

    return Inventory(
        version=version,
        default_variables=default_variables,
        default_output_fragments=default_output_fragments,
        groups=groups,
        targets=targets,
    )


def resolve_target(
    inventory: Inventory,
    target_name: str,
    *,
    cli_variables: Variables | None = None,
) -> ResolvedTarget:
    """Resolve one target's final per-output fragments and (still-unresolved)
    variables.

    Fragment order, per output (README.md "Fragment order") — precedence is
    positional:
        1. inventory ``defaults.outputs.<name>.fragments``
        2. each group's ``outputs.<name>.fragments``, in the target's group order
        3. target ``outputs.<name>.fragments``
    Group and fragment order MUST be preserved (never alphabetized). An output
    name that no layer contributes fragments to is omitted from the result
    entirely — that output is simply not produced for this target.

    Variable precedence (README.md "Variable precedence"), later wins, and is
    NOT per-output (variables are shared across all of a target's outputs):
        1. inventory ``defaults.variables``
        2. group variables, in target group order
        3. target variables
        4. ``cli_variables`` (always literal strings)

    The returned ``variables`` are LAYERED BUT UNRESOLVED: a value may be an
    untagged literal or a ``from:`` source mapping. Secrets are no longer a
    precedence layer; they are a named store referenced via ``from: secret`` and
    resolved later by :func:`yaml_frag.sources.resolve_variables`.

    ``target`` and ``output`` are reserved variable names (see
    :data:`RESERVED_VARIABLE_NAMES`). After layering, ``target`` is always set
    to ``target_name`` here, so every fragment can reference the current
    target via ``{{ target }}``. ``output`` is reserved the same way but is
    per-output rather than per-target, so it isn't set until
    :func:`yaml_frag.render.render_target` renders each output — this function
    only rejects a layer that tries to define either name, since that's the
    single point where all variable layers merge and the conflict would
    otherwise be silently discarded (README.md "Variable precedence").

    Raise :class:`~yaml_frag.errors.UnknownTargetError` for an unknown target
    and :class:`~yaml_frag.errors.InventoryError` for a referenced-but-undefined
    group or an attempt to define a reserved variable name.
    """
    target = inventory.targets.get(target_name)
    if target is None:
        raise UnknownTargetError(f"unknown target: {target_name!r}")

    output_fragments: dict[str, list[str]] = {
        name: list(fragments) for name, fragments in inventory.default_output_fragments.items()
    }
    variables: Variables = dict(inventory.default_variables)

    for group_name in target.groups:
        group = inventory.groups.get(group_name)
        if group is None:
            raise InventoryError(
                f"target {target_name!r} references undefined group {group_name!r}"
            )
        for output_name, fragments in group.output_fragments.items():
            output_fragments.setdefault(output_name, []).extend(fragments)
        variables.update(group.variables)

    for output_name, fragments in target.output_fragments.items():
        output_fragments.setdefault(output_name, []).extend(fragments)
    variables.update(target.variables)

    if cli_variables:
        variables.update(cli_variables)

    reserved_conflicts = RESERVED_VARIABLE_NAMES & variables.keys()
    if reserved_conflicts:
        raise InventoryError(
            f"target {target_name!r}: variable name(s) "
            f"{', '.join(f'`{name}`' for name in sorted(reserved_conflicts))} "
            f"are reserved (set automatically to the target's own name / the "
            f"current output's name) and must not be defined in defaults/group/"
            f"target variables or --var"
        )
    variables["target"] = target_name

    return ResolvedTarget(
        name=target_name,
        groups=target.groups,
        output_fragments={
            name: tuple(fragments) for name, fragments in output_fragments.items() if fragments
        },
        variables=variables,
    )


def load_secret_store(path: Path) -> SecretStore:
    """Load the optional untracked secrets file into a :class:`SecretStore`.

    The file shape is ``{secrets: {<name>: <value>}}`` (README.md "Secrets").
    A missing file is only an error if a ``from: secret`` reference later needs
    it; callers may pass an empty store when no ``--secrets`` file is given.
    Never log the returned values.
    """
    if not path.is_file():
        raise InventoryError(f"secrets file not found: {path}")

    try:
        raw = load_file(path)
    except Exception as exc:  # noqa: BLE001 - re-raise with secrets-file context
        raise InventoryError(f"cannot parse secrets file {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise InventoryError(f"secrets file {path} must be a mapping")

    schema: dict[str, Any] = cast(dict[str, Any], load_file(_schema_path("secrets.schema.json")))
    try:
        jsonschema.validate(instance=raw, schema=schema)
    except jsonschema.ValidationError as exc:
        raise InventoryError(
            f"secrets file {path} failed schema validation: {exc.message} "
            f"(at {'/'.join(str(part) for part in exc.path)})"
        ) from exc

    doc = cast(dict[str, Any], raw)
    return SecretStore(secrets=dict(doc.get("secrets", {}) or {}))
