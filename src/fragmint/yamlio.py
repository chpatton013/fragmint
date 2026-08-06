"""Centralized YAML loading and deterministic serialization.

All YAML I/O should go through this module so parsing and output formatting
stay consistent. See README.md "YAML serialization".

Loading:
- Parse safely (no arbitrary Python object construction / no unsafe tags).
- Preserve mapping insertion order (native ``dict`` in 3.12 already does; when
  using ruamel round-trip, normalize to plain ``dict``/``list``/scalars before
  returning so downstream code sees the ``YamlValue`` shape from
  :mod:`models`).

Serialization requirements (README.md "YAML serialization"):
- preserve mapping insertion order (do NOT sort keys);
- two-space indentation;
- never emit Python-specific YAML tags;
- Booleans as ``true``/``false``;
- quote a string whenever leaving it bare would change its type for a YAML
  1.1 reader (e.g. cloud-init's PyYAML-based parser); otherwise leave it
  unquoted;
- prefer block style for multiline strings;
- end the file with exactly one newline.

Any header such as ``#cloud-config`` is NOT YAML data; it comes from the
output's text template, applied by :mod:`render`, not by this module. See
README.md "The module model" and "YAML serialization".

``ruamel.yaml`` is recommended for the formatting control above; PyYAML is
acceptable if the determinism requirements are met.
"""

from __future__ import annotations

import io
from functools import cache
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError
from ruamel.yaml.nodes import ScalarNode
from ruamel.yaml.resolver import VersionedResolver
from ruamel.yaml.scalarstring import LiteralScalarString, SingleQuotedScalarString

from .errors import FragmintError
from .models import YamlValue

_load_yaml = YAML(typ="safe")
_load_yaml.allow_duplicate_keys = False

# Drives ruamel's own YAML 1.1 implicit-tag resolver (the core-schema rules a
# YAML 1.1 parser, e.g. cloud-init's PyYAML, applies to a bare plain scalar)
# to find strings that would silently change type if left unquoted. This is
# the same table ruamel uses for its own YAML(version=(1, 1)) loader/dumper;
# it needs no loader/dumper instance to answer a single scalar's tag.
_resolver_1_1 = VersionedResolver(version=(1, 1))

# The YAML 1.1 *spec*'s core schema lists bare `y`/`Y`/`n`/`N` as boolean
# aliases, and ruamel's resolver (above) follows the spec here. PyYAML does
# not: its bool implicit-resolver regex
# (``yaml.resolver.Resolver.add_implicit_resolver`` for
# ``tag:yaml.org,2002:bool``, checked against pyyaml 6.x) lists only
# yes/Yes/YES/no/No/NO/true/True/TRUE/false/False/FALSE/on/On/ON/off/Off/OFF —
# no bare y/Y/n/N. Since cloud-init and most other real-world consumers parse
# with PyYAML, these four letters read back as themselves and do not need
# quoting, even though the spec-faithful resolver above would flag them.
_YAML_1_1_SPEC_PYYAML_DIVERGENCE = frozenset({"y", "Y", "n", "N"})


@cache
def _needs_yaml_1_1_quoting(value: str) -> bool:
    """True if a YAML 1.1 parser would not read ``value`` back as this exact
    string when emitted as a bare (unquoted) plain scalar."""
    if value in _YAML_1_1_SPEC_PYYAML_DIVERGENCE:
        return False
    tag = _resolver_1_1.resolve(ScalarNode, value, (True, False))
    return bool(tag.suffix != "tag:yaml.org,2002:str")


def _normalize(node: Any) -> YamlValue:
    """Recursively convert ruamel/plain containers into plain dict/list/scalars."""
    if isinstance(node, dict):
        return {str(key): _normalize(value) for key, value in node.items()}
    if isinstance(node, list):
        return [_normalize(item) for item in node]
    if node is None or isinstance(node, (bool, int, float, str)):
        return node
    # Fallback: coerce unexpected scalar-ish types (e.g. ruamel's own str
    # subclasses) to plain str.
    return str(node)


def load_file(path: Path) -> YamlValue:
    """Parse a single YAML document from ``path``.

    Raise :class:`~fragmint.errors.FragmintError` (or the most specific
    applicable subclass at the call site) on parse failure, with the file path
    included in the message.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise FragmintError(f"cannot read YAML file {path}: {exc}") from exc
    try:
        data = _load_yaml.load(text)
    except YAMLError as exc:
        raise FragmintError(f"invalid YAML in {path}: {exc}") from exc
    return _normalize(data)


def _to_dumpable(node: YamlValue) -> Any:
    """Convert plain YamlValue into ruamel-friendly structures: block literal
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


def dump_str(document: YamlValue) -> str:
    """Serialize ``document`` to a deterministic YAML string.

    Does NOT include any output-template header. Must satisfy every requirement
    in the module docstring. Ends with a single trailing newline.
    """
    yaml = YAML()
    yaml.default_flow_style = False
    # mapping=2 gives two-space indentation; sequence=4/offset=2 gives list
    # items indented two spaces under their key ("key:\n  - item"), matching
    # README.md's examples. ruamel preserves plain-dict insertion order by
    # default (no key sorting), satisfying the "do not sort keys" rule.
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.width = 2**31 - 1
    yaml.allow_unicode = True

    stream = io.StringIO()
    yaml.dump(_to_dumpable(document), stream)
    text = stream.getvalue()
    return text.rstrip("\n") + "\n"
