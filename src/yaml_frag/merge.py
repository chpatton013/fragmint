"""Explicit merge operations.

This is the heart of the renderer. Each function applies ONE fragment
operation to the working document in place, recording provenance and (for
replacements) override warnings via the provided
:class:`~provenance.ProvenanceTracker`.

Behavior is defined precisely in README.md "Merge operations" and illustrated
in "Merge examples". Do NOT implement any implicit/"smart" merge behavior
(README.md "Design principles"). Summary of the contract:

- ``set``: replace value at path, creating missing parent mappings; replacing
  an existing value is allowed and emits an override warning, unless the
  operation sets ``overwrite_ok: true`` to declare the replacement intentional.
- ``merge``: recursively merge a mapping into a mapping. Both target and
  incoming must be mappings. Nested mappings merge recursively; scalars are
  replaced (also subject to ``overwrite_ok`` as above); lists are NOT
  implicitly merged — if a list exists at a path and the incoming mapping has
  a list at the same path, FAIL unless identical. Incompatible types (e.g.
  mapping into list) raise MergeConflictError.
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

from collections.abc import Callable

from . import pointer
from .errors import AssertionFailedError, MergeConflictError
from .models import FragmentOperation, ProvenanceEntry, YamlValue
from .provenance import ProvenanceTracker


def _type_name(value: YamlValue) -> str:
    """A human-readable YAML-ish type name for error messages."""
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, dict):
        return "mapping"
    if isinstance(value, list):
        return "list"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "float"
    if value is None:
        return "null"
    return type(value).__name__


def _escape_token(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def _join_path(base: str, key: str) -> str:
    escaped = _escape_token(key)
    if base in ("", "/"):
        return "/" + escaped
    return base + "/" + escaped


def _context(entry: ProvenanceEntry) -> str:
    return f"fragment {entry.fragment}, operation {entry.operation_index}"


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
    if operation.op == "assert":
        apply_assert(document, operation)
        return

    handler = _OPERATION_HANDLERS.get(operation.op)
    if handler is None:
        raise MergeConflictError(
            f"{_context(entry)}: unknown operation {operation.op!r}"
        )
    handler(document, operation, entry=entry, tracker=tracker)


def structurally_equal(a: YamlValue, b: YamlValue) -> bool:
    """Return whether two parsed-YAML values are structurally equal.

    Used for ``deduplicate`` and ``remove-list-items`` matching (README.md
    "Merge operations": `append`, `remove-list-items`). Order-sensitive for
    lists; key-order insensitive for mappings.
    """
    # bool is a subclass of int in Python; treat booleans as their own type
    # so that `True != 1` structurally.
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, dict) and isinstance(b, dict):
        if a.keys() != b.keys():
            return False
        return all(structurally_equal(a[key], b[key]) for key in a)
    if isinstance(a, dict) != isinstance(b, dict):
        return False
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return False
        return all(structurally_equal(x, y) for x, y in zip(a, b))
    if isinstance(a, list) != isinstance(b, list):
        return False
    return a == b


def apply_set(
    document: dict[str, YamlValue],
    operation: FragmentOperation,
    *,
    entry: ProvenanceEntry,
    tracker: ProvenanceTracker,
) -> None:
    """Apply a ``set`` operation. See README.md "Merge operations" (`set`)."""
    existed, _ = pointer.get(document, operation.path)
    pointer.set_(document, operation.path, operation.value)
    tracker.record(
        operation.path,
        entry,
        replaced_existing=existed,
        overwrite_ok=operation.overwrite_ok,
    )


def _merge_recursive(
    target: dict[str, YamlValue],
    incoming: dict[str, YamlValue],
    *,
    path: str,
    entry: ProvenanceEntry,
    tracker: ProvenanceTracker,
    overwrite_ok: bool,
) -> None:
    for key, incoming_value in incoming.items():
        child_path = _join_path(path, key)
        if key not in target:
            target[key] = incoming_value
            tracker.record(child_path, entry, replaced_existing=False)
            continue

        existing_value = target[key]

        if isinstance(existing_value, dict) and isinstance(incoming_value, dict):
            _merge_recursive(
                existing_value,
                incoming_value,
                path=child_path,
                entry=entry,
                tracker=tracker,
                overwrite_ok=overwrite_ok,
            )
            continue

        if isinstance(existing_value, dict) != isinstance(incoming_value, dict) or (
            isinstance(existing_value, list) != isinstance(incoming_value, list)
        ):
            prev = tracker.last_source(child_path)
            raise MergeConflictError(
                f"cannot merge {_type_name(incoming_value)} into "
                f"{_type_name(existing_value)} at {child_path}\n"
                f"  existing value from: "
                f"{prev.fragment if prev is not None else 'unknown'}\n"
                f"  incoming value from: {entry.fragment}"
            )

        if isinstance(existing_value, list) and isinstance(incoming_value, list):
            if structurally_equal(existing_value, incoming_value):
                continue
            prev = tracker.last_source(child_path)
            raise MergeConflictError(
                f"cannot merge list into list at {child_path}: lists differ "
                f"and merge never implicitly merges lists\n"
                f"  existing value from: "
                f"{prev.fragment if prev is not None else 'unknown'}\n"
                f"  incoming value from: {entry.fragment}"
            )

        # Both scalars (or otherwise compatible non-mapping, non-list
        # values): the incoming value replaces the existing one.
        target[key] = incoming_value
        tracker.record(
            child_path, entry, replaced_existing=True, overwrite_ok=overwrite_ok
        )


def apply_merge(
    document: dict[str, YamlValue],
    operation: FragmentOperation,
    *,
    entry: ProvenanceEntry,
    tracker: ProvenanceTracker,
) -> None:
    """Apply a ``merge`` operation. See README.md "Merge operations" (`merge`)."""
    if not isinstance(operation.value, dict):
        raise MergeConflictError(
            f"{_context(entry)}: merge requires a mapping value at "
            f"{operation.path} (got {_type_name(operation.value)})"
        )

    existed, current = pointer.get(document, operation.path)
    if not existed:
        pointer.set_(document, operation.path, {})
        existed, current = pointer.get(document, operation.path)

    if not isinstance(current, dict):
        prev = tracker.last_source(operation.path)
        raise MergeConflictError(
            f"cannot merge mapping into {_type_name(current)} at "
            f"{operation.path}\n"
            f"  existing value from: "
            f"{prev.fragment if prev is not None else 'unknown'}\n"
            f"  incoming value from: {entry.fragment}"
        )

    _merge_recursive(
        current,
        operation.value,
        path=operation.path,
        entry=entry,
        tracker=tracker,
        overwrite_ok=operation.overwrite_ok,
    )


def _dedupe(items: list[YamlValue]) -> list[YamlValue]:
    result: list[YamlValue] = []
    for item in items:
        if not any(structurally_equal(item, existing) for existing in result):
            result.append(item)
    return result


def _apply_list_insert(
    document: dict[str, YamlValue],
    operation: FragmentOperation,
    *,
    entry: ProvenanceEntry,
    tracker: ProvenanceTracker,
    prepend: bool,
) -> None:
    if not isinstance(operation.value, list):
        raise MergeConflictError(
            f"{_context(entry)}: {operation.op} requires a list value at "
            f"{operation.path} (got {_type_name(operation.value)})"
        )

    existed, current = pointer.get(document, operation.path)
    if not existed:
        current_list: list[YamlValue] = []
    elif isinstance(current, list):
        current_list = current
    else:
        raise MergeConflictError(
            f"{_context(entry)}: cannot {operation.op} onto non-list value "
            f"at {operation.path} (found {_type_name(current)})"
        )

    if prepend:
        new_list = list(operation.value) + list(current_list)
    else:
        new_list = list(current_list) + list(operation.value)

    if operation.deduplicate:
        new_list = _dedupe(new_list)

    pointer.set_(document, operation.path, new_list)
    tracker.record(operation.path, entry, replaced_existing=False)


def apply_append(
    document: dict[str, YamlValue],
    operation: FragmentOperation,
    *,
    entry: ProvenanceEntry,
    tracker: ProvenanceTracker,
) -> None:
    """Apply an ``append`` operation. See README.md "Merge operations" (`append`)."""
    _apply_list_insert(document, operation, entry=entry, tracker=tracker, prepend=False)


def apply_prepend(
    document: dict[str, YamlValue],
    operation: FragmentOperation,
    *,
    entry: ProvenanceEntry,
    tracker: ProvenanceTracker,
) -> None:
    """Apply a ``prepend`` operation. See README.md "Merge operations" (`prepend`)."""
    _apply_list_insert(document, operation, entry=entry, tracker=tracker, prepend=True)


def apply_remove(
    document: dict[str, YamlValue],
    operation: FragmentOperation,
    *,
    entry: ProvenanceEntry,
    tracker: ProvenanceTracker,
) -> None:
    """Apply a ``remove`` operation. See README.md "Merge operations" (`remove`)."""
    existed, _ = pointer.get(document, operation.path)
    if not existed:
        if operation.missing_ok:
            return
        raise MergeConflictError(
            f"{_context(entry)}: cannot remove missing path {operation.path} "
            f"(missing_ok not set)"
        )
    pointer.delete(document, operation.path, missing_ok=True)
    tracker.record(operation.path, entry, replaced_existing=False)


def apply_remove_list_items(
    document: dict[str, YamlValue],
    operation: FragmentOperation,
    *,
    entry: ProvenanceEntry,
    tracker: ProvenanceTracker,
) -> None:
    """Apply a ``remove-list-items`` operation. See README.md "Merge operations" (`remove-list-items`)."""
    if not isinstance(operation.value, list):
        raise MergeConflictError(
            f"{_context(entry)}: remove-list-items requires a list value at "
            f"{operation.path} (got {_type_name(operation.value)})"
        )

    existed, current = pointer.get(document, operation.path)
    if not existed or not isinstance(current, list):
        raise MergeConflictError(
            f"{_context(entry)}: remove-list-items target {operation.path} "
            f"is not a list"
        )

    to_remove = operation.value
    remaining: list[YamlValue] = []
    removed_any = False
    for item in current:
        if any(structurally_equal(item, target) for target in to_remove):
            removed_any = True
            continue
        remaining.append(item)

    if not removed_any:
        if operation.missing_ok:
            return
        raise MergeConflictError(
            f"{_context(entry)}: none of the requested values were found at "
            f"{operation.path}"
        )

    pointer.set_(document, operation.path, remaining)
    tracker.record(operation.path, entry, replaced_existing=False)


def _matches_assert_type(value: YamlValue, type_name: YamlValue) -> bool:
    checkers: dict[str, Callable[[YamlValue], bool]] = {
        "mapping": lambda v: isinstance(v, dict),
        "list": lambda v: isinstance(v, list),
        "string": lambda v: isinstance(v, str),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "boolean": lambda v: isinstance(v, bool),
    }
    if not isinstance(type_name, str) or type_name not in checkers:
        raise AssertionFailedError(f"unsupported assert type clause: {type_name!r}")
    return checkers[type_name](value)


def apply_assert(
    document: dict[str, YamlValue],
    operation: FragmentOperation,
) -> None:
    """Evaluate an ``assert`` operation without mutating ``document``.

    Supported forms (README.md "Merge operations" (`assert`)): ``equals``,
    ``exists: true|false``,
    and ``type: mapping|list|string|integer|boolean``. Raise
    :class:`~yaml_frag.errors.AssertionFailedError` on failure with
    the path and expectation in the message.
    """
    existed, value = pointer.get(document, operation.path)
    assertion = operation.assertion

    if "equals" in assertion:
        expected = assertion["equals"]
        if not existed or not structurally_equal(value, expected):
            actual = repr(value) if existed else "<missing>"
            raise AssertionFailedError(
                f"assertion failed at {operation.path}: expected value equal "
                f"to {expected!r}, got {actual}"
            )
        return

    if "exists" in assertion:
        expected_exists = bool(assertion["exists"])
        if bool(existed) != expected_exists:
            raise AssertionFailedError(
                f"assertion failed at {operation.path}: expected "
                f"exists={expected_exists}, got exists={bool(existed)}"
            )
        return

    if "type" in assertion:
        expected_type = assertion["type"]
        if not existed:
            raise AssertionFailedError(
                f"assertion failed at {operation.path}: expected type "
                f"{expected_type!r}, but path is missing"
            )
        if not _matches_assert_type(value, expected_type):
            raise AssertionFailedError(
                f"assertion failed at {operation.path}: expected type "
                f"{expected_type!r}, got {_type_name(value)!r}"
            )
        return

    raise AssertionFailedError(
        f"assertion at {operation.path} has no supported clause: {assertion!r}"
    )


_OPERATION_HANDLERS: dict[
    str,
    Callable[..., None],
] = {
    "set": apply_set,
    "merge": apply_merge,
    "append": apply_append,
    "prepend": apply_prepend,
    "remove": apply_remove,
    "remove-list-items": apply_remove_list_items,
}
