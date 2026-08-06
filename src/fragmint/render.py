"""Top-level rendering orchestration.

Ties together the flattened project, fragment loading, templating, merge
operations, provenance, validation, serialization, and output templating. See
README.md "Rendering algorithm" (per-target composition) and "Aggregate
outputs" (the second, small composition loop this module adds for
``scope: aggregate`` outputs).

:class:`RenderSession` owns the once-per-run work — loading the import
closure (:mod:`modules`) and flattening it once, plus the secret store, and
memoizing each target's resolved/resolved-and-source-resolved variables and
each loaded fragment (keyed by its resolved :class:`~fragmint.models.Ref`,
which is collision-safe by construction: two modules may both contain a
``host`` fragment) — so a single CLI invocation that touches many targets
(``render-all``, or any aggregate output, which by construction visits every
target) reads the closure once and runs each target's variable-value
captures at most once, regardless of how many outputs (per-target or
aggregate) end up consuming them. :func:`render_target` is a thin wrapper
over it — "build a session, render one target" — for callers that only need a
single target.

The functions here are pure with respect to the filesystem where practical:
:func:`render_target` and :meth:`RenderSession.render_target_outputs` return a
:class:`~models.RenderResult` (in-memory, one :class:`~models.RenderedOutput`
per output the target produces), :meth:`RenderSession.render_aggregate`
returns a single :class:`~models.RenderedOutput` (or ``None`` if no target
contributed), :func:`compose_output` turns a single output's result into
final output text, and :func:`write_output` handles the atomic on-disk write
separately so ``validate``/``inspect``/``--stdout`` can render without
writing.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Collection, Sequence
from dataclasses import replace
from pathlib import Path

from . import fragments as fragments_mod
from . import merge, modules, sources, templating, validation, yamlio
from . import pointer as pointer_mod
from .errors import FragmintError, InventoryError, ModuleError, TemplateRenderError
from .inventory import RESERVED_VARIABLE_NAMES, load_secret_store, resolve_target
from .models import (
    Fragment,
    OutputSpec,
    Project,
    ProvenanceEntry,
    Ref,
    RenderedOutput,
    RenderResult,
    ResolvedTarget,
    SecretStore,
    Variables,
    VariableSource,
    YamlValue,
)
from .modules import Closure
from .provenance import ProvenanceTracker
from .sources import CommandRunner

#: Literal token an output template may contain; replaced by the serialized
#: YAML during :func:`compose_output`. See README.md "The module model".
DOCUMENT_TOKEN = "{{ document }}"

#: Substring patterns that mark a variable name for redaction in ``inspect``
#: output. See README.md "CLI usage".
REDACT_SUBSTRINGS = ("password", "secret", "token", "private", "credential")


def resolve_output_path(output: OutputSpec, target: str, override: Path | None = None) -> Path:
    """Compute the destination path for ``target`` under a single output.

    When ``override`` is given, use it verbatim. Otherwise substitute
    ``{target}`` into ``output.path``. See README.md "The module model" and
    "CLI usage".
    """
    if override is not None:
        return override
    return Path(output.path.format(target=target))


def resolve_aggregate_output_path(output: OutputSpec, override: Path | None = None) -> Path:
    """Compute the destination path for an aggregate-scoped output.

    There is no per-target substitution here — an aggregate output has a
    single fixed path for the whole run (README.md "Aggregate outputs").
    ``override`` is used verbatim when given. Kept as a distinct function from
    :func:`resolve_output_path` (rather than a shared one with an optional
    target) so the type of path computation being performed is explicit at
    every call site. Raises :class:`~fragmint.errors.ModuleError` if
    ``output.scope`` is not ``"aggregate"``.
    """
    if output.scope != "aggregate":
        raise ModuleError(
            f"resolve_aggregate_output_path called on a `scope: {output.scope}` output"
        )
    if override is not None:
        return override
    return Path(output.path)


def select_validators(
    project: Project,
    output: OutputSpec,
    requested: tuple[str, ...],
) -> tuple[str, ...]:
    """Resolve which validators to run for a single output.

    If ``requested`` is non-empty, use it (validating each name exists in
    ``project.validators``; unknown names raise
    :class:`~fragmint.errors.ModuleError`). Otherwise fall back to
    ``output.validators``. See README.md "Validation" (named validators).
    """
    if requested:
        for name in requested:
            if name not in project.validators:
                raise ModuleError(f"unknown validator {name!r} requested")
        return requested
    return output.validators


def _render_operation_path(
    path: str,
    variables: Variables,
    *,
    scope: str,
    fragment: str,
    operation_index: int,
) -> str:
    """Render an operation's ``path`` through templating and validate it.

    See README.md "Template rendering" and "Paths". A templated path (e.g.
    ``/all/children/{{ ansible_group }}/hosts/{{ target }}``, the case
    "Aggregate outputs" needs) must render to a *string* that is itself a
    valid JSON Pointer — checked here, BEFORE :mod:`merge`/:mod:`pointer` ever
    see the result, so a malformed pointer or a non-string template result
    fails closed with the fragment name and operation index rather than
    surfacing as an unrelated-looking pointer error deeper in the stack.
    ``scope`` is an already-formatted, display-ready label (a target name, or
    an aggregate prologue/epilogue label). Raises
    :class:`~fragmint.errors.TemplateRenderError` in either failure case.
    """
    rendered = templating.render_value(
        path, variables, scope=scope, fragment=fragment, operation_index=operation_index
    )
    if not isinstance(rendered, str):
        raise TemplateRenderError(
            f"{scope}: fragment {fragment}, operation {operation_index}: templated "
            f"path must render to a string, got {type(rendered).__name__} ({rendered!r})"
        )
    try:
        pointer_mod.parse_pointer(rendered)
    except FragmintError as exc:
        raise TemplateRenderError(
            f"{scope}: fragment {fragment}, operation {operation_index}: templated "
            f"path {rendered!r} is not a valid JSON Pointer: {exc}"
        ) from exc
    return rendered


def _fragment_variable_names(fragment: Fragment) -> tuple[str, ...]:
    """Every variable name any operation in ``fragment`` templates — its
    ``path``, ``value``, and ``assertion``, via
    :func:`templating.collect_variable_names` — in first-appearance order.
    This is the unit of demand a rendered output's variable resolution is
    based on (README.md "Resolution timing")."""
    names: dict[str, None] = {}
    for op in fragment.operations:
        for value in (op.path, op.value, op.assertion):
            for name in templating.collect_variable_names(value):
                names.setdefault(name, None)
    return tuple(names)


class RenderSession:
    """The whole import closure, flattened once, plus per-run memoization.

    See README.md "Rendering algorithm" and "Aggregate outputs". A session is
    constructed once per CLI invocation (or once per test) and reused across
    every target/output it touches:

    - :meth:`resolve` memoizes each target's :class:`~models.ResolvedTarget`
      (per-output fragment order + layered-but-unresolved variables);
    - :meth:`variable` resolves and memoizes one ``(target, variable)`` value
      source at a time — secret lookup or capture execution — so a
      ``from: capture`` subprocess runs at most once per target per session
      no matter how many outputs demand the variable it produces (README.md
      "Resolution timing"); a variable no rendered output's fragments ever
      reference is never resolved at all;
    - fragment loading (:meth:`_load_fragment`) is memoized by resolved
      :class:`~models.Ref`, so a fragment shared by many targets (the common
      case for an aggregate output, e.g. ``ansible/host``) is read and
      schema-validated once, not once per target, and two modules that both
      happen to contain a same-named fragment stay distinct.

    ``secrets_path`` goes through :func:`modules.resolve_secrets_path`, so
    omitting it picks up a ``secrets.yaml`` beside the inventory; the resolved
    value (``None`` when there is none) is kept as :attr:`secrets_path`.

    :func:`render_target` is a thin wrapper that builds a one-shot session and
    renders a single target.

    ``apply_assertions``, when ``False``, skips every ``assert`` operation
    during composition instead of evaluating it — distinct from and
    orthogonal to ``validate``/``redact_sources``. Since :func:`merge.apply_operation`
    never mutates the document or records provenance for ``assert`` (README.md
    "Merge operations"), skipping it changes nothing about a document whose
    assertions all pass; the only observable effect is that a *failing*
    assertion no longer raises. This is what lets ``explain`` (the only
    caller that sets it) report provenance for a document that fails its own
    checks (README.md "Provenance and `explain`"). It has no effect on
    ``render``/``validate``, which always leave it at the default ``True``.
    """

    def __init__(
        self,
        closure: Closure,
        *,
        cli_variables: Variables | None = None,
        secrets_path: Path | None = None,
        runner: CommandRunner | None = None,
        redact_sources: bool = False,
        apply_assertions: bool = True,
    ) -> None:
        self.closure = closure
        self.cli_variables = cli_variables
        self.redact_sources = redact_sources
        self.apply_assertions = apply_assertions
        self.project: Project = modules.flatten(closure)
        self.secrets_path = modules.resolve_secrets_path(
            secrets_path, inventory_dir=closure.root.root
        )
        self.store: SecretStore = (
            load_secret_store(self.secrets_path) if self.secrets_path is not None else SecretStore()
        )
        self._resolved: dict[str, ResolvedTarget] = {}
        self._sources: dict[str, dict[str, VariableSource]] = {}
        self._values: dict[tuple[str, str], YamlValue | BaseException] = {}
        self._fragments: dict[Ref, Fragment] = {}
        self._aggregate_source_map: dict[str, VariableSource] | None = None
        self._aggregate_values: dict[str, YamlValue | BaseException] = {}
        self._runner: CommandRunner = runner if runner is not None else sources.DefaultCommandRunner()

    def resolve(self, target_name: str) -> ResolvedTarget:
        """Resolve ``target_name``'s per-output fragment order and layered
        (still-unresolved) variables, memoized for the life of the session."""
        if target_name not in self._resolved:
            self._resolved[target_name] = resolve_target(
                self.project, target_name, cli_variables=self.cli_variables
            )
        return self._resolved[target_name]

    def _parsed_sources(self, target_name: str) -> dict[str, VariableSource]:
        """This target's layered variables, each parsed into a
        :class:`~fragmint.models.VariableSource` (structural validation only
        — no secret lookup, no subprocess), memoized for the life of the
        session. Parsing stays eager for every declared variable so a
        malformed source fails closed even for one no fragment ever
        references (README.md "Variable value sources")."""
        if target_name not in self._sources:
            resolved = self.resolve(target_name)
            self._sources[target_name] = {
                name: sources.parse_source(raw) for name, raw in resolved.variables.items()
            }
        return self._sources[target_name]

    def variable(self, target_name: str, name: str) -> YamlValue:
        """Resolve one variable's value source for ``target_name`` — secret
        lookup or capture execution — memoized per ``(target_name, name)``
        for the life of the session, including failures: a repeat demand for
        a variable whose resolution previously raised re-raises the same
        exception without looking anything up or running anything again.
        This is what keeps a capture subprocess running at most once per
        target per session, shared across every output that demands the
        variable it produces (README.md "Resolution timing")."""
        key = (target_name, name)
        if key not in self._values:
            try:
                self._values[key] = sources.resolve_source(
                    self._parsed_sources(target_name)[name],
                    secrets=self.store,
                    runner=self._runner,
                    scope=f"target {target_name!r}",
                    variable=name,
                    redact=self.redact_sources,
                )
            except Exception as exc:  # noqa: BLE001 - memoized and re-raised below, on every repeat demand
                self._values[key] = exc
        value = self._values[key]
        if isinstance(value, BaseException):
            raise value
        return value

    def _variables_for_output(
        self, target_name: str, output_name: str, fragments: Sequence[Fragment]
    ) -> Variables:
        """The variables that ``output_name``'s fragments actually
        reference, resolved on demand for ``target_name``: the union, in
        first-appearance order, of :func:`_fragment_variable_names` over
        every fragment, resolved through :meth:`variable` for each name that
        is one of the target's own variables, plus the reserved ``output``
        name. A referenced name that is not one of the target's variables is
        skipped here — it is either genuinely undefined, in which case
        ``StrictUndefined`` reports it once the fragment renders, or it is
        ``output`` itself, injected below (README.md "Resolution timing").
        """
        parsed = self._parsed_sources(target_name)
        names: dict[str, None] = {}
        for fragment in fragments:
            for name in _fragment_variable_names(fragment):
                names.setdefault(name, None)
        variables: Variables = {
            name: self.variable(target_name, name) for name in names if name in parsed
        }
        variables["output"] = output_name
        return variables

    def _aggregate_sources(self) -> dict[str, VariableSource]:
        """The aggregate scope's layered variable sources (README.md
        "Variable precedence" — "The aggregate scope"), each parsed
        (structural validation only — no secret lookup, no subprocess),
        memoized for the life of the session. Only called when some output's
        prologue/epilogue actually needs the aggregate scope — a closure with
        no ``aggregate:`` block, or one whose outputs use no prologue/
        epilogue, never triggers this check or any resolution.
        """
        if self._aggregate_source_map is None:
            layered = layered_aggregate_variables(self.project)
            self._aggregate_source_map = {
                name: sources.parse_source(raw) for name, raw in layered.items()
            }
        return self._aggregate_source_map

    def _aggregate_variable(self, name: str) -> YamlValue:
        """Resolve one aggregate-scope variable by name — secret lookup or
        capture execution — memoized for the life of the session. The
        aggregate-scope analogue of :meth:`variable`; ``target`` is not a
        valid name here since it is never part of this scope (README.md
        "Aggregate outputs" — "The aggregate scope")."""
        if name not in self._aggregate_values:
            try:
                self._aggregate_values[name] = sources.resolve_source(
                    self._aggregate_sources()[name],
                    secrets=self.store,
                    runner=self._runner,
                    scope="aggregate scope",
                    variable=name,
                    redact=self.redact_sources,
                )
            except Exception as exc:  # noqa: BLE001 - memoized and re-raised below, on every repeat demand
                self._aggregate_values[name] = exc
        value = self._aggregate_values[name]
        if isinstance(value, BaseException):
            raise value
        return value

    def _variables_for_aggregate_scope(
        self, output_name: str, fragments: Sequence[Fragment]
    ) -> Variables:
        """The aggregate scope's variables that ``output_name``'s prologue/
        epilogue fragments actually reference, resolved on demand — the
        aggregate-scope analogue of :meth:`_variables_for_output`. ``output``
        is injected; ``target`` deliberately never is, so ``{{ target }}`` in
        a prologue/epilogue fragment raises a strict-undefined
        :class:`TemplateRenderError` (README.md "Aggregate outputs" — "The
        aggregate scope"). Calling this triggers :meth:`_aggregate_sources`'s
        reserved-name check even if no fragment references anything in the
        scope."""
        parsed = self._aggregate_sources()
        names: dict[str, None] = {}
        for fragment in fragments:
            for name in _fragment_variable_names(fragment):
                names.setdefault(name, None)
        variables: Variables = {
            name: self._aggregate_variable(name) for name in names if name in parsed
        }
        variables["output"] = output_name
        return variables

    def _load_fragment(self, ref: Ref) -> Fragment:
        """Load + validate a fragment by resolved reference, memoized for the
        life of the session (README.md "Aggregate outputs": otherwise a
        fragment shared by many targets would be re-read and re-validated once
        per target)."""
        if ref not in self._fragments:
            path = modules.fragment_path(ref, self.closure)
            self._fragments[ref] = fragments_mod.load_fragment(path, modules.display_ref(ref))
        return self._fragments[ref]

    def _apply_fragment(
        self,
        doc: dict[str, YamlValue],
        tracker: ProvenanceTracker,
        *,
        scope_label: str,
        fragment: Fragment,
        variables: Variables,
        provenance_target: str | None,
        defined: Collection[str],
    ) -> None:
        """Apply one fragment's operations to ``doc`` in listed order:
        check required variables; render each operation's path/value/assertion
        through templating; apply the operation, recording provenance.

        ``scope_label`` is the render scope shown in diagnostics — the
        target's own name for per-target and aggregate-target rendering, or an
        ``<aggregate NAME prologue/epilogue>`` label for aggregate composition
        outside the per-target loop (README.md "Aggregate outputs" — "The
        aggregate scope"). ``provenance_target`` is recorded on every
        :class:`~models.ProvenanceEntry` produced — ``None`` for per-target
        rendering and for an aggregate prologue/epilogue (redundant or
        inapplicable there: there's only one target in play, or none), the
        contributing target's name for aggregate rendering of a target's own
        fragments, where the same fragment runs once per target and "fragment
        X operation 0" alone no longer identifies a single write.

        ``defined`` is the set of names a ``requires: variables:`` entry may
        legally name in this scope — the scope's own variable names plus
        ``output`` (and, in the per-target/aggregate-target scopes, ``target``,
        since it's one of the target's own variables). It is NOT ``variables``
        itself: under on-demand resolution ``variables`` holds only the names
        this fragment's own operations reference, so a fragment that declares
        a required variable without templating it would otherwise be
        (incorrectly) reported missing.
        """
        for required_var in fragment.required_variables:
            if required_var not in defined:
                raise TemplateRenderError(
                    f"{scope_label}: fragment {fragment.name}: missing required "
                    f"variable {required_var!r}"
                )

        for index, op in enumerate(fragment.operations):
            if op.op == "assert" and not self.apply_assertions:
                # `assert` never mutates `doc` or records provenance (see
                # `merge.apply_operation`), so skipping it here is a no-op on
                # a document whose assertions would have passed; the only
                # effect is that a failing one no longer raises. Used by
                # `explain` so a failing assertion doesn't defeat the one
                # command meant to diagnose it.
                continue

            rendered_path = _render_operation_path(
                op.path,
                variables,
                scope=scope_label,
                fragment=fragment.name,
                operation_index=index,
            )
            rendered_value = templating.render_value(
                op.value,
                variables,
                scope=scope_label,
                fragment=fragment.name,
                operation_index=index,
            )
            rendered_assertion = {
                key: templating.render_value(
                    value,
                    variables,
                    scope=scope_label,
                    fragment=fragment.name,
                    operation_index=index,
                )
                for key, value in op.assertion.items()
            }
            rendered_op = replace(
                op, path=rendered_path, value=rendered_value, assertion=rendered_assertion
            )
            entry = ProvenanceEntry(
                fragment=fragment.name,
                operation_index=index,
                operation=op.op,
                target=provenance_target,
            )
            merge.apply_operation(doc, rendered_op, entry=entry, tracker=tracker)

    def _validate_document(self, doc: dict[str, YamlValue], output: OutputSpec) -> None:
        """Generic structural validation shared by per-target and aggregate
        rendering (README.md "Validation"): reject unresolved template
        markers, then optionally validate against ``output.schema``."""
        validation.check_unresolved_markers(doc)
        if output.schema:
            validation.validate_against_schema(doc, Path(output.schema))

    def render_target_outputs(
        self, target_name: str, *, only: str | None = None, validate: bool = True
    ) -> RenderResult:
        """Render every PER-TARGET output ``target_name`` produces, in memory
        (README.md "Rendering algorithm").

        For each produced output, independently: load + validate its
        referenced fragments, resolve exactly the variables those fragments
        reference (plus the reserved ``output`` name) via
        :meth:`_variables_for_output`, start from an empty document, and apply
        each fragment's operations in order via :meth:`_apply_fragment`. A
        secret lookup or capture only this output's fragments reference is
        never looked up/run for a *different* output (README.md "Resolution
        timing"). Runs generic validation per output unless ``validate`` is
        ``False``.

        ``only``, when given, restricts rendering to that single named
        per-target output: every other produced output is skipped entirely —
        not just excluded from the result, but never loaded, never resolved,
        and never validated. This is what lets ``--only NAME`` (README.md
        "CLI usage") need only ``NAME``'s own secrets/captures and pass even
        when a *different* output would fail schema validation or an
        `assert`.

        Outputs with ``scope: aggregate`` are skipped here even if this
        target contributes fragments to one — they're composed once for the
        whole run by :meth:`render_aggregate`, never per target (README.md
        "Aggregate outputs": ``render TARGET`` silently skips them).
        """
        resolved = self.resolve(target_name)
        defined = resolved.variables.keys() | {"output"}

        outputs: dict[str, RenderedOutput] = {}
        for output_name, refs in resolved.output_fragments.items():
            output = self.project.outputs[output_name]
            if output.scope == "aggregate":
                continue
            if only is not None and output_name != only:
                continue

            fragments = [self._load_fragment(ref) for ref in refs]
            output_variables = self._variables_for_output(target_name, output_name, fragments)

            doc: dict[str, YamlValue] = {}
            tracker = ProvenanceTracker()
            for fragment in fragments:
                self._apply_fragment(
                    doc,
                    tracker,
                    scope_label=target_name,
                    fragment=fragment,
                    variables=output_variables,
                    provenance_target=None,
                    defined=defined,
                )

            if validate:
                self._validate_document(doc, output)

            outputs[output_name] = RenderedOutput(
                name=output_name,
                document=doc,
                provenance=tracker.entries,
                overrides=tuple(tracker.overrides),
            )

        return RenderResult(target=target_name, outputs=outputs)

    def render_aggregate(self, output_name: str, *, validate: bool = True) -> RenderedOutput | None:
        """Compose the single document for ``output_name`` (``scope:
        aggregate``) from every target that contributes to it, for the whole
        run (README.md "Aggregate outputs").

        Three phases apply, in order, to one shared document: this output's
        ``aggregate.outputs.<name>.prologue`` (closure order, once, before any
        target); each contributing target's resolved fragment list for this
        output, in inventory declaration order (never sorted) and, within a
        target, that target's own fragment order — the same
        positional-precedence rule as per-target rendering, applied across
        targets instead of within one, so a later target's write can override
        an earlier target's (or the prologue's) contribution at the same path
        (and an unintended collision surfaces as the normal override
        warning, now naming both targets); then this output's
        ``aggregate.outputs.<name>.epilogue`` (closure order, once, after
        every contributing target, and able to override any target's write
        in turn). A target that contributes no fragments for this output is
        simply skipped (its opt-out).

        Returns ``None`` — nothing to write, and prologue/epilogue are never
        applied — if no target contributed (README.md "Fragment order": an
        output nothing feeds is not produced; a prologue/epilogue alone does
        not make it produced either). Epilogue ``assert`` operations run
        during this composition, not validation, so they are NOT suppressed
        by ``validate=False`` — identically to today's per-target trailing-
        assertion idiom. The session's ``apply_assertions`` flag is the only
        thing that skips them (used by ``explain``). Generic validation
        (unresolved-marker check + optional ``output.schema``) still runs on
        the finished document unless ``validate`` is ``False``. Any
        ``assert`` operation inside a fragment contributing to an aggregate
        output *from a target* only ever sees the PARTIAL document built so
        far (up through the current target) — an epilogue fragment is the
        remedy (README.md "Aggregate outputs").
        """
        output = self.project.outputs[output_name]
        if output.scope != "aggregate":
            raise ModuleError(
                f"output {output_name!r} has scope {output.scope!r}, not `aggregate`"
            )

        contributing = [
            target_name
            for target_name in self.project.targets
            if self.resolve(target_name).output_fragments.get(output_name)
        ]
        if not contributing:
            return None

        spec = self.project.aggregate_output_fragments.get(output_name)
        has_outer = spec is not None and bool(spec.prologue or spec.epilogue)

        doc: dict[str, YamlValue] = {}
        tracker = ProvenanceTracker()

        # Loading these here (before touching the aggregate scope at all)
        # keeps the `has_outer` laziness guard intact: a closure with no
        # `aggregate:` block never calls `_aggregate_sources`/
        # `_variables_for_aggregate_scope`, so it never runs the
        # reserved-name check or resolves anything in that scope.
        prologue_fragments: list[Fragment] = []
        epilogue_fragments: list[Fragment] = []
        outer_variables: Variables = {}
        outer_defined: Collection[str] = ()
        if has_outer and spec is not None:
            prologue_fragments = [self._load_fragment(ref) for ref in spec.prologue]
            epilogue_fragments = [self._load_fragment(ref) for ref in spec.epilogue]
            outer_variables = self._variables_for_aggregate_scope(
                output_name, [*prologue_fragments, *epilogue_fragments]
            )
            outer_defined = self._aggregate_sources().keys() | {"output"}

        for fragment in prologue_fragments:
            self._apply_fragment(
                doc,
                tracker,
                scope_label=f"<aggregate {output_name} prologue>",
                fragment=fragment,
                variables=outer_variables,
                provenance_target=None,
                defined=outer_defined,
            )

        for target_name in contributing:
            refs = self.resolve(target_name).output_fragments[output_name]
            fragments = [self._load_fragment(ref) for ref in refs]
            variables = self._variables_for_output(target_name, output_name, fragments)
            defined = self.resolve(target_name).variables.keys() | {"output"}

            for fragment in fragments:
                self._apply_fragment(
                    doc,
                    tracker,
                    scope_label=target_name,
                    fragment=fragment,
                    variables=variables,
                    provenance_target=target_name,
                    defined=defined,
                )

        for fragment in epilogue_fragments:
            self._apply_fragment(
                doc,
                tracker,
                scope_label=f"<aggregate {output_name} epilogue>",
                fragment=fragment,
                variables=outer_variables,
                provenance_target=None,
                defined=outer_defined,
            )

        if validate:
            self._validate_document(doc, output)

        return RenderedOutput(
            name=output_name,
            document=doc,
            provenance=tracker.entries,
            overrides=tuple(tracker.overrides),
        )


def render_target(
    target_name: str,
    *,
    closure: Closure,
    cli_variables: Variables | None = None,
    secrets_path: Path | None = None,
    runner: CommandRunner | None = None,
    validate: bool = True,
    redact_sources: bool = False,
) -> RenderResult:
    """Render a single target's every produced PER-TARGET output in memory.

    Thin wrapper over :class:`RenderSession`: build a one-shot session and
    render just this target (``session.render_target_outputs(target_name,
    validate=validate)``). Use it when a caller only ever renders one target
    and has no reason to hold a session; construct a :class:`RenderSession`
    directly to render several targets, or any aggregate output, without
    re-flattening the closure. See README.md "Rendering algorithm".

    ``runner`` is injectable so tests can resolve captures deterministically
    without running real programs. When ``redact_sources`` is true, secret and
    capture sources resolve to non-executing descriptions rather than being
    looked up or run — used by ``explain`` so it never executes captures or
    reveals secrets. Serialization and output templating happen in
    :func:`compose_output`, not here. No document-type knowledge lives here.
    Aggregate-scoped outputs are never produced through this function, even if
    ``target_name`` contributes fragments to one — see README.md "Aggregate
    outputs" and :meth:`RenderSession.render_aggregate`.
    """
    session = RenderSession(
        closure,
        cli_variables=cli_variables,
        secrets_path=secrets_path,
        runner=runner,
        redact_sources=redact_sources,
    )
    return session.render_target_outputs(target_name, validate=validate)


def compose_output(result: RenderedOutput, output: OutputSpec) -> str:
    """Produce the final output text for one rendered output.

    Serialize ``result.document`` deterministically via
    :func:`fragmint.yamlio.dump_str`. If ``output.template`` is set, read that
    file and replace the literal :data:`DOCUMENT_TOKEN` with the serialized
    YAML; otherwise use the serialized YAML verbatim. This is how a project adds
    a header such as ``#cloud-config``. Ends with a single trailing newline.
    Scope-agnostic: works identically for a per-target or an aggregate result
    (README.md "Aggregate outputs": the aggregate path adds no new
    serialization or templating code).
    """
    text = yamlio.dump_str(result.document)

    if output.template:
        template_text = Path(output.template).read_text(encoding="utf-8")
        composed = template_text.replace(DOCUMENT_TOKEN, text)
    else:
        composed = text

    return composed.rstrip("\n") + "\n"


def write_output(text: str, path: Path) -> Path:
    """Atomically write ``text`` to ``path``.

    Create parent directories, write to a temporary file in the destination
    directory, then ``os.replace`` into place so a failure never leaves a
    partial final file (README.md "CLI usage"). Return ``path``. Only the
    configured output file is written — no companion files are forced.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise
    return path


def layered_aggregate_variables(project: Project) -> Variables:
    """The aggregate scope's layered, UNRESOLVED variable map (README.md
    "Variable precedence" — "The aggregate scope"): every module's and the
    inventory's ``defaults.variables`` (closure order), then every
    ``aggregate.variables`` (closure order), later wins. Raises
    :class:`~fragmint.errors.InventoryError` if either layer defines the
    reserved ``target``/``output`` name — `target` is undefined in this
    scope, and `output` is set per aggregate output to that output's own
    name, so neither may be a real variable definition. Shared by
    :meth:`RenderSession._aggregate_sources` (which then parses each value
    into a :class:`~fragmint.models.VariableSource`) and ``inspect
    --aggregate`` (which instead redacts and displays this layer directly,
    unresolved, so the check fires identically in both places)."""
    layered: Variables = dict(project.default_variables)
    layered.update(project.aggregate_variables)
    reserved_conflicts = RESERVED_VARIABLE_NAMES & layered.keys()
    if reserved_conflicts:
        raise InventoryError(
            f"aggregate scope: variable name(s) "
            f"{', '.join(f'`{name}`' for name in sorted(reserved_conflicts))} "
            f"are reserved (`target` is undefined in this scope; `output` is "
            f"set to the aggregate output's own name) and must not be defined "
            f"in defaults/aggregate variables"
        )
    return layered


def redact_variables(variables: Variables, *, show_secrets: bool = False) -> Variables:
    """Return a copy of ``variables`` with secret-looking values redacted.

    Operates on the UNRESOLVED variables so captures are never executed. A
    variable defined via a ``from: secret``/``from: capture`` source is always
    shown as a non-executing description (see :func:`sources.describe_source`),
    regardless of ``show_secrets``. Otherwise, a variable is redacted when its
    name contains any of :data:`REDACT_SUBSTRINGS` (case-insensitive) and
    ``show_secrets`` is False. Used by the ``inspect`` command (README.md
    "CLI usage").
    """
    redacted: Variables = {}
    for name, value in variables.items():
        if sources.is_source(value):
            redacted[name] = sources.describe_source(value)
        elif not show_secrets and any(
            substring in name.lower() for substring in REDACT_SUBSTRINGS
        ):
            redacted[name] = "<redacted>"
        else:
            redacted[name] = value
    return redacted


__all__ = [
    "DOCUMENT_TOKEN",
    "REDACT_SUBSTRINGS",
    "RenderSession",
    "compose_output",
    "layered_aggregate_variables",
    "redact_variables",
    "render_target",
    "resolve_aggregate_output_path",
    "resolve_output_path",
    "select_validators",
    "write_output",
]
