"""JSON Pointer-style path handling.

Paths address locations in the merged document. See PLAN.md "Paths".

Rules to implement:
- ``/`` addresses the root document.
- ``/a/b/c`` addresses nested mapping keys ``a`` -> ``b`` -> ``c``.
- Array indexes are NOT supported in this version; a purely-numeric key is
  still treated as a mapping key, not a list index.
- JSON Pointer escaping: ``~1`` decodes to ``/`` and ``~0`` decodes to ``~``.
  Decode ``~1`` before ``~0`` is NOT correct; per RFC 6901 replace ``~1`` then
  ``~0`` — follow the spec exactly.

This module is intentionally small and pure (no I/O, no YAML), so it can be
unit-tested in isolation (PLAN.md "Unit tests": "JSON Pointer escaping").
"""

from __future__ import annotations

from .errors import MergeConflictError, YamlFragError
from .models import YamlValue


def _decode_token(token: str) -> str:
    # RFC 6901: decode "~1" -> "/" before "~0" -> "~".
    return token.replace("~1", "/").replace("~0", "~")


def parse_pointer(pointer: str) -> tuple[str, ...]:
    """Split a JSON Pointer string into its decoded reference tokens.

    ``"/"`` and ``""`` both denote the root and return ``()``.
    ``"/a/b~1c"`` returns ``("a", "b/c")``.

    Raise :class:`~yaml_frag.errors.YamlFragError` (or a suitable
    subclass) if the pointer does not start with ``/`` and is not empty.
    """
    if pointer in ("", "/"):
        return ()
    if not pointer.startswith("/"):
        raise YamlFragError(
            f"invalid JSON Pointer {pointer!r}: must start with '/' or be empty"
        )
    return tuple(_decode_token(token) for token in pointer[1:].split("/"))


def get(document: YamlValue, pointer: str) -> tuple[bool, YamlValue]:
    """Look up ``pointer`` in ``document``.

    Return ``(True, value)`` when the path exists, else ``(False, None)``.
    Must not raise for a missing intermediate key; that is a normal "absent"
    result. Do raise if an intermediate node exists but is not a mapping and
    the caller needs to descend through it (decide and document the exact
    behavior when implementing; be conservative).
    """
    tokens = parse_pointer(pointer)
    node: YamlValue = document
    for token in tokens:
        if not isinstance(node, dict) or token not in node:
            return False, None
        node = node[token]
    return True, node


def set_(document: dict[str, YamlValue], pointer: str, value: YamlValue) -> None:
    """Set ``value`` at ``pointer``, creating missing parent mappings.

    Mutates ``document`` in place. Used by the ``set`` operation
    (PLAN.md "set"). If an existing intermediate node is a non-mapping,
    raise :class:`~yaml_frag.errors.MergeConflictError`.
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

    node: dict[str, YamlValue] = document
    for token in tokens[:-1]:
        child = node.get(token)
        if token not in node:
            new_child: dict[str, YamlValue] = {}
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


def delete(document: dict[str, YamlValue], pointer: str, *, missing_ok: bool) -> bool:
    """Remove the field at ``pointer``.

    Return ``True`` if something was removed. If the path is absent and
    ``missing_ok`` is ``False``, raise
    :class:`~yaml_frag.errors.YamlFragError`; if ``True``, return
    ``False``. Used by the ``remove`` operation (PLAN.md "remove").
    """
    tokens = parse_pointer(pointer)
    if not tokens:
        # Removing the root clears the whole document.
        document.clear()
        return True

    node: YamlValue = document
    for token in tokens[:-1]:
        if not isinstance(node, dict) or token not in node:
            if missing_ok:
                return False
            raise YamlFragError(f"cannot remove {pointer}: path does not exist")
        node = node[token]

    last = tokens[-1]
    if not isinstance(node, dict) or last not in node:
        if missing_ok:
            return False
        raise YamlFragError(f"cannot remove {pointer}: path does not exist")
    del node[last]
    return True
