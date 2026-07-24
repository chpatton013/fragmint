"""Strict, sandboxed Jinja-style template substitution.

See README.md "Template rendering".

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
- Expose only the safe filters listed in README.md: ``default``, ``lower``,
  ``upper``, ``replace``, ``join``, ``tojson``.
- Rendering applies to already-parsed scalar strings only. Never reparse the
  result as YAML (README.md "Template rendering").

Errors must be raised as
:class:`~yaml_frag.errors.TemplateRenderError` and include the target name,
fragment name, operation index, and missing/failed variable name.
"""

from __future__ import annotations

import re

from jinja2 import StrictUndefined, TemplateError
from jinja2.runtime import Undefined
from jinja2.sandbox import SandboxedEnvironment

from .errors import TemplateRenderError
from .models import Variables, YamlValue

#: The only filters exposed to fragment templates (README.md "Template rendering").
_ALLOWED_FILTERS = {"default", "lower", "upper", "replace", "join", "tojson"}

#: Matches a scalar string consisting of exactly one ``{{ expr }}`` template
#: expression and nothing else (no surrounding text, no other tags).
_WHOLE_EXPRESSION_RE = re.compile(r"\A\{\{\-?\s*(?P<expr>.*?)\s*\-?\}\}\Z", re.DOTALL)


def _build_environment() -> SandboxedEnvironment:
    # keep_trailing_newline=True so a block scalar's trailing newline survives
    # rendering (e.g. a sudoers file's final newline). Jinja strips it by
    # default, which would silently change file content.
    env = SandboxedEnvironment(undefined=StrictUndefined, keep_trailing_newline=True)
    env.filters = {
        name: func for name, func in env.filters.items() if name in _ALLOWED_FILTERS
    }
    return env


_ENV = _build_environment()


def _context(target: str, fragment: str, operation_index: int) -> str:
    return f"{target}: fragment {fragment}, operation {operation_index}"


def _render_string(
    text: str,
    variables: Variables,
    *,
    target: str,
    fragment: str,
    operation_index: int,
) -> YamlValue:
    match = _WHOLE_EXPRESSION_RE.fullmatch(text)
    try:
        if match is not None and "{{" not in match.group("expr"):
            compiled = _ENV.compile_expression(match.group("expr"), undefined_to_none=False)
            result = compiled(**variables)
            if isinstance(result, Undefined):
                # Force StrictUndefined to raise its UndefinedError.
                str(result)
            return result
        template = _ENV.from_string(text)
        return template.render(**variables)
    except TemplateError as exc:
        raise TemplateRenderError(
            f"{_context(target, fragment, operation_index)}: {exc}"
        ) from exc


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
    if isinstance(value, dict):
        return {
            key: render_value(
                item,
                variables,
                target=target,
                fragment=fragment,
                operation_index=operation_index,
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            render_value(
                item,
                variables,
                target=target,
                fragment=fragment,
                operation_index=operation_index,
            )
            for item in value
        ]
    if isinstance(value, str):
        return _render_string(
            value,
            variables,
            target=target,
            fragment=fragment,
            operation_index=operation_index,
        )
    return value
