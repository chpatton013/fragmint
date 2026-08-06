"""Per-target resolution and the secret store.

See README.md "Authoring inventory", "Fragment order", "Variable
precedence", and "Secrets". Loading the inventory document itself — parsing,
schema validation, the import closure, and reference resolution — lives in
:mod:`modules`; this module resolves a single target, out of an already
flattened :class:`~yaml_frag.models.Project`, into a
:class:`~yaml_frag.models.ResolvedTarget`.

This module must contain NO domain-specific knowledge (README.md "Design
principles": separation of data and rendering logic).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import jsonschema

from .errors import InventoryError, UnknownTargetError
from .models import Project, Ref, ResolvedTarget, SecretStore, Variables
from .yamlio import load_file

#: Variable names the renderer sets automatically; a project must not define
#: them itself (README.md "Variable precedence"). ``target`` is injected here,
#: in :func:`resolve_target`, since it's the same for every output. ``output``
#: is injected later, once per output, in :func:`yaml_frag.render.render_target`
#: (it differs per output within the same target) — but conflicts with it are
#: still rejected here, at the single point where all variable layers merge.
#: The aggregate scope is the other injection site for ``output`` (set to the
#: aggregate output's own name) and the other place the conflict is rejected
#: — see :meth:`yaml_frag.render.RenderSession.aggregate_variables`, where
#: ``target`` is deliberately never injected at all (README.md "Aggregate
#: outputs" — "The aggregate scope").
RESERVED_VARIABLE_NAMES = frozenset({"target", "output"})


def _schema_path(name: str) -> Path:
    """Resolve a packaged tool-format schema under ``yaml_frag/schemas/``."""
    return Path(__file__).resolve().parent / "schemas" / name


def resolve_target(
    project: Project,
    target_name: str,
    *,
    cli_variables: Variables | None = None,
) -> ResolvedTarget:
    """Resolve one target's final per-output fragments and (still-unresolved)
    variables.

    Fragment order, per output (README.md "Fragment order" and "Composition
    and ordering") — precedence is positional:
        1. every module's ``defaults.outputs.<name>.fragments``, in closure
           order, followed by the inventory's own (already concatenated into
           ``project.default_output_fragments`` by :func:`modules.flatten`);
        2. each group the target lists' ``outputs.<name>.fragments``, in the
           target's group order (each group's own fragments already merged
           across the documents that defined it, in closure order);
        3. the target's own ``outputs.<name>.fragments``.
    Group and fragment order MUST be preserved (never alphabetized). An output
    name that no layer contributes fragments to is omitted from the result
    entirely — that output is simply not produced for this target.

    Variable precedence (README.md "Variable precedence"), later wins, and is
    NOT per-output (variables are shared across all of a target's outputs):
        1. ``project.default_variables`` (every module's defaults, closure
           order, then the inventory's — already layered by
           :func:`modules.flatten`);
        2. group variables, in target group order;
        3. target variables;
        4. ``cli_variables`` (always literal strings).

    The returned ``variables`` are LAYERED BUT UNRESOLVED: a value may be an
    untagged literal or a ``from:`` source mapping. Secrets are not a
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
    target = project.targets.get(target_name)
    if target is None:
        raise UnknownTargetError(f"unknown target: {target_name!r}")

    output_fragments: dict[str, list[Ref]] = {
        name: list(fragments) for name, fragments in project.default_output_fragments.items()
    }
    variables: Variables = dict(project.default_variables)

    for group_name in target.groups:
        group = project.groups.get(group_name)
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
    except Exception as exc:
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


__all__ = ["RESERVED_VARIABLE_NAMES", "load_secret_store", "resolve_target"]
