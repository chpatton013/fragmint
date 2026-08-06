"""JSON Pointer-style path handling.

Paths address locations in the merged document. See README.md "Paths".

Rules:
- ``/`` addresses the root document.
- ``/a/b/c`` addresses nested mapping keys ``a`` -> ``b`` -> ``c``.
- Array indexes are NOT supported; a purely-numeric key is still treated as a
  mapping key, not a list index.
- JSON Pointer escaping: ``~1`` decodes to ``/`` and ``~0`` decodes to ``~``,
  decoded in that order per RFC 6901.

This module is intentionally small and pure (no I/O, no YAML), so it can be
unit-tested in isolation (see ``tests/test_merge.py``'s JSON-Pointer-escaping
tests).
"""

from __future__ import annotations

from .errors import FragmintError, MergeConflictError
from .models import DataValue


def _decode_token(token: str) -> str:
    # RFC 6901: decode "~1" -> "/" before "~0" -> "~".
    return token.replace("~1", "/").replace("~0", "~")


def parse_pointer(pointer: str) -> tuple[str, ...]:
    """Split a JSON Pointer string into its decoded reference tokens.

    ``"/"`` and ``""`` both denote the root and return ``()``.
    ``"/a/b~1c"`` returns ``("a", "b/c")``.

    Raises :class:`~fragmint.errors.FragmintError` if the pointer does not
    start with ``/`` and is not empty.
    """
    if pointer in ("", "/"):
        return ()
    if not pointer.startswith("/"):
        raise FragmintError(
            f"invalid JSON Pointer {pointer!r}: must start with '/' or be empty"
        )
    return tuple(_decode_token(token) for token in pointer[1:].split("/"))


def get(document: DataValue, pointer: str) -> tuple[bool, DataValue]:
    """Look up ``pointer`` in ``document``.

    Return ``(True, value)`` when the path exists, else ``(False, None)``.
    A missing intermediate key is a normal "absent" result, not an error, and
    so is an intermediate node that exists but is not a mapping — descending
    through a scalar or list simply reports the path as absent. Lookup never
    raises; only mutation (:func:`set_`, :func:`delete`) reports conflicts.
    """
    tokens = parse_pointer(pointer)
    node: DataValue = document
    for token in tokens:
        if not isinstance(node, dict) or token not in node:
            return False, None
        node = node[token]
    return True, node


def set_(document: dict[str, DataValue], pointer: str, value: DataValue) -> None:
    """Set ``value`` at ``pointer``, creating missing parent mappings.

    Mutates ``document`` in place. Used by the ``set`` operation (README.md
    "Merge operations"). If an existing intermediate node is a non-mapping,
    raise :class:`~fragmint.errors.MergeConflictError`.
    """
    tokens = parse_pointer(pointer)
    if not tokens:
        if not isinstance(value, dict):
            raise MergeConflictError(
                f"cannot set root document to non-mapping value "
                f"(got {type(value).__name__})"
            )
        document.clear()
        document.update(value)
        return

    node: dict[str, DataValue] = document
    for token in tokens[:-1]:
        child = node.get(token)
        if token not in node:
            new_child: dict[str, DataValue] = {}
            node[token] = new_child
            node = new_child
        elif isinstance(child, dict):
            node = child
        else:
            raise MergeConflictError(
                f"cannot set {pointer}: intermediate path segment "
                f"{token!r} is not a mapping (found {type(child).__name__})"
            )
    node[tokens[-1]] = value


def delete(document: dict[str, DataValue], pointer: str, *, missing_ok: bool) -> bool:
    """Remove the field at ``pointer``.

    Return ``True`` if something was removed. If the path is absent and
    ``missing_ok`` is ``False``, raise
    :class:`~fragmint.errors.FragmintError`; if ``True``, return
    ``False``. Used by the ``remove`` operation (README.md "Merge operations").
    """
    tokens = parse_pointer(pointer)
    if not tokens:
        # Removing the root clears the whole document.
        document.clear()
        return True

    node: DataValue = document
    for token in tokens[:-1]:
        if not isinstance(node, dict) or token not in node:
            if missing_ok:
                return False
            raise FragmintError(f"cannot remove {pointer}: path does not exist")
        node = node[token]

    last = tokens[-1]
    if not isinstance(node, dict) or last not in node:
        if missing_ok:
            return False
        raise FragmintError(f"cannot remove {pointer}: path does not exist")
    del node[last]
    return True
