"""Inventory loading, validation, and per-machine resolution.

See PLAN.md "Inventory format", "Fragment ordering", "Variable precedence",
"Optional external secret variables", and "Inventory validation".

Responsibilities:
1. Parse the inventory YAML into an :class:`~models.Inventory`.
2. Validate it against ``schemas/inventory.schema.json`` and the structural
   rules in PLAN.md "Inventory validation" (unique machine/group names,
   referenced groups exist, fragments are lists of strings, variables are
   mappings, order preserved, no group cycles if nested groups are added).
3. Resolve a single machine into a :class:`~models.ResolvedMachine` with the
   final ordered fragment list and fully layered variable map.

This module must contain NO host-specific knowledge (PLAN.md "Separation of
data and rendering logic").
"""

from __future__ import annotations

from pathlib import Path

from .models import Inventory, ResolvedMachine, Variables


def load_inventory(path: Path) -> Inventory:
    """Load and validate the inventory file.

    Raise :class:`~autoinstall_renderer.errors.InventoryError` (with file/field
    context) on any schema or structural violation from PLAN.md "Inventory
    validation".
    """
    raise NotImplementedError


def resolve_machine(
    inventory: Inventory,
    machine_name: str,
    *,
    cli_variables: Variables | None = None,
    secrets: Variables | None = None,
) -> ResolvedMachine:
    """Resolve one machine's final fragments and variables.

    Fragment order (PLAN.md "Fragment ordering"):
        1. inventory ``defaults.fragments``
        2. each group's fragments, in the machine's group order
        3. machine ``fragments``
    Group and fragment order MUST be preserved (never alphabetized).

    Variable precedence (PLAN.md "Variable precedence"), later wins:
        1. inventory ``defaults.variables``
        2. group variables, in machine group order
        3. machine variables
        4. ``secrets`` (per-machine overlay; see below)
        5. ``cli_variables``

    ``secrets`` here is the already-extracted per-machine mapping from the
    optional secrets file (see :func:`load_secrets`). Secret values must never
    be logged (PLAN.md "Optional external secret variables").

    Raise :class:`~autoinstall_renderer.errors.UnknownMachineError` for an
    unknown machine and :class:`~autoinstall_renderer.errors.InventoryError`
    for a referenced-but-undefined group.
    """
    raise NotImplementedError


def load_secrets(path: Path, machine_name: str) -> Variables:
    """Load the optional untracked secrets overlay for a single machine.

    The secrets file shape is ``{machines: {<name>: {<var>: <value>}}}``
    (PLAN.md "Optional external secret variables"). Return an empty mapping if
    the machine has no secrets entry. Never log the returned values.
    """
    raise NotImplementedError
