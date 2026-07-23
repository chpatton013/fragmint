"""Explicit merge operations.

This is the heart of the renderer. Each function applies ONE fragment
operation to the working document in place, recording provenance and (for
replacements) override warnings via the provided
:class:`~provenance.ProvenanceTracker`.

Behavior is defined precisely in PLAN.md "Supported merge operations" and
illustrated in "Merge examples". Do NOT implement any implicit/"smart" merge
behavior (PLAN.md "Implementation preference"). Summary of the contract:

- ``set``: replace value at path, creating missing parent mappings; replacing
  an existing value is allowed and emits an override warning.
- ``merge``: recursively merge a mapping into a mapping. Both target and
  incoming must be mappings. Nested mappings merge recursively; scalars are
  replaced; lists are NOT implicitly merged — if a list exists at a path and
  the incoming mapping has a list at the same path, FAIL unless identical.
  Incompatible types (e.g. mapping into list) raise MergeConflictError.
- ``append`` / ``prepend``: value must be a list; create the target list if
  absent; fail if the existing target is not a list; preserve order; do not
  deduplicate unless ``deduplicate`` is set (keep first occurrence; structural
  equality for mappings).
- ``remove``: remove a whole field; missing is an error unless ``missing_ok``.
- ``remove-list-items``: target must be a list; remove all structurally-equal
  matches; fail if no requested value matched unless ``missing_ok``.
- ``assert``: verify a condition without mutating (see :func:`apply_assert`).

Every mutating function calls ``tracker.record(...)`` for the affected path(s)
so provenance and override reporting stay accurate.
"""

from __future__ import annotations

from .models import FragmentOperation, YamlValue
from .provenance import ProvenanceEntry, ProvenanceTracker


def apply_operation(
    document: dict[str, YamlValue],
    operation: FragmentOperation,
    *,
    entry: ProvenanceEntry,
    tracker: ProvenanceTracker,
) -> None:
    """Dispatch ``operation`` to the correct handler and apply it in place.

    ``entry`` identifies the fragment/operation for provenance. Raise the
    appropriate :mod:`errors` exception on any violation of the contract in
    the module docstring.
    """
    raise NotImplementedError


def structurally_equal(a: YamlValue, b: YamlValue) -> bool:
    """Return whether two parsed-YAML values are structurally equal.

    Used for ``deduplicate`` and ``remove-list-items`` matching (PLAN.md
    "append", "remove-list-items"). Order-sensitive for lists; key-order
    insensitive for mappings.
    """
    raise NotImplementedError


def apply_set(
    document: dict[str, YamlValue],
    operation: FragmentOperation,
    *,
    entry: ProvenanceEntry,
    tracker: ProvenanceTracker,
) -> None:
    """Apply a ``set`` operation. See PLAN.md "set"."""
    raise NotImplementedError


def apply_merge(
    document: dict[str, YamlValue],
    operation: FragmentOperation,
    *,
    entry: ProvenanceEntry,
    tracker: ProvenanceTracker,
) -> None:
    """Apply a ``merge`` operation. See PLAN.md "merge"."""
    raise NotImplementedError


def apply_append(
    document: dict[str, YamlValue],
    operation: FragmentOperation,
    *,
    entry: ProvenanceEntry,
    tracker: ProvenanceTracker,
) -> None:
    """Apply an ``append`` operation. See PLAN.md "append"."""
    raise NotImplementedError


def apply_prepend(
    document: dict[str, YamlValue],
    operation: FragmentOperation,
    *,
    entry: ProvenanceEntry,
    tracker: ProvenanceTracker,
) -> None:
    """Apply a ``prepend`` operation. See PLAN.md "prepend"."""
    raise NotImplementedError


def apply_remove(
    document: dict[str, YamlValue],
    operation: FragmentOperation,
    *,
    entry: ProvenanceEntry,
    tracker: ProvenanceTracker,
) -> None:
    """Apply a ``remove`` operation. See PLAN.md "remove"."""
    raise NotImplementedError


def apply_remove_list_items(
    document: dict[str, YamlValue],
    operation: FragmentOperation,
    *,
    entry: ProvenanceEntry,
    tracker: ProvenanceTracker,
) -> None:
    """Apply a ``remove-list-items`` operation. See PLAN.md "remove-list-items"."""
    raise NotImplementedError


def apply_assert(
    document: dict[str, YamlValue],
    operation: FragmentOperation,
) -> None:
    """Evaluate an ``assert`` operation without mutating ``document``.

    Supported forms (PLAN.md "assert"): ``equals``, ``exists: true|false``,
    and ``type: mapping|list|string|integer|boolean``. Raise
    :class:`~yaml_frag.errors.AssertionFailedError` on failure with
    the path and expectation in the message.
    """
    raise NotImplementedError
