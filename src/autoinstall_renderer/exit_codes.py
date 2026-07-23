"""Stable process exit codes.

These values are part of the tool's contract and are asserted by the CLI tests.
See PLAN.md section "Exit codes". Values may be extended but existing ones must
not be repurposed.
"""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    """Process exit codes returned by the ``autoinstall-render`` executable."""

    SUCCESS = 0
    #: General rendering failure not covered by a more specific code.
    RENDER_FAILURE = 1
    #: Invalid command-line usage (bad flags, arguments, etc.).
    USAGE = 2
    #: Inventory failed validation.
    INVENTORY_VALIDATION = 3
    #: A fragment failed validation.
    FRAGMENT_VALIDATION = 4
    #: A merge operation produced an incompatible-type conflict.
    MERGE_CONFLICT = 5
    #: The rendered document failed structural validation.
    RENDERED_VALIDATION = 6
