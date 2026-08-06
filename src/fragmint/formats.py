"""The format boundary: safe loading and deterministic serialization for
every supported input/output format (YAML, TOML, JSON).

Everything else in the package operates on the plain
:data:`~fragmint.models.DataValue` shape (mapping/list/scalar/``None``) and
has no knowledge of which file format produced or will hold it. This module
is the only place that talks to a concrete format library.

Loading, for every format: parse safely (no arbitrary Python object
construction / no unsafe tags), then normalize into the plain
``dict``/``list``/scalar shape :data:`~fragmint.models.DataValue` describes,
so every codec's output is indistinguishable downstream.

Serialization, for every format: deterministic (no key sorting — mapping
insertion order is preserved, subject to each format's own syntactic
constraints; see the per-codec ``dump`` docstrings), diff-friendly, and
ending with exactly one trailing newline. A value a format cannot express is
a fail-closed :class:`~fragmint.errors.SerializationError` naming the JSON
Pointer path, never a silent coercion.

Any header such as ``#cloud-config`` is NOT data in any format; it comes from
the output's text template, applied by :mod:`render`, not by this module.
See README.md "The module model" and "Serialization".
"""

from __future__ import annotations

import datetime
import io
import json
import math
import tomllib
from functools import cache
from pathlib import Path
from typing import Any, Protocol, cast

import tomli_w
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError
from ruamel.yaml.nodes import ScalarNode
from ruamel.yaml.resolver import VersionedResolver
from ruamel.yaml.scalarstring import LiteralScalarString, SingleQuotedScalarString

from . import pointer
from .errors import FragmintError, SerializationError
from .models import DataValue, Format

#: Suffixes that select an output's serialization format when its `path`
#: carries no explicit `format:`. `.yml` is accepted here but deliberately
#: NOT a fragment candidate suffix (see FRAGMENT_SUFFIXES).
OUTPUT_SUFFIXES: dict[str, Format] = {
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".json": "json",
}

#: The complete candidate set for an extension-less fragment reference. Order
#: is used only to make the *ambiguity* diagnostic deterministic; it never
#: resolves an ambiguity (see README.md "Reference resolution").
FRAGMENT_SUFFIXES: tuple[str, ...] = (".yaml", ".toml", ".json")

#: The format used when a path carries no recognized suffix and no explicit
#: `format:` is given.
DEFAULT_FORMAT: Format = "yaml"


class Codec(Protocol):
    """A single format's load/dump pair. See the module docstring for the
    shared contract every implementation must satisfy."""

    name: Format

    def load(self, text: str, *, path: Path) -> DataValue: ...

    def dump(self, document: DataValue) -> str: ...


def _normalize(node: Any) -> DataValue:
    """Recursively convert a format library's parsed containers into plain
    dict/list/scalars. Every codec's ``load`` ends with this so all three
    formats agree on the model shape."""
    if isinstance(node, dict):
        return {str(key): _normalize(value) for key, value in node.items()}
    if isinstance(node, list):
        return [_normalize(item) for item in node]
    if node is None or isinstance(node, (bool, int, float, str)):
        return node
    # Fallback: coerce unexpected scalar-ish types (e.g. ruamel's own str
    # subclasses) to plain str.
    return str(node)


# --------------------------------------------------------------------------
# YAML
# --------------------------------------------------------------------------

_load_yaml = YAML(typ="safe")
_load_yaml.allow_duplicate_keys = False

# Drives ruamel's own YAML 1.1 implicit-tag resolver (the core-schema rules a
# YAML 1.1 parser, e.g. cloud-init's PyYAML, applies to a bare plain scalar)
# to find strings that would silently change type if left unquoted. This is
# the same table ruamel uses for its own YAML(version=(1, 1)) loader/dumper;
# it needs no loader/dumper instance to answer a single scalar's tag.
_resolver_1_1 = VersionedResolver(version=(1, 1))


@cache
def _needs_yaml_1_1_quoting(value: str) -> bool:
    """True if a YAML 1.1 parser would not read ``value`` back as this exact
    string when emitted as a bare (unquoted) plain scalar.

    The spec's resolver is the sole authority, deliberately including cases a
    given implementation happens to be laxer about: bare ``y``/``n`` are
    boolean aliases per the spec, so they are quoted even though PyYAML alone
    would read them back as strings. Quoting a scalar no reader would have
    retyped costs two characters; leaving one bare that some reader retypes
    corrupts a value silently.
    """
    tag = _resolver_1_1.resolve(ScalarNode, value, (True, False))
    return bool(tag.suffix != "tag:yaml.org,2002:str")


def _to_dumpable(node: DataValue) -> Any:
    """Convert plain DataValue into ruamel-friendly structures: block literal
    scalars for multiline strings, single-quoted scalars for strings that a
    YAML 1.1 reader would not parse back as themselves when left bare."""
    if isinstance(node, dict):
        return {key: _to_dumpable(value) for key, value in node.items()}
    if isinstance(node, list):
        return [_to_dumpable(item) for item in node]
    if isinstance(node, str) and "\n" in node:
        return LiteralScalarString(node)
    if isinstance(node, str) and _needs_yaml_1_1_quoting(node):
        return SingleQuotedScalarString(node)
    return node


class _YamlCodec:
    """YAML 1.2 emitted (via ``ruamel.yaml``), but quoted defensively against
    YAML 1.1 readers such as cloud-init's PyYAML-based parser — see
    :func:`_needs_yaml_1_1_quoting`.

    Determinism/diff-friendliness guarantees (README.md "Serialization"):
    preserve mapping insertion order (never sort keys); two-space indentation;
    never emit Python-specific YAML tags; booleans as ``true``/``false``;
    block style for multiline strings; exactly one trailing newline.
    """

    name: Format = "yaml"

    def load(self, text: str, *, path: Path) -> DataValue:
        try:
            data = _load_yaml.load(text)
        except YAMLError as exc:
            raise FragmintError(f"invalid yaml in {path}: {exc}") from exc
        return _normalize(data)

    def dump(self, document: DataValue) -> str:
        yaml = YAML()
        yaml.default_flow_style = False
        # mapping=2 gives two-space indentation; sequence=4/offset=2 gives list
        # items indented two spaces under their key ("key:\n  - item"),
        # matching README.md's examples. ruamel preserves plain-dict
        # insertion order by default (no key sorting), satisfying the "do not
        # sort keys" rule.
        yaml.indent(mapping=2, sequence=4, offset=2)
        yaml.width = 2**31 - 1
        yaml.allow_unicode = True

        stream = io.StringIO()
        yaml.dump(_to_dumpable(document), stream)
        text = stream.getvalue()
        return text.rstrip("\n") + "\n"


class _TomlCodec:
    """TOML, read via the stdlib ``tomllib`` and written via ``tomli_w``.

    Determinism/diff-friendliness guarantees (README.md "Serialization"):
    key ordering is preserved *within a table* for scalars, arrays, and
    inline values, but TOML's own syntax requires a table's sub-tables and
    arrays-of-tables to follow all of that table's scalar keys — the tool
    never sorts keys, but cannot make TOML express an ordering its grammar
    forbids. ``None`` has no TOML representation and fails closed, naming
    the pointer, rather than being silently dropped or coerced. Multi-line
    strings use TOML's ``\"\"\"`` form when lossless; a document containing a
    bare ``\\r`` anywhere falls back to single-line escaped strings for
    *every* string in the document, since ``tomli_w`` controls this per
    document, not per string, and the fallback is exactly as lossless.
    Exactly one trailing newline.
    """

    name: Format = "toml"

    def load(self, text: str, *, path: Path) -> DataValue:
        try:
            data = tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            raise FragmintError(f"invalid toml in {path}: {exc}") from exc
        _reject_toml_datetimes(data, "", path=path)
        return _normalize(data)

    def dump(self, document: DataValue) -> str:
        _reject_null(document, "", fmt="toml")
        multiline = not _contains_carriage_return(document)
        text = tomli_w.dumps(
            cast(dict[str, Any], document), multiline_strings=multiline, indent=2
        )
        return text.rstrip("\n") + "\n"


def _reject_toml_datetimes(node: Any, ptr: str, *, path: Path) -> None:
    """TOML's ``date``/``time``/``datetime`` types have no home in
    :data:`~fragmint.models.DataValue`; unlike YAML (which stringifies them
    for backward compatibility — see :mod:`_normalize`'s fallback), TOML input
    fails closed on them, naming the pointer and telling the author to quote
    the value. See README.md "Supported formats"."""
    if isinstance(node, (datetime.date, datetime.time)):
        raise FragmintError(
            f"cannot load {path}: a date/time value at {ptr or '/'} has no "
            f"home in fragmint's data model; quote it as a string"
        )
    if isinstance(node, dict):
        for key, value in node.items():
            _reject_toml_datetimes(value, pointer.join(ptr, key), path=path)
    elif isinstance(node, list):
        for index, item in enumerate(node):
            _reject_toml_datetimes(item, pointer.join(ptr, str(index)), path=path)


def _reject_null(node: Any, ptr: str, *, fmt: Format) -> None:
    """TOML has no null. Raise :class:`~fragmint.errors.SerializationError`
    naming the pointer and pointing at the actual remedy (``op: remove``)
    rather than a generic "value not supported" message."""
    if node is None:
        raise SerializationError(
            f"cannot serialize to {fmt}: null value at {ptr or '/'}; {fmt} has "
            f"no null — remove the key instead, or use a different output format"
        )
    if isinstance(node, dict):
        for key, value in node.items():
            _reject_null(value, pointer.join(ptr, key), fmt=fmt)
    elif isinstance(node, list):
        for index, item in enumerate(node):
            _reject_null(item, pointer.join(ptr, str(index)), fmt=fmt)


def _contains_carriage_return(node: Any) -> bool:
    """True if any string value anywhere in ``node`` contains ``\\r`` — see
    :meth:`_TomlCodec.dump`."""
    if isinstance(node, str):
        return "\r" in node
    if isinstance(node, dict):
        return any(_contains_carriage_return(value) for value in node.values())
    if isinstance(node, list):
        return any(_contains_carriage_return(item) for item in node)
    return False


def _reject_non_finite_float(node: Any, ptr: str, *, fmt: Format) -> None:
    """JSON has no ``NaN``/``Infinity``. Raise
    :class:`~fragmint.errors.SerializationError` naming the pointer and the
    offending value."""
    if isinstance(node, float) and not math.isfinite(node):
        raise SerializationError(
            f"cannot serialize to {fmt}: non-finite float at {ptr or '/'} "
            f"({node}); {fmt} cannot represent nan or infinity"
        )
    if isinstance(node, dict):
        for key, value in node.items():
            _reject_non_finite_float(value, pointer.join(ptr, key), fmt=fmt)
    elif isinstance(node, list):
        for index, item in enumerate(node):
            _reject_non_finite_float(item, pointer.join(ptr, str(index)), fmt=fmt)


class _JsonCodec:
    """JSON, read and written via the stdlib ``json`` module.

    Determinism/diff-friendliness guarantees (README.md "Serialization"):
    insertion order preserved (``sort_keys=False``); two-space indentation;
    raw UTF-8 (``ensure_ascii=False``); exactly one trailing newline. A
    non-finite float has no JSON representation and fails closed, naming the
    pointer, before ``json.dumps`` (called with ``allow_nan=False`` as a
    second line of defense) ever runs.
    """

    name: Format = "json"

    def load(self, text: str, *, path: Path) -> DataValue:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise FragmintError(f"invalid json in {path}: {exc}") from exc
        return _normalize(data)

    def dump(self, document: DataValue) -> str:
        _reject_non_finite_float(document, "", fmt="json")
        text = json.dumps(
            document, indent=2, ensure_ascii=False, allow_nan=False, sort_keys=False
        )
        return text.rstrip("\n") + "\n"


_CODECS: dict[Format, Codec] = {
    "yaml": _YamlCodec(),
    "toml": _TomlCodec(),
    "json": _JsonCodec(),
}


def codec_for(fmt: Format) -> Codec:
    """The codec registered for ``fmt``."""
    return _CODECS[fmt]


def format_for_path(path: str | Path) -> Format | None:
    """Infer a format from ``path``'s suffix, or ``None`` if the suffix is
    unknown, absent, or not (yet) backed by a registered codec. See
    :data:`OUTPUT_SUFFIXES`."""
    fmt = OUTPUT_SUFFIXES.get(Path(path).suffix)
    return fmt if fmt in _CODECS else None


def parse_text(text: str, fmt: Format, *, path: Path) -> DataValue:
    """Parse ``text`` (already read from ``path``) as ``fmt``. ``path`` is
    used only for error messages."""
    return codec_for(fmt).load(text, path=path)


def load_data_file(path: Path) -> DataValue:
    """Parse a single document from ``path``, dispatching on its suffix via
    :data:`OUTPUT_SUFFIXES` and defaulting to :data:`DEFAULT_FORMAT` for an
    unknown or absent suffix.

    Raise :class:`~fragmint.errors.FragmintError` (or the most specific
    applicable subclass at the call site) on read or parse failure, with the
    file path included in the message.
    """
    fmt = format_for_path(path) or DEFAULT_FORMAT
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise FragmintError(f"cannot read {fmt} file {path}: {exc}") from exc
    # Each codec's own `load` already raises a FragmintError naming `path`
    # and `fmt` (see e.g. `_YamlCodec.load`), so it is not re-wrapped here.
    return parse_text(text, fmt, path=path)


def dump_document(document: DataValue, fmt: Format) -> str:
    """Serialize ``document`` deterministically as ``fmt``.

    Does NOT include any output-template header. Ends with a single trailing
    newline. See the module docstring and the per-codec docstrings for the
    exact guarantees.
    """
    return codec_for(fmt).dump(document)


__all__ = [
    "DEFAULT_FORMAT",
    "FRAGMENT_SUFFIXES",
    "OUTPUT_SUFFIXES",
    "Codec",
    "codec_for",
    "dump_document",
    "format_for_path",
    "load_data_file",
    "parse_text",
]
