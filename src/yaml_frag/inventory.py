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

from .models import Inventory, ResolvedTarget, SecretStore, Variables


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
) -> ResolvedTarget:
    """Resolve one target's final fragments and (still-unresolved) variables.

    Fragment order (PLAN.md "Fragment ordering") — precedence is positional:
        1. inventory ``defaults.fragments``
        2. each group's fragments, in the target's group order
        3. target ``fragments``
    Group and fragment order MUST be preserved (never alphabetized).

    Variable precedence (PLAN.md "Variable precedence"), later wins:
        1. inventory ``defaults.variables``
        2. group variables, in target group order
        3. target variables
        4. ``cli_variables`` (always literal strings)

    The returned ``variables`` are LAYERED BUT UNRESOLVED: a value may be an
    untagged literal or a ``from:`` source mapping. Secrets are no longer a
    precedence layer; they are a named store referenced via ``from: secret`` and
    resolved later by :func:`yaml_frag.sources.resolve_variables`.

    Raise :class:`~yaml_frag.errors.UnknownTargetError` for an unknown target
    and :class:`~yaml_frag.errors.InventoryError` for a referenced-but-undefined
    group.
    """
    raise NotImplementedError


def load_secret_store(path: Path) -> SecretStore:
    """Load the optional untracked secrets file into a :class:`SecretStore`.

    The file shape is ``{secrets: {<name>: <value>}}`` (PLAN.md "Secrets").
    A missing file is only an error if a ``from: secret`` reference later needs
    it; callers may pass an empty store when no ``--secrets`` file is given.
    Never log the returned values.
    """
    raise NotImplementedError
