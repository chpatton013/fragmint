"""Centralized YAML loading and deterministic serialization.

All YAML I/O should go through this module so parsing and output formatting
stay consistent. See PLAN.md "YAML serialization".

Loading:
- Parse safely (no arbitrary Python object construction / no unsafe tags).
- Preserve mapping insertion order (native ``dict`` in 3.12 already does; when
  using ruamel round-trip, normalize to plain ``dict``/``list``/scalars before
  returning so downstream code sees the ``YamlValue`` shape from
  :mod:`models`).

Serialization requirements (PLAN.md "YAML serialization"):
- preserve mapping insertion order (do NOT sort keys);
- two-space indentation;
- never emit Python-specific YAML tags;
- Booleans as ``true``/``false``;
- quote strings only when necessary;
- prefer block style for multiline strings;
- end the file with exactly one newline.

The ``#cloud-config`` marker is NOT YAML data; it is prepended by the caller
(:mod:`render`), not by this module. See PLAN.md
"base/autoinstall-base.yaml" note.

``ruamel.yaml`` is recommended for the formatting control above; PyYAML is
acceptable if the determinism requirements are met.
"""

from __future__ import annotations

from pathlib import Path

from .models import YamlValue


def load_file(path: Path) -> YamlValue:
    """Parse a single YAML document from ``path``.

    Raise :class:`~autoinstall_renderer.errors.AutoinstallError` (or the most
    specific applicable subclass at the call site) on parse failure, with the
    file path included in the message.
    """
    raise NotImplementedError


def dump_str(document: YamlValue) -> str:
    """Serialize ``document`` to a deterministic YAML string.

    Does NOT include the ``#cloud-config`` prefix. Must satisfy every
    requirement in the module docstring. Ends with a single trailing newline.
    """
    raise NotImplementedError
