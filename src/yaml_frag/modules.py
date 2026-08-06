"""Document loading, the import closure, and reference resolution.

See README.md "The module model" and "Composition and ordering". This module
owns the whole input-loading path: parsing a single document (module or
inventory — they share one shape, see ``schemas/document.schema.json``),
walking the ``imports`` graph into a :class:`Closure`, resolving
fragment/template/schema references through it, and flattening the whole
closure into a single :class:`~yaml_frag.models.Project` that :mod:`render`
renders from.

Two kinds of document
----------------------
A **module** (``yaml-frag.yaml``) may declare input directories, ``imports``,
``outputs``, ``defaults`` (variables + per-output fragments), and ``groups``.
The **inventory** is the same shape plus ``targets``, and is the *root* of the
import graph — the only document a module may not itself reference. See
README.md "The module model" for the full document shape.

The closure
-----------
``imports`` maps a local name to a module directory, resolved relative to the
importing document. Importing a module transitively imports its own imports,
and every module anywhere in the closure is addressable by the name it was
first bound to — one flat namespace. :func:`load_closure` walks this graph
**post-order, depth-first, in declaration order**, visiting each module once
at first encounter (a diamond contributes exactly once), with the inventory
contributing last. It fails closed on a name bound to two different
directories (collision), one directory bound to two different names
(aliasing), or an import cycle — naming both offenders / the cycle path in
every case.

Reference resolution
---------------------
A fragment/template/schema reference is a single rule: a value containing
``:`` is module-qualified (``ns:path``); a value without one is bare and
resolves against the *declaring* document's own directory of that kind. See
:func:`resolve_ref`, :func:`display_ref`, and the per-kind path resolvers.
After :meth:`Path.resolve`, a resolved path must remain inside the module
directory it resolved through (README.md finding 2) — checked by every path
resolver here, never left to the caller.

Flattening
----------
:func:`flatten` turns a loaded :class:`Closure` into one
:class:`~yaml_frag.models.Project`: outputs and validators unioned by name
(each defined exactly once across the closure), ``defaults`` variables and
fragments layered in closure order, same-named ``groups`` merged across
documents, the inventory's ``targets`` carried through with their fragment
references resolved, and every document's ``aggregate:`` block flattened the
same way ``defaults`` is — ``aggregate.variables`` layered in closure order,
each aggregate output's ``prologue``/``epilogue`` extended independently, also
in closure order (:func:`_flatten_aggregate`; README.md "Aggregate outputs").
An ``aggregate.outputs`` entry naming an output undefined anywhere in the
closure, or one whose ``scope`` is ``"target"``, fails closed naming the
declaring document. This is kept as a separate step from graph traversal
specifically so composition/merge rules can be unit-tested against a
synthetic :class:`Closure` without loading real files (see
``tests/test_modules.py``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

import jsonschema

from .errors import ModuleError
from .models import (
    AggregateFragments,
    GroupDefinition,
    OutputFragments,
    OutputSpec,
    Project,
    Ref,
    TargetDefinition,
    ValidatorSpec,
    Variables,
)
from .yamlio import load_file

#: The only document version supported by this release.
SUPPORTED_DOCUMENT_VERSION = 1

#: The inventory's default filename when ``--inventory`` names a directory
#: (or is omitted, defaulting to ``.``). See README.md "The module model".
INVENTORY_FILENAME = "targets.yaml"

#: A module's document filename.
MODULE_FILENAME = "yaml-frag.yaml"

#: The secrets overlay's default filename, looked for beside the inventory when
#: ``--secrets`` is omitted. See README.md "Secrets".
SECRETS_FILENAME = "secrets.yaml"

#: Conventional, existence-conditional subdirectory names inferred when the
#: corresponding ``*_dir`` key is unset. See README.md "Directory inference".
_INFERRED_SUBDIRS: dict[str, str] = {
    "fragments": "fragments",
    "templates": "templates",
    "schemas": "schemas",
}

#: Reference kinds, each with its own directory attribute on :class:`Document`
#: and (for fragments only) an implicit ``.yaml`` suffix.
RefKind = Literal["fragments", "templates", "schemas"]

#: Module names are restricted to a simple identifier-like charset so a
#: qualified reference's first ``:`` is unambiguous and the name is safe to
#: print in diagnostics.
_MODULE_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")

#: Characters a target name must not contain because they would corrupt a
#: JSON Pointer once substituted into an operation path (README.md
#: "Aggregate outputs" and "Paths": `/` separates pointer tokens and `~`
#: begins an escape sequence). Rejected at load time, per-target, rather than
#: escaped on substitution, for a clearer error at the clearer point of fault.
POINTER_HOSTILE_CHARACTERS = ("/", "~")


def _schema_path(name: str) -> Path:
    """Resolve a packaged tool-format schema under ``yaml_frag/schemas/``."""
    return Path(__file__).resolve().parent / "schemas" / name


# --------------------------------------------------------------------------
# Ref parsing and display
# --------------------------------------------------------------------------


def parse_ref(raw: str) -> tuple[str | None, str]:
    """Split a written reference on its first ``:`` only.

    Returns ``(module, path)`` where ``module`` is ``None`` for a bare
    reference. Raises :class:`ModuleError` if a qualified reference has an
    empty module or path half, or the module half fails the identifier-like
    charset check in :data:`_MODULE_NAME_RE`.
    """
    if ":" not in raw:
        if not raw:
            raise ModuleError("empty reference")
        return None, raw
    module, _, path = raw.partition(":")
    if not module or not path:
        raise ModuleError(
            f"malformed reference {raw!r}: both the module and path half of a "
            f"qualified reference must be non-empty"
        )
    if not _MODULE_NAME_RE.match(module):
        raise ModuleError(
            f"malformed reference {raw!r}: module name {module!r} must match "
            f"{_MODULE_NAME_RE.pattern!r}"
        )
    return module, path


def display_ref(ref: Ref) -> str:
    """The one function every display site routes through (README.md "The
    module model"): bare when ``ref.module is None`` (the ref resolves to the
    root document), module-qualified otherwise — even if the reference was
    originally *written* bare inside that module. Never format with an empty
    module string; that would render a leading ``:`` for a root-resolved ref.
    """
    if ref.module is None:
        return ref.path
    return f"{ref.module}:{ref.path}"


# --------------------------------------------------------------------------
# Per-document raw parse (no closure knowledge yet)
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class _RawOutput:
    """One document's own ``outputs.<name>`` entry, prior to closure-wide
    resolution. ``path`` is already anchored to the declaring document's
    directory (it is not a reference, just a document-relative path, exactly
    like today's project-config paths); ``template``/``schema`` are left as
    written (bare or qualified) since resolving them may require the whole
    closure (a qualified reference into another module)."""

    path: str
    template: str | None
    schema: str | None
    validators: tuple[str, ...]
    default: bool
    scope: Literal["target", "aggregate"]


@dataclass(frozen=True)
class _RawGroup:
    variables: Variables = field(default_factory=dict)
    output_fragments: dict[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class _RawTarget:
    groups: tuple[str, ...] = ()
    output_fragments: dict[str, tuple[str, ...]] = field(default_factory=dict)
    variables: Variables = field(default_factory=dict)


@dataclass(frozen=True)
class _RawAggregate:
    """One document's own ``aggregate.outputs.<name>`` entry, prior to
    closure-wide resolution. See README.md "Aggregate outputs"."""

    prologue: tuple[str, ...] = ()
    epilogue: tuple[str, ...] = ()


@dataclass(frozen=True)
class Document:
    """A loaded module or the inventory (the root), before closure-wide
    reference resolution.

    ``path``/``root`` are absolute. ``name`` is the closure-assigned name —
    the name this document was first imported under — and is ``None`` only
    for the root. ``fragments_dir``/``templates_dir``/``schemas_dir`` are
    ``None`` when this document provides no directory of that kind (an unset,
    inferred directory that doesn't exist on disk; see README.md "Directory
    inference"). ``imports`` maps this document's own local names to the
    already-resolved (``Path.resolve()``-ed) directory each names.
    """

    path: Path
    root: Path
    name: str | None
    fragments_dir: Path | None
    templates_dir: Path | None
    schemas_dir: Path | None
    imports: dict[str, Path]
    outputs: dict[str, _RawOutput]
    validators: dict[str, ValidatorSpec]
    default_variables: Variables
    default_output_fragments: dict[str, tuple[str, ...]]
    groups: dict[str, _RawGroup]
    targets: dict[str, _RawTarget]
    aggregate_variables: Variables
    aggregate_output_fragments: dict[str, _RawAggregate]

    @property
    def is_root(self) -> bool:
        return self.name is None

    def dir_for(self, kind: RefKind) -> Path | None:
        return {
            "fragments": self.fragments_dir,
            "templates": self.templates_dir,
            "schemas": self.schemas_dir,
        }[kind]


def _resolve_dir(doc_dir: Path, declared: Any, kind: RefKind) -> Path | None:
    """Resolve one of a document's ``*_dir`` settings.

    An explicitly declared directory that doesn't exist is an error
    (declaring it is a claim); an *inferred* one (unset, defaulting to
    ``./<kind>/``) that doesn't exist simply means this document provides no
    directory of that kind. See README.md "Directory inference".
    """
    if declared is not None:
        candidate = (doc_dir / cast(str, declared)).resolve()
        if not candidate.is_dir():
            raise ModuleError(
                f"declared {kind}_dir {declared!r} not found (expected a "
                f"directory at {candidate})"
            )
        return candidate
    inferred = (doc_dir / _INFERRED_SUBDIRS[kind]).resolve()
    return inferred if inferred.is_dir() else None


def _parse_output_fragments(raw: dict[str, Any] | None) -> dict[str, tuple[str, ...]]:
    result: dict[str, tuple[str, ...]] = {}
    for output_name, output_raw in (raw or {}).items():
        output_raw = output_raw or {}
        result[output_name] = tuple(output_raw.get("fragments", []) or [])
    return result


def _parse_aggregate(raw: dict[str, Any] | None) -> tuple[Variables, dict[str, _RawAggregate]]:
    """Parse a document's own ``aggregate:`` block into its variables and its
    per-output prologue/epilogue contribution. See README.md "Aggregate
    outputs"."""
    raw = raw or {}
    variables: Variables = dict(raw.get("variables", {}) or {})
    output_fragments: dict[str, _RawAggregate] = {}
    for output_name, output_raw in (raw.get("outputs", {}) or {}).items():
        output_raw = output_raw or {}
        output_fragments[output_name] = _RawAggregate(
            prologue=tuple(output_raw.get("prologue", []) or []),
            epilogue=tuple(output_raw.get("epilogue", []) or []),
        )
    return variables, output_fragments


def _parse_output(doc_dir: Path, name: str, raw: dict[str, Any]) -> _RawOutput:
    path = cast(str, raw["path"])
    scope_raw = raw.get("scope", "target")
    if scope_raw not in ("target", "aggregate"):
        raise ModuleError(f"output {name!r}: scope must be 'target' or 'aggregate', got {scope_raw!r}")
    scope = cast(Literal["target", "aggregate"], scope_raw)
    default_flag = bool(raw.get("default", False))

    if scope == "aggregate":
        if "{target}" in path:
            raise ModuleError(
                f"output {name!r} has scope 'aggregate' but its path contains "
                f"'{{target}}'; an aggregate output is not per-target"
            )
        if default_flag:
            raise ModuleError(
                f"output {name!r} has scope 'aggregate' and cannot also be "
                f"marked default: true"
            )

    return _RawOutput(
        path=str(doc_dir / path),
        template=raw.get("template"),
        schema=raw.get("schema"),
        validators=tuple(raw.get("validators", []) or []),
        default=default_flag,
        scope=scope,
    )


def _load_document(doc_path: Path, *, name: str | None) -> Document:
    """Parse and structurally validate a single module/inventory document at
    ``doc_path``. Does not touch ``imports`` or references beyond parsing
    their raw strings — that needs the whole closure (see :func:`load_closure`
    / :func:`resolve_ref`)."""
    if not doc_path.is_file():
        kind = "inventory" if name is None else f"module {name!r}"
        raise ModuleError(f"{kind} document not found: {doc_path}")

    try:
        raw = load_file(doc_path)
    except Exception as exc:
        raise ModuleError(f"cannot parse document {doc_path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ModuleError(f"document {doc_path} must be a mapping")

    schema: dict[str, Any] = cast(dict[str, Any], load_file(_schema_path("document.schema.json")))
    try:
        jsonschema.validate(instance=raw, schema=schema)
    except jsonschema.ValidationError as exc:
        raise ModuleError(
            f"document {doc_path} failed schema validation: {exc.message} "
            f"(at {'/'.join(str(part) for part in exc.path)})"
        ) from exc

    doc = cast(dict[str, Any], raw)

    version = doc.get("version")
    if version != SUPPORTED_DOCUMENT_VERSION:
        raise ModuleError(
            f"document {doc_path}: unsupported version {version!r}, expected "
            f"{SUPPORTED_DOCUMENT_VERSION}"
        )

    doc_dir = doc_path.resolve().parent
    is_root = name is None

    if not is_root and doc.get("targets"):
        raise ModuleError(
            f"module {doc_path}: modules must not define `targets` — only the "
            f"inventory (the root document) may"
        )
    if is_root and not doc.get("targets"):
        raise ModuleError(f"inventory {doc_path}: `targets` is required and must be non-empty")

    imports: dict[str, Path] = {
        local_name: (doc_dir / rel).resolve()
        for local_name, rel in (doc.get("imports", {}) or {}).items()
    }

    outputs_raw = doc.get("outputs", {}) or {}
    outputs = {name: _parse_output(doc_dir, name, spec) for name, spec in outputs_raw.items()}

    validators: dict[str, ValidatorSpec] = {}
    for vname, vspec in (doc.get("validators", {}) or {}).items():
        validators[vname] = ValidatorSpec(name=vname, command=tuple(vspec["command"]))

    for output_name, output in outputs.items():
        for validator_name in output.validators:
            if validator_name not in validators:
                raise ModuleError(
                    f"document {doc_path}: output {output_name!r} default validator "
                    f"{validator_name!r} is not declared in this document's `validators`"
                )

    defaults_raw = doc.get("defaults", {}) or {}
    default_variables: Variables = dict(defaults_raw.get("variables", {}) or {})
    default_output_fragments = _parse_output_fragments(defaults_raw.get("outputs"))

    aggregate_variables, aggregate_output_fragments = _parse_aggregate(doc.get("aggregate"))

    groups: dict[str, _RawGroup] = {}
    for gname, graw in (doc.get("groups", {}) or {}).items():
        graw = graw or {}
        groups[gname] = _RawGroup(
            variables=dict(graw.get("variables", {}) or {}),
            output_fragments=_parse_output_fragments(graw.get("outputs")),
        )

    targets: dict[str, _RawTarget] = {}
    if is_root:
        for tname, traw in (doc.get("targets", {}) or {}).items():
            if any(char in tname for char in POINTER_HOSTILE_CHARACTERS):
                raise ModuleError(
                    f"inventory {doc_path}: target name {tname!r} must not contain "
                    f"{' or '.join(repr(c) for c in POINTER_HOSTILE_CHARACTERS)}"
                )
            traw = traw or {}
            target_groups = tuple(traw.get("groups", []) or [])
            # A referenced group may be defined by an imported module rather
            # than by this document, so existence is checked only once the
            # whole closure's groups are known — see flatten().
            targets[tname] = _RawTarget(
                groups=target_groups,
                output_fragments=_parse_output_fragments(traw.get("outputs")),
                variables=dict(traw.get("variables", {}) or {}),
            )

    return Document(
        path=doc_path.resolve(),
        root=doc_dir,
        name=name,
        fragments_dir=_resolve_dir(doc_dir, doc.get("fragments_dir"), "fragments"),
        templates_dir=_resolve_dir(doc_dir, doc.get("templates_dir"), "templates"),
        schemas_dir=_resolve_dir(doc_dir, doc.get("schemas_dir"), "schemas"),
        imports=imports,
        outputs=outputs,
        validators=validators,
        default_variables=default_variables,
        default_output_fragments=default_output_fragments,
        groups=groups,
        targets=targets,
        aggregate_variables=aggregate_variables,
        aggregate_output_fragments=aggregate_output_fragments,
    )


# --------------------------------------------------------------------------
# The closure
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Closure:
    """The resolved import DAG: documents in closure order (post-order,
    depth-first, declaration order; the root last), plus the name index.

    ``by_name`` covers every module in the closure (never the root, which has
    no name).
    """

    documents: tuple[Document, ...]
    by_name: dict[str, Document]

    @property
    def root(self) -> Document:
        return self.documents[-1]


def load_closure(root_path: Path) -> Closure:
    """Load the inventory at ``root_path`` and its whole transitive import
    closure.

    Raises :class:`ModuleError` for a name collision (one name bound to two
    different directories), aliasing (one directory bound to two different
    names), or an import cycle — each naming both offenders / the cycle path.
    See README.md "The module model" and "Composition and ordering".
    """
    root_doc = _load_document(root_path, name=None)

    documents: list[Document] = []
    finished: dict[Path, Document] = {}
    name_to_dir: dict[str, Path] = {}
    dir_to_name: dict[Path, str] = {}

    def visit(doc: Document, stack: tuple[Path, ...]) -> None:
        for local_name, module_dir in doc.imports.items():
            if module_dir in stack:
                cycle = [*stack[stack.index(module_dir) :], module_dir]
                raise ModuleError(
                    f"import cycle: {' -> '.join(str(p) for p in cycle)}"
                )

            existing_dir = name_to_dir.get(local_name)
            if existing_dir is not None and existing_dir != module_dir:
                raise ModuleError(
                    f"module name collision: {local_name!r} is bound to both "
                    f"{existing_dir} and {module_dir}"
                )
            existing_name = dir_to_name.get(module_dir)
            if existing_name is not None and existing_name != local_name:
                raise ModuleError(
                    f"module aliasing: {module_dir} is bound to both "
                    f"{existing_name!r} and {local_name!r} — importers must agree "
                    f"on one name for the same module"
                )
            name_to_dir[local_name] = module_dir
            dir_to_name[module_dir] = local_name

            if module_dir in finished:
                continue  # diamond: already loaded and contributed once.

            child_path = module_dir / MODULE_FILENAME
            child_doc = _load_document(child_path, name=local_name)
            visit(child_doc, (*stack, module_dir))
            finished[module_dir] = child_doc
            documents.append(child_doc)

    visit(root_doc, (root_doc.root,))
    documents.append(root_doc)

    by_name = {doc.name: doc for doc in documents if doc.name is not None}
    return Closure(documents=tuple(documents), by_name=by_name)


def resolve_inventory_path(inventory: Path | None) -> Path:
    """Resolve the ``--inventory`` CLI value to a concrete document path.

    A directory (or the default ``.``) resolves to ``<dir>/targets.yaml``; a
    file is used as given. Raises :class:`ModuleError`, naming the path it
    tried, if a named directory has no ``targets.yaml`` or a named file is
    missing. See README.md "The module model".
    """
    value = inventory if inventory is not None else Path(".")
    if value.is_dir():
        candidate = value / INVENTORY_FILENAME
        if not candidate.is_file():
            raise ModuleError(f"no {INVENTORY_FILENAME} found in inventory directory {value}")
        return candidate
    if value.is_file():
        return value
    raise ModuleError(f"inventory not found: {value}")


def resolve_secrets_path(secrets: Path | None, *, inventory_dir: Path) -> Path | None:
    """Resolve the ``--secrets`` CLI value to a concrete secrets file, or
    ``None`` for "run without a secrets overlay".

    An explicit ``--secrets`` is used as given, so naming a file that does not
    exist stays an error. Otherwise ``<inventory_dir>/secrets.yaml`` is used
    when it exists — the same convention that resolves an inventory *directory*
    to its ``targets.yaml``. Inference is existence-conditional: an inventory
    with no sibling secrets file simply renders without one, which is what any
    inventory that references no secret needs.
    """
    if secrets is not None:
        return secrets
    candidate = inventory_dir / SECRETS_FILENAME
    return candidate if candidate.is_file() else None


# --------------------------------------------------------------------------
# Reference resolution
# --------------------------------------------------------------------------


def resolve_ref(raw: str, *, declaring: Document, closure: Closure) -> Ref:
    """Resolve a written reference (as it appears in ``declaring``) into a
    :class:`Ref` naming the document it denotes.

    A bare reference resolves to ``declaring`` itself (module ``None`` when
    ``declaring`` is the root); a qualified ``ns:path`` reference resolves to
    the closure member named ``ns``, which must exist. See README.md "The
    module model" — note this is resolution of *identity*, not yet a
    filesystem path; see :func:`fragment_path`/:func:`template_path`/
    :func:`schema_path` for that.
    """
    module_name, path = parse_ref(raw)
    if module_name is None:
        return Ref(module=declaring.name, path=path)
    if module_name not in closure.by_name:
        raise ModuleError(
            f"reference {raw!r} (in {declaring.path}) names unknown module {module_name!r}"
        )
    return Ref(module=module_name, path=path)


def _document_for(ref: Ref, closure: Closure) -> Document:
    return closure.root if ref.module is None else closure.by_name[ref.module]


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _resolve_in_dir(ref: Ref, closure: Closure, kind: RefKind, rel: str) -> Path:
    doc = _document_for(ref, closure)
    directory = doc.dir_for(kind)
    if directory is None:
        raise ModuleError(
            f"{display_ref(ref)}: document {doc.path} provides no {kind} directory"
        )
    candidate = (directory / rel).resolve()
    if not _is_within(candidate, doc.root):
        raise ModuleError(
            f"{display_ref(ref)}: resolves to {candidate}, which escapes module "
            f"root {doc.root}"
        )
    return candidate


def fragment_path(ref: Ref, closure: Closure) -> Path:
    """The absolute file path a resolved fragment :class:`Ref` denotes
    (``<fragments_dir>/<path>.yaml``), containment-checked against the
    resolved module's own directory."""
    return _resolve_in_dir(ref, closure, "fragments", f"{ref.path}.yaml")


def template_path(ref: Ref, closure: Closure) -> Path:
    """The absolute file path a resolved template :class:`Ref` denotes,
    containment-checked against the resolved module's own directory."""
    return _resolve_in_dir(ref, closure, "templates", ref.path)


def schema_path(ref: Ref, closure: Closure) -> Path:
    """The absolute file path a resolved schema :class:`Ref` denotes,
    containment-checked against the resolved module's own directory."""
    return _resolve_in_dir(ref, closure, "schemas", ref.path)


def _resolve_output_fragments(
    raw: dict[str, tuple[str, ...]], *, declaring: Document, closure: Closure
) -> OutputFragments:
    return {
        output_name: tuple(resolve_ref(item, declaring=declaring, closure=closure) for item in refs)
        for output_name, refs in raw.items()
    }


# --------------------------------------------------------------------------
# Flattening
# --------------------------------------------------------------------------


def _flatten_outputs(closure: Closure) -> tuple[dict[str, OutputSpec], str | None, dict[str, ValidatorSpec]]:
    outputs: dict[str, OutputSpec] = {}
    declared_by: dict[str, Document] = {}
    validators: dict[str, ValidatorSpec] = {}

    for doc in closure.documents:
        for vname, vspec in doc.validators.items():
            if vname in validators:
                raise ModuleError(f"validator {vname!r} is declared more than once in the closure")
            validators[vname] = vspec

        for oname, raw_output in doc.outputs.items():
            if oname in outputs:
                raise ModuleError(
                    f"output {oname!r} is declared more than once in the closure "
                    f"(by {declared_by[oname].path} and {doc.path})"
                )
            declared_by[oname] = doc
            template_ref = (
                resolve_ref(raw_output.template, declaring=doc, closure=closure)
                if raw_output.template
                else None
            )
            schema_ref = (
                resolve_ref(raw_output.schema, declaring=doc, closure=closure)
                if raw_output.schema
                else None
            )
            outputs[oname] = OutputSpec(
                path=raw_output.path,
                template=str(template_path(template_ref, closure)) if template_ref else None,
                schema=str(schema_path(schema_ref, closure)) if schema_ref else None,
                validators=raw_output.validators,
                default=raw_output.default,
                scope=raw_output.scope,
            )

    default_candidates = [name for name, spec in outputs.items() if spec.default]
    if len(default_candidates) > 1:
        raise ModuleError(
            f"only one output across the closure may be marked default: true, "
            f"got {sorted(default_candidates)}"
        )
    if default_candidates:
        default_output: str | None = default_candidates[0]
    elif len(outputs) == 1:
        default_output = next(iter(outputs))
    else:
        default_output = None

    for oname, ospec in outputs.items():
        for vname in ospec.validators:
            if vname not in validators:
                raise ModuleError(
                    f"output {oname!r} default validator {vname!r} is not declared "
                    f"anywhere in the closure"
                )

    if not outputs:
        raise ModuleError("the closure declares no outputs")

    return outputs, default_output, validators


def _flatten_defaults(closure: Closure) -> tuple[Variables, OutputFragments]:
    variables: Variables = {}
    output_fragments: dict[str, list[Ref]] = {}
    for doc in closure.documents:
        variables.update(doc.default_variables)
        resolved = _resolve_output_fragments(doc.default_output_fragments, declaring=doc, closure=closure)
        for oname, refs in resolved.items():
            output_fragments.setdefault(oname, []).extend(refs)
    return variables, {name: tuple(refs) for name, refs in output_fragments.items()}


def _flatten_groups(closure: Closure) -> dict[str, GroupDefinition]:
    variables: dict[str, Variables] = {}
    fragments: dict[str, dict[str, list[Ref]]] = {}
    for doc in closure.documents:
        for gname, raw_group in doc.groups.items():
            variables.setdefault(gname, {}).update(raw_group.variables)
            resolved = _resolve_output_fragments(
                raw_group.output_fragments, declaring=doc, closure=closure
            )
            group_fragments = fragments.setdefault(gname, {})
            for oname, refs in resolved.items():
                group_fragments.setdefault(oname, []).extend(refs)

    return {
        name: GroupDefinition(
            name=name,
            variables=variables[name],
            output_fragments={oname: tuple(refs) for oname, refs in fragments.get(name, {}).items()},
        )
        for name in variables
    }


def _flatten_targets(closure: Closure) -> dict[str, TargetDefinition]:
    root = closure.root
    return {
        tname: TargetDefinition(
            name=tname,
            groups=raw_target.groups,
            output_fragments=_resolve_output_fragments(
                raw_target.output_fragments, declaring=root, closure=closure
            ),
            variables=raw_target.variables,
        )
        for tname, raw_target in root.targets.items()
    }


def _flatten_aggregate(
    closure: Closure, outputs: dict[str, OutputSpec]
) -> tuple[Variables, dict[str, AggregateFragments]]:
    """Flatten every document's ``aggregate:`` block across the closure.

    Mirrors :func:`_flatten_defaults`: ``aggregate.variables`` layers in
    closure order (later wins), and each output's ``prologue``/``epilogue``
    extends independently, also in closure order. Checked here, inside the
    per-document loop, so a fail-closed error can name the declaring document
    (README.md "Aggregate outputs"): naming an output undefined anywhere in
    the closure, or one whose ``scope`` is ``"target"``, since prologue/
    epilogue exist only for ``scope: aggregate`` outputs.
    """
    variables: Variables = {}
    prologue: dict[str, list[Ref]] = {}
    epilogue: dict[str, list[Ref]] = {}
    for doc in closure.documents:
        variables.update(doc.aggregate_variables)
        for output_name, raw_aggregate in doc.aggregate_output_fragments.items():
            if output_name not in outputs:
                raise ModuleError(
                    f"document {doc.path}: `aggregate.outputs` names undefined "
                    f"output {output_name!r}"
                )
            output = outputs[output_name]
            if output.scope != "aggregate":
                raise ModuleError(
                    f"document {doc.path}: `aggregate.outputs.{output_name}` declares "
                    f"a prologue/epilogue, but output {output_name!r} has scope "
                    f"{output.scope!r}; prologue/epilogue exist only for "
                    f"`scope: aggregate` outputs"
                )
            prologue.setdefault(output_name, []).extend(
                resolve_ref(ref, declaring=doc, closure=closure) for ref in raw_aggregate.prologue
            )
            epilogue.setdefault(output_name, []).extend(
                resolve_ref(ref, declaring=doc, closure=closure) for ref in raw_aggregate.epilogue
            )

    output_names = set(prologue) | set(epilogue)
    output_fragments = {
        name: AggregateFragments(
            prologue=tuple(prologue.get(name, ())), epilogue=tuple(epilogue.get(name, ()))
        )
        for name in output_names
    }
    return variables, output_fragments


def flatten(closure: Closure) -> Project:
    """Flatten a loaded :class:`Closure` into one closure-wide
    :class:`~yaml_frag.models.Project`.

    See the module docstring's "Flattening" section and README.md
    "Composition and ordering" for the exact ordering/merge rules
    implemented here: closure-order concatenation for defaults, closure-order
    merge for same-named groups, and closure-wide uniqueness for output and
    validator names.
    """
    outputs, default_output, validators = _flatten_outputs(closure)
    default_variables, default_output_fragments = _flatten_defaults(closure)
    groups = _flatten_groups(closure)
    targets = _flatten_targets(closure)
    aggregate_variables, aggregate_output_fragments = _flatten_aggregate(closure, outputs)

    for tname, target in targets.items():
        for group_name in target.groups:
            if group_name not in groups:
                raise ModuleError(f"target {tname!r} references undefined group {group_name!r}")

    for attacher_label, fragments_by_output in [
        ("defaults", default_output_fragments),
        *[(f"group {gname!r}", g.output_fragments) for gname, g in groups.items()],
        *[(f"target {tname!r}", t.output_fragments) for tname, t in targets.items()],
    ]:
        for output_name in fragments_by_output:
            if output_name not in outputs:
                raise ModuleError(
                    f"{attacher_label} attaches fragments to undefined output {output_name!r}"
                )

    return Project(
        version=SUPPORTED_DOCUMENT_VERSION,
        outputs=outputs,
        default_output=default_output,
        validators=validators,
        default_variables=default_variables,
        default_output_fragments=default_output_fragments,
        groups=groups,
        targets=targets,
        aggregate_variables=aggregate_variables,
        aggregate_output_fragments=aggregate_output_fragments,
    )


__all__ = [
    "INVENTORY_FILENAME",
    "MODULE_FILENAME",
    "Closure",
    "Document",
    "display_ref",
    "flatten",
    "fragment_path",
    "load_closure",
    "parse_ref",
    "resolve_inventory_path",
    "resolve_ref",
    "schema_path",
    "template_path",
]
