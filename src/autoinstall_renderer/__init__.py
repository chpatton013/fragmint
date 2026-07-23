"""Autoinstall configuration renderer.

Render complete Ubuntu Server autoinstall configurations from an inventory of
machines, an ordered sequence of reusable fragments, per-machine variables, and
explicit fragment merge instructions.

See PLAN.md for the full specification. Public entry points live in
:mod:`autoinstall_renderer.cli` and :mod:`autoinstall_renderer.render`.
"""

from __future__ import annotations

__all__ = ["__version__"]

__version__ = "0.1.0"
