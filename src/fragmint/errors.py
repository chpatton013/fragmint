"""Custom exception hierarchy.

Every user-facing failure should raise one of these exceptions with a message
that contains enough context to find the source file and operation (target
name, fragment name, operation index, path, variable name, as applicable).
See README.md "Design principles" (fail closed).

Each concrete exception declares the :class:`~fragmint.exit_codes.ExitCode`
the CLI should return when it propagates to the top level. The CLI is expected
to catch :class:`FragmintError`, print ``str(exc)`` to stderr, and exit with
``exc.exit_code``.
"""

from __future__ import annotations

from .exit_codes import ExitCode


class FragmintError(Exception):
    """Base class for all fragmint errors.

    Subclasses set :attr:`exit_code`. Message text is the user-facing
    diagnostic and should already include actionable source context.
    """

    exit_code: ExitCode = ExitCode.RENDER_FAILURE


class ModuleError(FragmintError):
    """A module or inventory document is missing, malformed, internally
    inconsistent, or its import closure is invalid (name collision, aliasing,
    a cycle, an undefined output, a containment violation, and the like)."""

    exit_code = ExitCode.MODULE_ERROR


class InventoryError(FragmintError):
    """The inventory file is malformed or internally inconsistent."""

    exit_code = ExitCode.INVENTORY_VALIDATION


class UnknownTargetError(InventoryError):
    """A requested target name does not exist in the inventory."""


class UnknownFragmentError(InventoryError):
    """A referenced fragment name cannot be resolved to a fragment file."""

    exit_code = ExitCode.FRAGMENT_VALIDATION


class FragmentError(FragmintError):
    """A fragment file is malformed or fails fragment-schema validation."""

    exit_code = ExitCode.FRAGMENT_VALIDATION


class AmbiguousFragmentError(FragmentError):
    """An extension-less fragment reference matches more than one file under
    its fragments directory, differing only by format extension (e.g. both
    ``host.yaml`` and ``host.toml``). The reference alone cannot say which
    one was meant, so this fails closed naming the reference and every
    matching file rather than silently preferring one (README.md "Reference
    resolution")."""


class VariableResolutionError(FragmintError):
    """A variable value source could not be resolved.

    Base for secret-lookup and capture-subprocess failures. Messages must name
    the resolution scope (a target, or the aggregate scope) and the variable,
    and must never include resolved secret values or secret-derived command
    arguments. See README.md "Variable value sources".
    """

    exit_code = ExitCode.VARIABLE_RESOLUTION


class SecretNotFoundError(VariableResolutionError):
    """A ``from: secret`` reference names a secret absent from the store."""


class CaptureError(VariableResolutionError):
    """A ``from: capture`` subprocess failed (nonzero exit, timeout, or not
    found). Include the command name and captured stderr, with any
    secret-sourced arguments redacted."""


class TemplateRenderError(FragmintError):
    """Template substitution failed (e.g. a strictly-undefined variable)."""

    exit_code = ExitCode.RENDER_FAILURE


class MergeConflictError(FragmintError):
    """A merge operation encountered an incompatible existing value."""

    exit_code = ExitCode.MERGE_CONFLICT


class AssertionFailedError(FragmintError):
    """A fragment ``assert`` operation did not hold."""

    exit_code = ExitCode.RENDERED_VALIDATION


class ValidationError(FragmintError):
    """The rendered document failed generic validation, schema validation, or a
    configured external validator."""

    exit_code = ExitCode.RENDERED_VALIDATION
