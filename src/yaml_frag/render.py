"""Top-level rendering orchestration.

Ties together the flattened project, fragment loading, templating, merge
operations, provenance, validation, serialization, and output templating. See
README.md "Rendering algorithm" (per-target composition) and "Aggregate
outputs" (the second, small composition loop this module adds for
``scope: aggregate`` outputs).

:class:`RenderSession` owns the once-per-run work — loading the import
closure (:mod:`modules`) and flattening it once, plus the secret store, and
memoizing each target's resolved/resolved-and-source-resolved variables and
each loaded fragment (keyed by its resolved :class:`~yaml_frag.models.Ref`,
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
from dataclasses import replace
from pathlib import Path

from . import fragments as fragments_mod
from . import merge, modules, sources, templating, validation, yamlio
from . import pointer as pointer_mod
from .errors import InventoryError, ModuleError, TemplateRenderError, YamlFragError
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
    every call site. Raises :class:`~yaml_frag.errors.ModuleError` if
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
    :class:`~yaml_frag.errors.ModuleError`). Otherwise fall back to
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
    target: str,
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
    Raises :class:`~yaml_frag.errors.TemplateRenderError` in either failure
    case.
    """
    rendered = templating.render_value(
        path, variables, target=target, fragment=fragment, operation_index=operation_index
    )
    if not isinstance(rendered, str):
        raise TemplateRenderError(
            f"{target}: fragment {fragment}, operation {operation_index}: templated "
            f"path must render to a string, got {type(rendered).__name__} ({rendered!r})"
        )
    try:
        pointer_mod.parse_pointer(rendered)
    except YamlFragError as exc:
        raise TemplateRenderError(
            f"{target}: fragment {fragment}, operation {operation_index}: templated "
            f"path {rendered!r} is not a valid JSON Pointer: {exc}"
        ) from exc
    return rendered


class RenderSession:
    """The whole import closure, flattened once, plus per-run memoization.

    See README.md "Rendering algorithm" and "Aggregate outputs". A session is
    constructed once per CLI invocation (or once per test) and reused across
    every target/output it touches:

    - :meth:`resolve` memoizes each target's :class:`~models.ResolvedTarget`
      (per-output fragment order + layered-but-unresolved variables);
    - :meth:`variables` memoizes each target's fully SOURCE-RESOLVED variable
      map, so a ``from: capture`` subprocess runs at most once per target per
      session regardless of how many outputs consume it — this is the point
      that keeps ``render-all`` (and aggregate rendering, which visits every
      target by construction) from re-running captures per output;
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
    """

    def __init__(
        self,
        closure: Closure,
        *,
        cli_variables: Variables | None = None,
        secrets_path: Path | None = None,
        runner: CommandRunner | None = None,
        redact_sources: bool = False,
    ) -> None:
        self.closure = closure
        self.cli_variables = cli_variables
        self.runner = runner
        self.redact_sources = redact_sources
        self.project: Project = modules.flatten(closure)
        self.secrets_path = modules.resolve_secrets_path(
            secrets_path, inventory_dir=closure.root.root
        )
        self.store: SecretStore = (
            load_secret_store(self.secrets_path) if self.secrets_path is not None else SecretStore()
        )
        self._resolved: dict[str, ResolvedTarget] = {}
        self._variables: dict[str, Variables] = {}
        self._fragments: dict[Ref, Fragment] = {}
        self._aggregate_variables: Variables | None = None

    def resolve(self, target_name: str) -> ResolvedTarget:
        """Resolve ``target_name``'s per-output fragment order and layered
        (still-unresolved) variables, memoized for the life of the session."""
        if target_name not in self._resolved:
            self._resolved[target_name] = resolve_target(
                self.project, target_name, cli_variables=self.cli_variables
            )
        return self._resolved[target_name]

    def variables(self, target_name: str) -> Variables:
        """This target's layered variables with every value source resolved
        (secrets looked up, captures run via ``self.runner``), memoized so a
        capture subprocess runs at most once per target for the life of the
        session — shared across every output (per-target or aggregate) that
        consumes it. See README.md "Variable value sources"."""
        if target_name not in self._variables:
            resolved = self.resolve(target_name)
            self._variables[target_name] = sources.resolve_variables(
                resolved.variables,
                secrets=self.store,
                runner=self.runner,
                target=target_name,
                redact=self.redact_sources,
            )
        return self._variables[target_name]

    def aggregate_variables(self) -> Variables:
        """The aggregate scope's source-resolved variables (README.md
        "Variable precedence" — "The aggregate scope"): every module's and the
        inventory's ``defaults.variables`` (closure order), then every
        ``aggregate.variables`` (closure order, later wins), with sources
        resolved. ``target`` is deliberately absent — this scope is not a
        function of any one target — so ``{{ target }}`` in a prologue/
        epilogue fragment raises a strict-undefined :class:`TemplateRenderError`.
        Memoized for the life of the session. Raises
        :class:`~yaml_frag.errors.InventoryError` if either layer defines the
        reserved ``target``/``output`` name.
        """
        if self._aggregate_variables is None:
            layered: Variables = dict(self.project.default_variables)
            layered.update(self.project.aggregate_variables)
            reserved_conflicts = RESERVED_VARIABLE_NAMES & layered.keys()
            if reserved_conflicts:
                raise InventoryError(
                    f"aggregate scope: variable name(s) "
                    f"{', '.join(f'`{name}`' for name in sorted(reserved_conflicts))} "
                    f"are reserved (`target` is undefined in this scope; `output` is "
                    f"set to the aggregate output's own name) and must not be defined "
                    f"in defaults/aggregate variables"
                )
            self._aggregate_variables = sources.resolve_variables(
                layered,
                secrets=self.store,
                runner=self.runner,
                target="<aggregate scope>",
                redact=self.redact_sources,
            )
        return self._aggregate_variables

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
        """
        for required_var in fragment.required_variables:
            if required_var not in variables:
                raise TemplateRenderError(
                    f"{scope_label}: fragment {fragment.name}: missing required "
                    f"variable {required_var!r}"
                )

        for index, op in enumerate(fragment.operations):
            rendered_path = _render_operation_path(
                op.path,
                variables,
                target=scope_label,
                fragment=fragment.name,
                operation_index=index,
            )
            rendered_value = templating.render_value(
                op.value,
                variables,
                target=scope_label,
                fragment=fragment.name,
                operation_index=index,
            )
            rendered_assertion = {
                key: templating.render_value(
                    value,
                    variables,
                    target=scope_label,
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

    def render_target_outputs(self, target_name: str, *, validate: bool = True) -> RenderResult:
        """Render every PER-TARGET output ``target_name`` produces, in memory
        (README.md "Rendering algorithm").

        For each produced output, independently: add the reserved ``output``
        variable to a per-output copy of this target's resolved variables,
        load + validate its referenced fragments, start from an empty
        document, and apply each fragment's operations in order via
        :meth:`_apply_fragment`. Runs generic validation per output unless
        ``validate`` is ``False``.

        Outputs with ``scope: aggregate`` are skipped here even if this
        target contributes fragments to one — they're composed once for the
        whole run by :meth:`render_aggregate`, never per target (README.md
        "Aggregate outputs": ``render TARGET`` silently skips them).
        """
        resolved = self.resolve(target_name)
        variables = self.variables(target_name)

        outputs: dict[str, RenderedOutput] = {}
        for output_name, refs in resolved.output_fragments.items():
            output = self.project.outputs[output_name]
            if output.scope == "aggregate":
                continue

            # `output` is reserved (see inventory.RESERVED_VARIABLE_NAMES) and,
            # unlike `target`, differs per output within the same target, so
            # it's added to a per-output copy here rather than once per target.
            output_variables: Variables = dict(variables)
            output_variables["output"] = output_name

            doc: dict[str, YamlValue] = {}
            tracker = ProvenanceTracker()
            for ref in refs:
                fragment = self._load_fragment(ref)
                self._apply_fragment(
                    doc,
                    tracker,
                    scope_label=target_name,
                    fragment=fragment,
                    variables=output_variables,
                    provenance_target=None,
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
        assertion idiom. Generic validation (unresolved-marker check +
        optional ``output.schema``) still runs on the finished document
        unless ``validate`` is ``False``. Any ``assert`` operation inside a
        fragment contributing to an aggregate output *from a target* only
        ever sees the PARTIAL document built so far (up through the current
        target) — an epilogue fragment is the remedy (README.md "Aggregate
        outputs").
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

        outer_variables: Variables = {}
        if has_outer:
            outer_variables = dict(self.aggregate_variables())
            outer_variables["output"] = output_name

        for ref in spec.prologue if has_outer and spec is not None else ():
            fragment = self._load_fragment(ref)
            self._apply_fragment(
                doc,
                tracker,
                scope_label=f"<aggregate {output_name} prologue>",
                fragment=fragment,
                variables=outer_variables,
                provenance_target=None,
            )

        for target_name in contributing:
            refs = self.resolve(target_name).output_fragments[output_name]
            variables: Variables = dict(self.variables(target_name))
            variables["output"] = output_name

            for ref in refs:
                fragment = self._load_fragment(ref)
                self._apply_fragment(
                    doc,
                    tracker,
                    scope_label=target_name,
                    fragment=fragment,
                    variables=variables,
                    provenance_target=target_name,
                )

        for ref in spec.epilogue if has_outer and spec is not None else ():
            fragment = self._load_fragment(ref)
            self._apply_fragment(
                doc,
                tracker,
                scope_label=f"<aggregate {output_name} epilogue>",
                fragment=fragment,
                variables=outer_variables,
                provenance_target=None,
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
    :func:`yaml_frag.yamlio.dump_str`. If ``output.template`` is set, read that
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
    "redact_variables",
    "render_target",
    "resolve_aggregate_output_path",
    "resolve_output_path",
    "select_validators",
    "write_output",
]
