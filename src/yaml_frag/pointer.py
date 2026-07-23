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

from .models import YamlValue


def parse_pointer(pointer: str) -> tuple[str, ...]:
    """Split a JSON Pointer string into its decoded reference tokens.

    ``"/"`` and ``""`` both denote the root and return ``()``.
    ``"/a/b~1c"`` returns ``("a", "b/c")``.

    Raise :class:`~yaml_frag.errors.YamlFragError` (or a suitable
    subclass) if the pointer does not start with ``/`` and is not empty.
    """
    raise NotImplementedError


def get(document: YamlValue, pointer: str) -> tuple[bool, YamlValue]:
    """Look up ``pointer`` in ``document``.

    Return ``(True, value)`` when the path exists, else ``(False, None)``.
    Must not raise for a missing intermediate key; that is a normal "absent"
    result. Do raise if an intermediate node exists but is not a mapping and
    the caller needs to descend through it (decide and document the exact
    behavior when implementing; be conservative).
    """
    raise NotImplementedError


def set_(document: dict[str, YamlValue], pointer: str, value: YamlValue) -> None:
    """Set ``value`` at ``pointer``, creating missing parent mappings.

    Mutates ``document`` in place. Used by the ``set`` operation
    (PLAN.md "set"). If an existing intermediate node is a non-mapping,
    raise :class:`~yaml_frag.errors.MergeConflictError`.
    """
    raise NotImplementedError


def delete(document: dict[str, YamlValue], pointer: str, *, missing_ok: bool) -> bool:
    """Remove the field at ``pointer``.

    Return ``True`` if something was removed. If the path is absent and
    ``missing_ok`` is ``False``, raise
    :class:`~yaml_frag.errors.YamlFragError`; if ``True``, return
    ``False``. Used by the ``remove`` operation (PLAN.md "remove").
    """
    raise NotImplementedError
