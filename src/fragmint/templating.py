"""Strict, sandboxed Jinja-style template substitution.

See README.md "Template rendering".

Properties this module guarantees:
- A missing variable raises rather than substituting an empty string
  (``jinja2.StrictUndefined``).
- No arbitrary Python execution, filesystem, environment, subprocess, or
  object internals are reachable (``jinja2.sandbox.SandboxedEnvironment``).
- Native types survive: when the ENTIRE scalar is a single ``{{ expr }}``, the
  result keeps its Python/YAML type (e.g. Boolean ``false``, not the string
  ``"False"``) — that case is detected and evaluated as an expression rather
  than rendered as text.
- Only the safe filters listed in README.md are exposed: ``default``,
  ``lower``, ``upper``, ``replace``, ``join``, ``tojson``.
- Rendering applies to already-parsed scalar strings only; the result is never
  reparsed as YAML (README.md "Template rendering").

Failures raise :class:`~fragmint.errors.TemplateRenderError`, including the
render scope (a target name, or an aggregate prologue/epilogue label), the
fragment name, operation index, and missing/failed variable name.

:func:`collect_variable_names` performs the same traversal as
:func:`render_value` over the same environment, so the two never disagree
about which strings are templates or what a template references. It never
raises: a malformed template contributes no names there, and its syntax error
still surfaces from :func:`render_value` at the point the fragment is
actually rendered.
"""

from __future__ import annotations

import functools
import re

from jinja2 import StrictUndefined, TemplateError, meta
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


def _context(scope: str, fragment: str, operation_index: int) -> str:
    return f"{scope}: fragment {fragment}, operation {operation_index}"


def _render_string(
    text: str,
    variables: Variables,
    *,
    scope: str,
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
            f"{_context(scope, fragment, operation_index)}: {exc}"
        ) from exc


def render_value(
    value: YamlValue,
    variables: Variables,
    *,
    scope: str,
    fragment: str,
    operation_index: int,
) -> YamlValue:
    """Recursively render every scalar string within ``value``.

    Walks mappings and lists; renders string leaves through the strict
    environment; leaves non-string scalars untouched. Returns a new structure
    (does not mutate ``value``). ``scope`` is an already-formatted, display-
    ready label for where this render is happening (a target name, or an
    aggregate prologue/epilogue label); it, ``fragment``, and
    ``operation_index`` exist purely to build actionable error messages.
    """
    if isinstance(value, dict):
        return {
            key: render_value(
                item,
                variables,
                scope=scope,
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
                scope=scope,
                fragment=fragment,
                operation_index=operation_index,
            )
            for item in value
        ]
    if isinstance(value, str):
        return _render_string(
            value,
            variables,
            scope=scope,
            fragment=fragment,
            operation_index=operation_index,
        )
    return value


@functools.lru_cache(maxsize=4096)
def _names_in_string(text: str) -> tuple[str, ...]:
    """Every variable name a single template string references, sorted (the
    names come from a ``set``, whose iteration order is not deterministic
    across ``PYTHONHASHSEED``, so :func:`collect_variable_names` needs a
    stable per-string order to build on). Cheaply short-circuits plain text,
    and a malformed template yields no names rather than raising — the error
    still surfaces from :func:`render_value`/:func:`_render_string`. Cached
    because a fragment shared across many targets is analyzed once."""
    if "{{" not in text and "{%" not in text:
        return ()
    try:
        parsed = _ENV.parse(text)
    except TemplateError:
        return ()
    return tuple(sorted(meta.find_undeclared_variables(parsed)))


def _collect_names(value: YamlValue) -> tuple[str, ...]:
    if isinstance(value, dict):
        names: list[str] = []
        for item in value.values():
            names.extend(_collect_names(item))
        return tuple(names)
    if isinstance(value, list):
        names = []
        for item in value:
            names.extend(_collect_names(item))
        return tuple(names)
    if isinstance(value, str):
        return _names_in_string(value)
    return ()


def collect_variable_names(value: YamlValue) -> tuple[str, ...]:
    """Every variable name a template in ``value`` references, in a
    deterministic, deduplicated, first-appearance order.

    Walks mappings/lists/strings exactly as :func:`render_value` does. Reports
    a name referenced anywhere in ``value``, including inside a branch that
    is never taken at render time (README.md "Resolution timing": whether a
    variable is *demanded* is a function of the documents on disk, not of
    which branch a render happens to take). Variable VALUES are never
    templated in this tool, so only fragment operations should be passed
    here — never a raw variable value.
    """
    seen: dict[str, None] = {}
    for name in _collect_names(value):
        seen.setdefault(name, None)
    return tuple(seen)
