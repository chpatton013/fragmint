"""fragmint — a generic YAML fragment composer.

Render structured YAML documents from an inventory of targets, an ordered
sequence of reusable fragments, per-target variables, and explicit path-based
merge operations. The renderer holds no knowledge of any particular document
schema; domain specifics (e.g. Ubuntu autoinstall) live in project
configuration, schemas, validators, and fragments.

See README.md for the full specification. Public entry points live in
:mod:`fragmint.cli` and :mod:`fragmint.render`.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.1"
