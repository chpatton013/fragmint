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
from typing import Literal

#: A node in a parsed YAML/JSON document.
YamlValue = (
    None
    | bool
    | int
    | float
    | str
    | list["YamlValue"]
    | dict[str, "YamlValue"]
)

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

    command: tuple[VariableSource, ...]
    stdin: VariableSource | None = None
    trim: bool = True


#: A parsed variable value source. See :mod:`sources`.
VariableSource = LiteralSource | SecretSource | CaptureSource


@dataclass(frozen=True)
class SecretStore:
    """Named secrets loaded from the untracked secrets file (``{secrets: {...}}``).

    A flat name -> value store referenced by :class:`SecretSource`. Values must
    never be logged or written to non-output diagnostics. Lookup lives in
    :mod:`sources`. See README.md "Secrets".
    """

    secrets: dict[str, YamlValue] = field(default_factory=dict)


@dataclass(frozen=True)
class Ref:
    """A reference to a fragment, template, or schema file, resolved to the
    document it denotes.

    ``module`` is the resolved document's closure-assigned name, or ``None``
    when the reference resolves to the root document (the inventory) —
    regardless of whether it was *written* bare or qualified. A bare
    reference written inside a module resolves to that module's own tree, so
    its ``module`` here is that module's name, not ``None``; only a
    reference that resolves all the way to the root carries ``None``. See
    README.md "The module model" for the parsing/resolution rules and
    ``modules.display_ref`` for the one function that turns a ``Ref`` back
    into display text.

    ``path`` is the reference's own path component (without a
    module prefix), e.g. ``"host"`` for both a bare ``host`` and a qualified
    ``ansible:host``.
    """

    module: str | None
    path: str


#: Per-output fragment lists, keyed by output name (see README.md "Authoring
#: fragments" and "Fragment order"). Each layer (module defaults/inventory
#: defaults/group/target) declares only the fragments *it* contributes to a
#: given output, as already-resolved :class:`Ref`s; the final list for an
#: output is the positional concatenation across layers.
OutputFragments = dict[str, tuple[Ref, ...]]


@dataclass(frozen=True)
class GroupDefinition:
    """A reusable set of variables and per-output fragments referenced by targets.

    See README.md "Authoring inventory" and "The module model". A group name
    defined by several documents in the closure is merged into one of these —
    variables layer and fragments concatenate, both in closure order — so a
    module may ship a reusable group that the inventory (or another module)
    extends. Group order is significant and must never be reordered.
    ``output_fragments`` maps output name -> this group's ordered, resolved
    fragment contribution to that output.
    """

    name: str
    variables: Variables = field(default_factory=dict)
    output_fragments: OutputFragments = field(default_factory=dict)


@dataclass(frozen=True)
class AggregateFragments:
    """One aggregate output's prologue/epilogue fragment contribution, already
    concatenated across the closure in closure order (README.md "Aggregate
    outputs"). ``prologue`` fragments apply once, before any target
    contributes; ``epilogue`` fragments apply once, after every contributing
    target — both outside the per-target loop, on the same shared document.
    """

    prologue: tuple[Ref, ...] = ()
    epilogue: tuple[Ref, ...] = ()


@dataclass(frozen=True)
class TargetDefinition:
    """A single target as declared in the inventory (the only document kind
    that may define targets — see README.md "The module model").

    A target is any named thing you render a document for (a machine, an
    environment, a service). ``groups`` and each output's fragment list
    preserve declaration order. ``variables`` are the target-level variables
    only (module-defaults/inventory-defaults/group/secret/CLI layering is
    resolved later in :mod:`inventory` / :mod:`render`). ``output_fragments``
    maps output name -> this target's ordered, resolved fragment contribution
    to that output.
    """

    name: str
    groups: tuple[str, ...] = ()
    output_fragments: OutputFragments = field(default_factory=dict)
    variables: Variables = field(default_factory=dict)


@dataclass(frozen=True)
class Project:
    """The whole import closure, flattened.

    See README.md "The module model" and "Composition and ordering".
    Produced by :func:`yaml_frag.modules.flatten` from a
    :class:`~yaml_frag.modules.Closure`: every module's and the inventory's
    ``outputs``/``validators`` unioned (each name defined exactly once across
    the closure), ``defaults`` variables/fragments layered in closure order,
    same-named ``groups`` merged across documents, and the root's ``targets``
    carried through unchanged. This is the single, closure-wide structure
    :class:`~yaml_frag.render.RenderSession` and :func:`inventory.resolve_target`
    operate on — the redesign's replacement for the old, separate project
    config and inventory objects.
    """

    version: int
    outputs: dict[str, OutputSpec] = field(default_factory=dict)
    default_output: str | None = None
    validators: dict[str, ValidatorSpec] = field(default_factory=dict)
    default_variables: Variables = field(default_factory=dict)
    default_output_fragments: OutputFragments = field(default_factory=dict)
    groups: dict[str, GroupDefinition] = field(default_factory=dict)
    targets: dict[str, TargetDefinition] = field(default_factory=dict)
    aggregate_variables: Variables = field(default_factory=dict)
    aggregate_output_fragments: dict[str, AggregateFragments] = field(default_factory=dict)


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

    See README.md "Authoring fragments". ``name`` is the fragment's qualified
    reference (see ``modules.display_ref``) — bare only when it resolves to
    the root document, module-qualified otherwise — used for diagnostics and
    provenance; it is derived from how the fragment was requested, not from
    the file's contents. Fragments may live under any nested path a module or
    the inventory chooses.
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
    ``target`` is ``None`` for per-target rendering (where it would be
    redundant — there's only one target in play) and for an aggregate
    output's prologue/epilogue (which runs once, outside the per-target loop,
    so no single target identifies it either); it is set to the contributing
    target's name only for aggregate-scoped rendering of a target's own
    fragments, where the same fragment is applied once per target and
    "fragment X operation 0" alone does not identify a single write
    (README.md "Aggregate outputs").
    """

    fragment: str
    operation_index: int
    operation: str
    target: str | None = None


@dataclass(frozen=True)
class ResolvedTarget:
    """The fully resolved inputs for one target, prior to rendering.

    Produced by :mod:`inventory`. ``output_fragments`` maps output name -> its
    final ordered fragment reference list (defaults -> groups -> target, per
    README.md "Fragment order"); precedence is purely positional. Only outputs
    with at least one fragment appear here — an output no layer contributed to
    is not produced for this target. ``variables`` is the fully layered
    variable map (defaults -> groups -> target -> secrets -> CLI, per
    README.md "Variable precedence"), shared across all of the target's
    outputs, and always includes the reserved ``target`` key set to ``name``.
    """

    name: str
    groups: tuple[str, ...]
    output_fragments: OutputFragments
    variables: Variables


@dataclass(frozen=True)
class OutputSpec:
    """How one named output file is written. See README.md "Project
    configuration".

    A project declares one or more named outputs (e.g. ``user-data``,
    ``meta-data``); each is entirely independent — its own path, template,
    schema, and validators. ``path`` is a destination pattern containing
    ``{target}`` for ``scope: target`` outputs (the default); an aggregate
    output's ``path`` is a single fixed path and must NOT contain
    ``{target}`` (enforced in :mod:`config`, a config error otherwise).
    ``template`` is an optional text-template file into which the
    serialized YAML is injected (replacing the literal token ``{{ document
    }}``); when ``None`` the serialized YAML is written verbatim. ``schema`` is
    an optional JSON schema the rendered document is validated against.
    ``validators`` names the validators run by default for this output.
    ``default`` marks the output implied by commands like ``--stdout`` when a
    target produces more than one output and no ``--only`` is given (see
    README.md "CLI usage"); an aggregate output may never be ``default: true``
    (also enforced in :mod:`modules`) since ``default`` exists solely to
    disambiguate per-target commands. ``scope`` is ``"target"`` (the default:
    one document per target) or ``"aggregate"`` (one document composed across
    every contributing target; see README.md "Aggregate outputs").
    """

    path: str = "rendered/{target}"
    template: str | None = None
    schema: str | None = None
    validators: tuple[str, ...] = ()
    default: bool = False
    scope: Literal["target", "aggregate"] = "target"


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
class RenderedOutput:
    """The rendered result for a single named output of a target (in memory).

    ``document`` is the final merged data (the root mapping) for this output
    alone. ``provenance`` maps a document path string (e.g.
    ``/autoinstall/kernel/package``) to the list of contributing entries, most
    recent last. ``overrides`` holds any override warnings collected during
    this output's merge (see README.md "Conflict reporting"). Serialization
    and output templating happen in :mod:`render`.
    """

    name: str
    document: dict[str, YamlValue]
    provenance: dict[str, list[ProvenanceEntry]] = field(default_factory=dict)
    overrides: tuple[str, ...] = ()


@dataclass(frozen=True)
class RenderResult:
    """The output of rendering every produced output of a single target.

    ``outputs`` maps output name -> :class:`RenderedOutput`, containing only
    the outputs this target actually produced (README.md "Fragment order":
    an output with no contributing fragments for this target is absent, not
    empty).
    """

    target: str
    outputs: dict[str, RenderedOutput] = field(default_factory=dict)
