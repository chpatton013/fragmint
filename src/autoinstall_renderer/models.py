"""Core data structures shared across the renderer.

These are plain, immutable value objects. They contain no behavior beyond
trivial constructors/accessors; parsing and validation live in
:mod:`inventory` and :mod:`fragments`, and merge/render logic lives in
:mod:`merge` and :mod:`render`. See PLAN.md "Python requirements".

Type conventions
----------------
``YamlValue`` is the recursive type of any parsed-YAML node (mapping, list,
scalar, or ``None``). ``Variables`` is the flat per-machine variable map. Keep
these aliases in one place so every module agrees on the shapes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Union

#: A node in a parsed YAML/JSON document.
YamlValue = Union[
    None,
    bool,
    int,
    float,
    str,
    list["YamlValue"],
    dict[str, "YamlValue"],
]

#: The flat variable map used for template rendering and required-variable checks.
Variables = dict[str, YamlValue]


@dataclass(frozen=True)
class GroupDefinition:
    """A reusable set of variables and fragments referenced by machines.

    See PLAN.md "Inventory format". Group order is significant and must never
    be reordered.
    """

    name: str
    variables: Variables = field(default_factory=dict)
    fragments: tuple[str, ...] = ()


@dataclass(frozen=True)
class MachineDefinition:
    """A single machine as declared in the inventory.

    ``groups`` and ``fragments`` preserve declaration order. ``variables`` are
    the machine-level variables only (defaults/group/CLI/secret layering is
    resolved later in :mod:`inventory` / :mod:`render`).
    """

    name: str
    groups: tuple[str, ...] = ()
    fragments: tuple[str, ...] = ()
    variables: Variables = field(default_factory=dict)


@dataclass(frozen=True)
class Inventory:
    """The fully parsed inventory document.

    See PLAN.md "Inventory format". ``default_variables`` and
    ``default_fragments`` come from the top-level ``defaults`` block.
    """

    version: int
    default_variables: Variables = field(default_factory=dict)
    default_fragments: tuple[str, ...] = ()
    groups: dict[str, GroupDefinition] = field(default_factory=dict)
    machines: dict[str, MachineDefinition] = field(default_factory=dict)


@dataclass(frozen=True)
class FragmentOperation:
    """A single merge instruction within a fragment.

    ``op`` is one of the operations in PLAN.md "Supported merge operations":
    ``set``, ``merge``, ``append``, ``prepend``, ``remove``,
    ``remove-list-items``, ``assert``.

    Optional per-operation fields (only meaningful for some ops):
    - ``deduplicate``: ``append``/``prepend`` — drop duplicates, keep first.
    - ``missing_ok``: ``remove``/``remove-list-items`` — tolerate absence.
    - ``assertion``: for ``assert``, the parsed assertion clause
      (e.g. ``{"equals": 1}``, ``{"exists": True}``, ``{"type": "list"}``).
    """

    op: str
    path: str
    value: YamlValue = None
    deduplicate: bool = False
    missing_ok: bool = False
    assertion: dict[str, YamlValue] = field(default_factory=dict)


@dataclass(frozen=True)
class Fragment:
    """A parsed, schema-valid fragment document.

    See PLAN.md "Fragment format". ``name`` must match the fragment's path
    relative to the fragments directory (minus ``.yaml``); a mismatch is a
    :class:`~autoinstall_renderer.errors.FragmentError`.
    """

    version: int
    name: str
    description: str
    required_variables: tuple[str, ...] = ()
    operations: tuple[FragmentOperation, ...] = ()


@dataclass(frozen=True)
class ProvenanceEntry:
    """Records which fragment/operation last touched a document path.

    See PLAN.md "Provenance tracking". ``operation`` is the ``op`` string;
    ``operation_index`` is the zero-based index within the fragment.
    """

    fragment: str
    operation_index: int
    operation: str


@dataclass(frozen=True)
class ResolvedMachine:
    """The fully resolved inputs for one machine, prior to rendering.

    Produced by :mod:`inventory`. ``fragments`` is the final ordered fragment
    reference list (defaults -> groups -> machine, per PLAN.md "Fragment
    ordering"). ``variables`` is the fully layered variable map (defaults ->
    groups -> machine -> secrets -> CLI, per PLAN.md "Variable precedence").
    """

    name: str
    groups: tuple[str, ...]
    fragments: tuple[str, ...]
    variables: Variables


@dataclass(frozen=True)
class RenderResult:
    """The output of rendering a single machine.

    ``document`` is the final merged data (root mapping, typically containing
    the ``autoinstall`` key). ``provenance`` maps a document path string
    (e.g. ``/autoinstall/kernel/package``) to the list of contributing
    entries, most recent last. ``overrides`` holds any override warnings
    collected during merge (see PLAN.md "Conflict reporting").
    """

    machine: str
    document: dict[str, YamlValue]
    provenance: dict[str, list[ProvenanceEntry]] = field(default_factory=dict)
    overrides: tuple[str, ...] = ()
