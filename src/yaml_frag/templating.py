"""Strict, sandboxed Jinja-style template substitution.

See PLAN.md "Template rendering" and "Security".

Requirements to implement:
- Use a strict undefined (``jinja2.StrictUndefined``) so a missing variable
  raises rather than substituting an empty string.
- Use a sandboxed environment (``jinja2.sandbox.SandboxedEnvironment`` or
  ``NativeEnvironment`` wrapped for safety) so no arbitrary Python execution,
  filesystem, environment, subprocess, or object internals are reachable.
- Preserve native types: when the ENTIRE scalar is a single ``{{ expr }}``,
  the result keeps its Python/YAML type (e.g. Boolean ``false``, not the
  string ``"False"``). Use a native-environment strategy or detect the
  "whole string is one expression" case and coerce accordingly.
- Expose only the safe filters listed in PLAN.md: ``default``, ``lower``,
  ``upper``, ``replace``, ``join``, ``tojson``.
- Rendering applies to already-parsed scalar strings only. Never reparse the
  result as YAML (PLAN.md "Template scope").

Errors must be raised as
:class:`~yaml_frag.errors.TemplateRenderError` and include the target name,
fragment name, operation index, and missing/failed variable name (PLAN.md
"Template rendering", "Error-message tests").
"""

from __future__ import annotations

from .models import Variables, YamlValue


def render_value(
    value: YamlValue,
    variables: Variables,
    *,
    target: str,
    fragment: str,
    operation_index: int,
) -> YamlValue:
    """Recursively render every scalar string within ``value``.

    Walks mappings and lists; renders string leaves through the strict
    environment; leaves non-string scalars untouched. Returns a new structure
    (does not mutate ``value``). The ``target``/``fragment``/``operation_index``
    arguments exist purely to build actionable error messages.
    """
    raise NotImplementedError
