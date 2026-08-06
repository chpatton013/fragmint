"""yaml-frag — a generic YAML fragment composer.

Render structured YAML documents from an inventory of targets, an ordered
sequence of reusable fragments, per-target variables, and explicit path-based
merge operations. The renderer holds no knowledge of any particular document
schema; domain specifics (e.g. Ubuntu autoinstall) live in project
configuration, schemas, validators, and fragments.

See README.md for the full specification. Public entry points live in
:mod:`yaml_frag.cli` and :mod:`yaml_frag.render`.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.1"
