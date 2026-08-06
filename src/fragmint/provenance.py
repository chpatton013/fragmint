"""Provenance tracking and override reporting.

See README.md "Provenance and `explain`" and "Conflict reporting".

The tracker records which fragment/operation last modified each document path,
and accumulates per-path contributor history so ``explain`` can show where a
final value came from and which fragments contributed to a list.

It also produces the override warnings emitted when a ``set`` (or a
``merge``'s scalar replacement) replaces an existing value. Warnings are on
by default, suppressible globally via ``--quiet-overrides`` or per-operation
via the fragment's own ``overwrite_ok: true`` (see README.md "Conflict
reporting").
"""

from __future__ import annotations

from .models import ProvenanceEntry


def _describe(entry: ProvenanceEntry) -> str:
    """Render an entry's fragment for an override warning, appending a
    ``(target NAME)`` parenthetical when the entry carries one (aggregate
    scope only — see README.md "Aggregate outputs"). This is what turns an
    aggregate override warning into a genuinely actionable duplicate-target
    report: which two targets wrote the same path."""
    if entry.target is not None:
        return f"{entry.fragment} (target {entry.target})"
    return entry.fragment


class ProvenanceTracker:
    """Accumulates provenance and override information during a render.

    - ``entries`` is an ordered map ``path -> list[ProvenanceEntry]`` (most
      recent last).
    - ``record`` is called by :mod:`merge` after each path mutation.
    - ``overrides`` collects human-readable warning strings for ``set``
      operations (and ``merge`` scalar replacements) that replaced an
      existing value, formatted per README.md "Conflict reporting" (previous
      source vs new source). Suppressed per-call when ``overwrite_ok`` is set.
    """

    def __init__(self) -> None:
        self.entries: dict[str, list[ProvenanceEntry]] = {}
        self.overrides: list[str] = []

    def record(
        self,
        path: str,
        entry: ProvenanceEntry,
        *,
        replaced_existing: bool,
        overwrite_ok: bool = False,
    ) -> None:
        """Record that ``entry`` modified ``path``.

        When ``replaced_existing`` is true and a prior entry exists for the
        path, append a formatted override warning to :attr:`overrides` —
        unless ``overwrite_ok`` is set, in which case the replacement is
        recorded (provenance/``explain`` still show it) but no warning is
        raised.
        """
        history = self.entries.setdefault(path, [])
        if replaced_existing and history and not overwrite_ok:
            previous = history[-1]
            self.overrides.append(
                f"warning: {entry.fragment} operation {entry.operation_index} "
                f"replaced {path}\n"
                f"  previous source: {_describe(previous)}\n"
                f"  new source: {_describe(entry)}"
            )
        history.append(entry)

    def last_source(self, path: str) -> ProvenanceEntry | None:
        """Return the most recent entry for ``path``, or ``None``."""
        history = self.entries.get(path)
        if not history:
            return None
        return history[-1]
