"""Provenance tracking and override reporting.

See PLAN.md "Provenance tracking" and "Conflict reporting".

The tracker records which fragment/operation last modified each document path,
and accumulates per-path contributor history so ``explain`` can show where a
final value came from and which fragments contributed to a list.

It also produces the override warnings emitted when a ``set`` replaces an
existing value (warnings on by default, suppressible via ``--quiet-overrides``).
"""

from __future__ import annotations

from .models import ProvenanceEntry


class ProvenanceTracker:
    """Accumulates provenance and override information during a render.

    Implementation notes:
    - Keep an ordered map ``path -> list[ProvenanceEntry]`` (most recent last).
    - ``record`` is called by :mod:`merge` after each path mutation.
    - ``overrides`` collects human-readable warning strings for ``set``
      operations that replaced an existing value, formatted per PLAN.md
      "Conflict reporting" (previous source vs new source).
    """

    def __init__(self) -> None:
        self.entries: dict[str, list[ProvenanceEntry]] = {}
        self.overrides: list[str] = []
        raise NotImplementedError

    def record(
        self,
        path: str,
        entry: ProvenanceEntry,
        *,
        replaced_existing: bool,
    ) -> None:
        """Record that ``entry`` modified ``path``.

        When ``replaced_existing`` is true and a prior entry exists for the
        path, append a formatted override warning to :attr:`overrides`.
        """
        raise NotImplementedError

    def last_source(self, path: str) -> ProvenanceEntry | None:
        """Return the most recent entry for ``path``, or ``None``."""
        raise NotImplementedError
