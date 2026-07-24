"""Core data structures shared across the renderer.

These are plain, immutable value objects. They contain no behavior beyond
trivial constructors/accessors; parsing and validation live in :mod:`inventory`,
:mod:`fragments`, and :mod:`config`, and merge/render logic lives in :mod:`merge`
and :mod:`render`.

Type conventions
----------------
``YamlValue`` is the recursive type of any parsed-YAML node (mapping, list,
scalar, or ``None``). ``Variables`` is the flat per-target variable map. Keep
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

#: The flat variable map used for template rendering and required-variable
#: checks. After resolution these are concrete :data:`YamlValue`s; before
#: resolution a value may instead be an untagged literal or a ``from:`` source
#: mapping (see :mod:`sources`).
Variables = dict[str, YamlValue]


@dataclass(frozen=True)
class LiteralSource:
    """A value used verbatim. The default when a variable has no ``from:`` tag,
    and also the explicit escape hatch ``{from: literal, value: ...}`` for a
    literal mapping that would otherwise look like a source. See README.md
    "Variable value sources"."""

    value: YamlValue


@dataclass(frozen=True)
class SecretSource:
    """``{from: secret, name: NAME}`` — resolved from the :class:`SecretStore`."""

    name: str


@dataclass(frozen=True)
class CaptureSource:
    """``{from: capture, command: [...], stdin: <source?>, trim: bool}``.

    Runs a subprocess (argv list, never a shell) and uses its stdout. Each
    element of ``command`` and the optional ``stdin`` are themselves sources
    (literal or secret), so arguments/stdin can come from literals or secrets.
    ``trim`` strips a single trailing newline (default ``True``).
    """

    command: tuple["VariableSource", ...]
    stdin: "VariableSource | None" = None
    trim: bool = True


#: A parsed variable value source. See :mod:`sources`.
VariableSource = Union[LiteralSource, SecretSource, CaptureSource]


@dataclass(frozen=True)
class SecretStore:
    """Named secrets loaded from the untracked secrets file (``{secrets: {...}}``).

    A flat name -> value store referenced by :class:`SecretSource`. Values must
    never be logged or written to non-output diagnostics. Lookup lives in
    :mod:`sources`. See README.md "Secrets".
    """

    secrets: dict[str, YamlValue] = field(default_factory=dict)


@dataclass(frozen=True)
class GroupDefinition:
    """A reusable set of variables and fragments referenced by targets.

    See README.md "Authoring inventory". Group order is significant and must
    never be reordered.
    """

    name: str
    variables: Variables = field(default_factory=dict)
    fragments: tuple[str, ...] = ()


@dataclass(frozen=True)
class TargetDefinition:
    """A single target as declared in the inventory.

    A target is any named thing you render a document for (a machine, an
    environment, a service). ``groups`` and ``fragments`` preserve declaration
    order. ``variables`` are the target-level variables only (defaults/group/
    secret/CLI layering is resolved later in :mod:`inventory` / :mod:`render`).
    """

    name: str
    groups: tuple[str, ...] = ()
    fragments: tuple[str, ...] = ()
    variables: Variables = field(default_factory=dict)


@dataclass(frozen=True)
class Inventory:
    """The fully parsed inventory document.

    See README.md "Authoring inventory". ``default_variables`` and
    ``default_fragments`` come from the top-level ``defaults`` block.
    """

    version: int
    default_variables: Variables = field(default_factory=dict)
    default_fragments: tuple[str, ...] = ()
    groups: dict[str, GroupDefinition] = field(default_factory=dict)
    targets: dict[str, TargetDefinition] = field(default_factory=dict)


@dataclass(frozen=True)
class FragmentOperation:
    """A single merge instruction within a fragment.

    ``op`` is one of the operations in README.md "Merge operations":
    ``set``, ``merge``, ``append``, ``prepend``, ``remove``,
    ``remove-list-items``, ``assert``.

    Optional per-operation fields (only meaningful for some ops):
    - ``deduplicate``: ``append``/``prepend`` — drop duplicates, keep first.
    - ``missing_ok``: ``remove``/``remove-list-items`` — tolerate absence.
    - ``overwrite_ok``: ``set``/``merge`` — declare that replacing an existing
      value at this path is intentional, suppressing the per-operation
      override warning (see README.md "Conflict reporting").
    - ``assertion``: for ``assert``, the parsed assertion clause
      (e.g. ``{"equals": 1}``, ``{"exists": True}``, ``{"type": "list"}``).
    """

    op: str
    path: str
    value: YamlValue = None
    deduplicate: bool = False
    missing_ok: bool = False
    overwrite_ok: bool = False
    assertion: dict[str, YamlValue] = field(default_factory=dict)


@dataclass(frozen=True)
class Fragment:
    """A parsed, schema-valid fragment document.

    See README.md "Authoring fragments". ``name`` is the fragment's reference
    path relative to the fragments directory (minus ``.yaml``), used for
    diagnostics and provenance; it is derived from how the fragment was
    requested, not from the file's contents. Fragments may live under any
    nested path the project chooses.
    """

    version: int
    name: str
    description: str
    required_variables: tuple[str, ...] = ()
    operations: tuple[FragmentOperation, ...] = ()


@dataclass(frozen=True)
class ProvenanceEntry:
    """Records which fragment/operation last touched a document path.

    See README.md "Provenance and `explain`". ``operation`` is the ``op`` string;
    ``operation_index`` is the zero-based index within the fragment.
    """

    fragment: str
    operation_index: int
    operation: str


@dataclass(frozen=True)
class ResolvedTarget:
    """The fully resolved inputs for one target, prior to rendering.

    Produced by :mod:`inventory`. ``fragments`` is the final ordered fragment
    reference list (defaults -> groups -> target, per README.md "Fragment
    order"); precedence is purely positional. ``variables`` is the fully
    layered variable map (defaults -> groups -> target -> secrets -> CLI, per
    README.md "Variable precedence").
    """

    name: str
    groups: tuple[str, ...]
    fragments: tuple[str, ...]
    variables: Variables


@dataclass(frozen=True)
class OutputSpec:
    """How a target's rendered document is written. See README.md
    "Project configuration".

    ``path`` is a destination pattern containing ``{target}``. ``template`` is
    an optional text-template file into which the serialized YAML is injected
    (replacing the literal token ``{{ document }}``); when ``None`` the
    serialized YAML is written verbatim. ``schema`` is an optional JSON schema
    the rendered document is validated against. ``validators`` names the
    validators run by default for this output.
    """

    path: str = "rendered/{target}"
    template: str | None = None
    schema: str | None = None
    validators: tuple[str, ...] = ()


@dataclass(frozen=True)
class ValidatorSpec:
    """A named external validator. See README.md "Validation" (named
    validators).

    ``command`` is run against the written output file; a nonzero exit is a
    failure.
    """

    name: str
    command: tuple[str, ...]


@dataclass(frozen=True)
class ProjectConfig:
    """The parsed project-configuration file (``yaml-frag.yaml``).

    See README.md "Project configuration". This is where domain-specific behavior
    (output template/header, path layout, validators, document schema) lives —
    never in the renderer code.
    """

    version: int
    inventory: str
    fragments_dir: str
    output: OutputSpec
    validators: dict[str, ValidatorSpec] = field(default_factory=dict)


@dataclass(frozen=True)
class RenderResult:
    """The output of rendering a single target (in memory).

    ``document`` is the final merged data (the root mapping). ``provenance``
    maps a document path string (e.g. ``/autoinstall/kernel/package``) to the
    list of contributing entries, most recent last. ``overrides`` holds any
    override warnings collected during merge (see README.md "Conflict reporting").
    Serialization and output templating happen in :mod:`render`.
    """

    target: str
    document: dict[str, YamlValue]
    provenance: dict[str, list[ProvenanceEntry]] = field(default_factory=dict)
    overrides: tuple[str, ...] = ()
