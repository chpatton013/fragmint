"""Custom exception hierarchy.

Every user-facing failure should raise one of these exceptions with a message
that contains enough context to find the source file and operation (machine
name, fragment name, operation index, path, variable name, as applicable).
See PLAN.md sections "Fail closed", "Python requirements", and
"Error-message tests".

Each concrete exception declares the :class:`~autoinstall_renderer.exit_codes.ExitCode`
the CLI should return when it propagates to the top level. The CLI is expected
to catch :class:`AutoinstallError`, print ``str(exc)`` to stderr, and exit with
``exc.exit_code``.
"""

from __future__ import annotations

from .exit_codes import ExitCode


class AutoinstallError(Exception):
    """Base class for all renderer errors.

    Subclasses set :attr:`exit_code`. Message text is the user-facing
    diagnostic and should already include actionable source context.
    """

    exit_code: ExitCode = ExitCode.RENDER_FAILURE


class InventoryError(AutoinstallError):
    """The inventory file is malformed or internally inconsistent."""

    exit_code = ExitCode.INVENTORY_VALIDATION


class UnknownMachineError(InventoryError):
    """A requested machine name does not exist in the inventory."""


class UnknownFragmentError(InventoryError):
    """A referenced fragment name cannot be resolved to a fragment file."""

    exit_code = ExitCode.FRAGMENT_VALIDATION


class FragmentError(AutoinstallError):
    """A fragment file is malformed or fails fragment-schema validation."""

    exit_code = ExitCode.FRAGMENT_VALIDATION


class TemplateRenderError(AutoinstallError):
    """Template substitution failed (e.g. a strictly-undefined variable)."""

    exit_code = ExitCode.RENDER_FAILURE


class MergeConflictError(AutoinstallError):
    """A merge operation encountered an incompatible existing value."""

    exit_code = ExitCode.MERGE_CONFLICT


class AssertionFailedError(AutoinstallError):
    """A fragment ``assert`` operation did not hold."""

    exit_code = ExitCode.RENDER_FAILURE


class ValidationError(AutoinstallError):
    """The rendered document failed structural validation."""

    exit_code = ExitCode.RENDERED_VALIDATION
