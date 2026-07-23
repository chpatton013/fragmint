"""Fragment resolution, loading, and validation.

See PLAN.md "Fragment format", "Fragment names", and "Fragment validation".

Responsibilities:
1. Resolve a fragment reference (e.g. ``hardware/gb10``) to a file path under
   the fragments directory (``fragments/hardware/gb10.yaml``).
2. Parse and validate the fragment against ``schemas/fragment.schema.json`` and
   the rules in PLAN.md "Fragment validation":
   - supported ``fragment.version``;
   - ``fragment.name`` matches the resolved path (mismatch is an error);
   - each operation ``op`` is recognized;
   - ``path`` is a valid JSON Pointer;
   - required fields present per op (e.g. ``value`` for set/merge/append/...);
   - ``append``/``prepend`` values are lists;
   - ``merge`` values are mappings;
   - assertions use a supported form.
3. Return a fully-typed :class:`~models.Fragment`.
"""

from __future__ import annotations

from pathlib import Path

from .models import Fragment

#: The only fragment format version supported by this release.
SUPPORTED_FRAGMENT_VERSION = 1


def resolve_fragment_path(fragments_dir: Path, name: str) -> Path:
    """Map a fragment reference to its file path.

    ``resolve_fragment_path(Path("fragments"), "hardware/gb10")`` ->
    ``fragments/hardware/gb10.yaml``. Does not check existence here; the loader
    raises :class:`~autoinstall_renderer.errors.UnknownFragmentError` if the
    file is missing.
    """
    raise NotImplementedError


def load_fragment(fragments_dir: Path, name: str) -> Fragment:
    """Load, validate, and return the fragment referenced by ``name``.

    Raise :class:`~autoinstall_renderer.errors.UnknownFragmentError` if the
    file does not exist, and
    :class:`~autoinstall_renderer.errors.FragmentError` for any validation
    failure (including a name/path mismatch), with fragment name context.
    """
    raise NotImplementedError
