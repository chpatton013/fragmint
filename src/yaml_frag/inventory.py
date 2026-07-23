"""Inventory loading, validation, and per-target resolution.

See PLAN.md "Inventory format", "Fragment ordering", "Variable precedence",
"Optional external secret variables", and "Validation".

Responsibilities:
1. Parse the inventory YAML into an :class:`~models.Inventory`.
2. Validate it against ``schemas/inventory.schema.json`` and the structural
   rules in PLAN.md (unique target/group names, referenced groups exist,
   fragments are lists of strings, variables are mappings, order preserved, no
   group cycles if nested groups are added).
3. Resolve a single target into a :class:`~models.ResolvedTarget` with the
   final ordered fragment list and fully layered variable map.

This module must contain NO domain-specific knowledge (PLAN.md "Separation of
data and rendering logic").
"""

from __future__ import annotations

from pathlib import Path

from .models import Inventory, ResolvedTarget, Variables


def load_inventory(path: Path) -> Inventory:
    """Load and validate the inventory file.

    Raise :class:`~yaml_frag.errors.InventoryError` (with file/field context)
    on any schema or structural violation from PLAN.md "Validation".
    """
    raise NotImplementedError


def resolve_target(
    inventory: Inventory,
    target_name: str,
    *,
    cli_variables: Variables | None = None,
    secrets: Variables | None = None,
) -> ResolvedTarget:
    """Resolve one target's final fragments and variables.

    Fragment order (PLAN.md "Fragment ordering") — precedence is positional:
        1. inventory ``defaults.fragments``
        2. each group's fragments, in the target's group order
        3. target ``fragments``
    Group and fragment order MUST be preserved (never alphabetized).

    Variable precedence (PLAN.md "Variable precedence"), later wins:
        1. inventory ``defaults.variables``
        2. group variables, in target group order
        3. target variables
        4. ``secrets`` (per-target overlay; see below)
        5. ``cli_variables``

    ``secrets`` here is the already-extracted per-target mapping from the
    optional secrets file (see :func:`load_secrets`). Secret values must never
    be logged (PLAN.md "Optional external secret variables").

    Raise :class:`~yaml_frag.errors.UnknownTargetError` for an unknown target
    and :class:`~yaml_frag.errors.InventoryError` for a referenced-but-undefined
    group.
    """
    raise NotImplementedError


def load_secrets(path: Path, target_name: str) -> Variables:
    """Load the optional untracked secrets overlay for a single target.

    The secrets file shape is ``{targets: {<name>: {<var>: <value>}}}``
    (PLAN.md "Optional external secret variables"). Return an empty mapping if
    the target has no secrets entry. Never log the returned values.
    """
    raise NotImplementedError
