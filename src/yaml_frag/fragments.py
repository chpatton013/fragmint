"""Fragment loading and validation.

See README.md "Authoring fragments" and "Validation". Resolving a fragment
*reference* to a file path — bare vs. module-qualified, which directory it's
relative to, and containment within that module's own tree — lives in
:mod:`modules` (:func:`yaml_frag.modules.fragment_path`); this module loads
and validates whatever file that resolution names.

Responsibilities:
1. Parse and validate the fragment against ``schemas/fragment.schema.json`` and
   the rules in README.md "Validation":
   - supported ``fragment.version``;
   - each operation ``op`` is recognized;
   - ``path`` is a valid JSON Pointer;
   - required fields present per op (e.g. ``value`` for set/merge/append/...);
   - ``append``/``prepend`` values are lists;
   - ``merge`` values are mappings;
   - assertions use a supported form.
2. Return a fully-typed :class:`~models.Fragment`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import jsonschema

from .errors import FragmentError, UnknownFragmentError
from .models import Fragment, FragmentOperation
from .yamlio import load_file

#: The only fragment format version supported by this release.
SUPPORTED_FRAGMENT_VERSION = 1

#: Operations recognized in the ``operations`` list. See README.md "Merge
#: operations".
RECOGNIZED_OPS = frozenset(
    {"set", "merge", "append", "prepend", "remove", "remove-list-items", "assert"}
)

#: Assertion clause keys recognized for ``assert`` operations.
ASSERTION_KEYS = frozenset({"equals", "exists", "type"})
ASSERTION_TYPES = frozenset({"mapping", "list", "string", "integer", "boolean"})


def _schema_path(name: str) -> Path:
    """Resolve a packaged tool-format schema under ``yaml_frag/schemas/``."""
    return Path(__file__).resolve().parent / "schemas" / name


def _parse_operation(name: str, index: int, raw: dict[str, Any]) -> FragmentOperation:
    op = raw.get("op")
    if op not in RECOGNIZED_OPS:
        raise FragmentError(f"fragment {name}, operation {index}: unrecognized op {op!r}")

    path = raw.get("path")
    if not isinstance(path, str) or not (path == "" or path.startswith("/")):
        raise FragmentError(f"fragment {name}, operation {index}: invalid path {path!r}")

    if op == "merge":
        if not isinstance(raw.get("value"), dict):
            raise FragmentError(
                f"fragment {name}, operation {index}: `merge` requires a mapping `value`"
            )
    elif op in ("append", "prepend"):
        if not isinstance(raw.get("value"), list):
            raise FragmentError(
                f"fragment {name}, operation {index}: `{op}` requires a list `value`"
            )
    elif op == "set":
        if "value" not in raw:
            raise FragmentError(
                f"fragment {name}, operation {index}: `set` requires a `value`"
            )
    elif op == "remove-list-items":
        if not isinstance(raw.get("value"), list):
            raise FragmentError(
                f"fragment {name}, operation {index}: `remove-list-items` requires a list `value`"
            )
    elif op == "remove":
        pass
    elif op == "assert":
        assertion_keys = ASSERTION_KEYS & raw.keys()
        if not assertion_keys:
            raise FragmentError(
                f"fragment {name}, operation {index}: `assert` requires one of "
                f"{sorted(ASSERTION_KEYS)}"
            )
        if "type" in raw and raw["type"] not in ASSERTION_TYPES:
            raise FragmentError(
                f"fragment {name}, operation {index}: unsupported assert type {raw['type']!r}"
            )

    assertion: dict[str, Any] = {key: raw[key] for key in ASSERTION_KEYS if key in raw}

    return FragmentOperation(
        op=op,
        path=path,
        value=raw.get("value"),
        deduplicate=bool(raw.get("deduplicate", False)),
        missing_ok=bool(raw.get("missing_ok", False)),
        overwrite_ok=bool(raw.get("overwrite_ok", False)),
        assertion=assertion,
    )


def load_fragment(path: Path, name: str) -> Fragment:
    """Load, validate, and return the fragment file at ``path``.

    ``path`` is an already-resolved, containment-checked absolute path (see
    :func:`yaml_frag.modules.fragment_path`). ``name`` is the fragment's
    qualified reference (see :func:`yaml_frag.modules.display_ref`), used for
    diagnostics and provenance — bare only when the fragment resolved to the
    root document, module-qualified otherwise. Raise
    :class:`~yaml_frag.errors.UnknownFragmentError` if the file does not
    exist, and :class:`~yaml_frag.errors.FragmentError` for any validation
    failure, with fragment name context.
    """
    if not path.is_file():
        raise UnknownFragmentError(f"fragment {name!r} not found (expected at {path})")

    try:
        raw = load_file(path)
    except Exception as exc:
        raise FragmentError(f"cannot parse fragment {name} ({path}): {exc}") from exc

    if not isinstance(raw, dict):
        raise FragmentError(f"fragment {name} ({path}) must be a mapping")

    schema: dict[str, Any] = cast(dict[str, Any], load_file(_schema_path("fragment.schema.json")))
    try:
        jsonschema.validate(instance=raw, schema=schema)
    except jsonschema.ValidationError as exc:
        raise FragmentError(
            f"fragment {name} ({path}) failed schema validation: {exc.message} "
            f"(at {'/'.join(str(part) for part in exc.path)})"
        ) from exc

    # Schema validation above guarantees the shape README.md documents; treat the
    # parsed document as loosely-typed data from here on rather than fighting
    # the recursive YamlValue union.
    doc = cast(dict[str, Any], raw)

    fragment_meta = doc.get("fragment", {})
    version = fragment_meta.get("version")
    if version != SUPPORTED_FRAGMENT_VERSION:
        raise FragmentError(
            f"fragment {name} ({path}): unsupported version {version!r}, "
            f"expected {SUPPORTED_FRAGMENT_VERSION}"
        )

    description = fragment_meta.get("description", "")

    required_variables = tuple(doc.get("requires", {}).get("variables", []) or [])

    operations = tuple(
        _parse_operation(name, index, op_raw)
        for index, op_raw in enumerate(doc.get("operations", []) or [])
    )

    return Fragment(
        version=version,
        name=name,
        description=description,
        required_variables=required_variables,
        operations=operations,
    )
