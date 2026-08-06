"""Command-line interface for ``fragmint``.

See README.md "CLI usage". This module wires up the command structure,
options, and the top-level error-to-exit-code mapping; the work itself lives
in :mod:`modules`, :mod:`render`, :mod:`inventory`, and :mod:`provenance`.

The inventory is the render root (README.md "The module model"): every
command resolves ``--inventory`` to a document, loads its whole import
closure, and (for commands that need it) flattens that closure once. Domain
defaults (input directories, outputs, validators) come from that closure;
the CLI flags below override them per invocation.

Output discipline (README.md): rendered output goes to STDOUT only for
``--stdout``; ALL diagnostics (warnings, errors, progress) go to STDERR.
"""

from __future__ import annotations

import sys
import tempfile
from collections.abc import Collection
from dataclasses import replace
from pathlib import Path
from typing import cast

import click

from . import inventory as inventory_mod
from . import modules as modules_mod
from . import pointer as pointer_mod
from . import render as render_mod
from . import validation as validation_mod
from . import yamlio
from .errors import FragmintError, ModuleError
from .exit_codes import ExitCode
from .models import (
    OutputSpec,
    Project,
    ProvenanceEntry,
    Ref,
    RenderedOutput,
    ResolvedTarget,
    Variables,
    YamlValue,
)
from .modules import Closure, Document


def _parse_var(ctx: click.Context, param: click.Parameter, values: tuple[str, ...]) -> dict[str, str]:
    """Parse repeated ``--var KEY=VALUE`` options into a mapping.

    Raise ``click.BadParameter`` (which maps to ExitCode.USAGE) if any value is
    missing the ``=`` separator. Values are strings here; type coercion happens
    during templating (README.md "Template rendering").
    """
    result: dict[str, str] = {}
    for raw in values:
        if "=" not in raw:
            raise click.BadParameter(
                f"expected KEY=VALUE, got {raw!r}", ctx=ctx, param=param
            )
        key, _, value = raw.partition("=")
        result[key] = value
    return result


def _parse_fragments_dir(
    ctx: click.Context, param: click.Parameter, values: tuple[str, ...]
) -> tuple[Path | None, dict[str, Path]]:
    """Parse ``--fragments-dir``, which takes one of two forms (never mixed):
    a single bare ``DIR`` overriding the root document's fragments directory,
    or one or more repeatable ``NS=DIR`` overriding module ``NS``'s. See
    README.md "The module model"."""
    if not values:
        return None, {}
    qualified = ["=" in value for value in values]
    if any(qualified) and not all(qualified):
        raise click.BadParameter(
            "cannot mix a bare DIR with NS=DIR forms", ctx=ctx, param=param
        )
    if not any(qualified):
        if len(values) > 1:
            raise click.BadParameter(
                "a bare --fragments-dir may only be given once", ctx=ctx, param=param
            )
        return Path(values[0]), {}
    overrides: dict[str, Path] = {}
    for value in values:
        name, _, directory = value.partition("=")
        overrides[name] = Path(directory)
    return None, overrides


def _apply_fragments_dir_overrides(
    closure: Closure, root_override: Path | None, module_overrides: dict[str, Path]
) -> Closure:
    """Apply ``--fragments-dir`` overrides to a loaded closure.

    A bare override replaces the root document's fragments directory;
    ``NS=DIR`` replaces module ``NS``'s — it overrides an already-imported
    module, it does not declare one, so an unknown ``NS`` is an error.
    """
    if root_override is None and not module_overrides:
        return closure

    unknown = set(module_overrides) - set(closure.by_name)
    if unknown:
        raise ModuleError(
            f"--fragments-dir names unknown module(s): {sorted(unknown)}"
        )

    documents: list[Document] = []
    for doc in closure.documents:
        if doc.is_root and root_override is not None:
            doc = replace(doc, fragments_dir=root_override.resolve())
        elif doc.name in module_overrides:
            doc = replace(doc, fragments_dir=module_overrides[doc.name].resolve())
        documents.append(doc)

    by_name = {doc.name: doc for doc in documents if doc.name is not None}
    return Closure(documents=tuple(documents), by_name=by_name)


def _load_closure(
    inventory_path: Path | None,
    fragments_dir_override: tuple[Path | None, dict[str, Path]],
) -> Closure:
    """Resolve ``--inventory`` to a document, load its import closure, and
    apply any ``--fragments-dir`` override. See README.md "The module
    model"."""
    root_path = modules_mod.resolve_inventory_path(inventory_path)
    closure = modules_mod.load_closure(root_path)
    root_override, module_overrides = fragments_dir_override
    return _apply_fragments_dir_overrides(closure, root_override, module_overrides)


def _emit_overrides(output_name: str, overrides: tuple[str, ...], *, quiet: bool) -> None:
    if quiet:
        return
    for warning in overrides:
        click.echo(f"[{output_name}] {warning}", err=True)


def _run_selected_validators(
    project: Project,
    output: OutputSpec,
    output_path: Path,
    requested: tuple[str, ...],
) -> None:
    for name in render_mod.select_validators(project, output, requested):
        spec = project.validators[name]
        validation_mod.run_validator(output_path, spec.command)


def _check_only_output(project: Project, only: str | None) -> None:
    """Raise :class:`ModuleError` if ``--only`` names an output the closure
    doesn't declare."""
    if only is not None and only not in project.outputs:
        raise ModuleError(f"unknown output {only!r} requested")


def _check_only_not_aggregate_for_single_target(
    project: Project, only: str | None, *, instead: str
) -> None:
    """For a single-target command (``render``/``validate``/``explain`` WITH
    a TARGET), ``--only`` naming an aggregate-scoped output is a config
    error — an aggregate output is not a function of one target (README.md
    "CLI usage"/"Aggregate outputs"). Assumes ``only`` has already been
    checked against ``project.outputs`` by :func:`_check_only_output`.

    ``instead`` is the command form to point the user at, so each command
    suggests its own remedy (``render-all`` for ``render``, ``explain``
    without a TARGET for ``explain``) rather than a one-size-fits-all hint.
    """
    if only is not None and project.outputs[only].scope == "aggregate":
        raise ModuleError(
            f"output {only!r} has scope `aggregate`; use `{instead}`"
        )


def _format_provenance_source(entry: ProvenanceEntry) -> str:
    """Format a provenance entry for `explain`'s ``source:``/``contributors:``
    lines, appending a ``(target NAME)`` parenthetical only when the entry
    carries one (aggregate-scoped rendering only; see README.md "Provenance
    and `explain`")."""
    suffix = f" (target {entry.target})" if entry.target is not None else ""
    return f"{entry.fragment} operation {entry.operation_index}{suffix}"


def _explain_section(name: str, rendered: RenderedOutput, *, path: str | None) -> str:
    """Build one ``== name ==`` provenance section for `explain`, shared by
    the per-target and the (TARGET-less, aggregate-scoped) code paths."""

    def _in_scope(doc_path: str) -> bool:
        if path is None:
            return True
        return doc_path == path or doc_path.startswith(path.rstrip("/") + "/")

    blocks: list[str] = []
    for doc_path in sorted(rendered.provenance):
        if not _in_scope(doc_path):
            continue
        entries = rendered.provenance[doc_path]
        _, value = pointer_mod.get(rendered.document, doc_path)
        lines = [doc_path]
        if isinstance(value, list):
            lines.append("  contributors:")
            for entry in entries:
                lines.append(f"    - {_format_provenance_source(entry)}")
        else:
            last = entries[-1]
            lines.append(f"  value: {value}")
            lines.append(f"  source: {_format_provenance_source(last)}")
        blocks.append("\n".join(lines))
    return f"== {name} ==\n\n" + "\n\n".join(blocks)


def _produced_output_names(project: Project, resolved: ResolvedTarget) -> list[str]:
    """The per-target output names :meth:`render_mod.RenderSession.render_target_outputs`
    would produce for this already-resolved target, in order — ``resolved``'s
    ``output_fragments`` keys whose output isn't ``scope: aggregate``.

    Computed from the resolved (but not yet rendered) target so a caller can
    select which single output to act on (``--only``, ``--stdout``,
    ``--output PATH``) BEFORE paying for that output's variable resolution or
    validation — the point of scoping a render to one output (README.md
    "Resolution timing").
    """
    return [
        name
        for name in resolved.output_fragments
        if project.outputs[name].scope != "aggregate"
    ]


def _select_output_names(
    produced: Collection[str],
    only: str | None,
    target: str,
) -> list[str]:
    """Which output names a command should act on.

    ``only`` (already validated against ``project.outputs`` by
    :func:`_check_only_output`) restricts to a single output, which must be one
    the target actually produces. With no ``--only``, act on every output the
    target produces.
    """
    if only is None:
        return list(produced)
    if only not in produced:
        raise ModuleError(f"{target!r} does not produce output {only!r}")
    return [only]


def _select_single_output(
    project: Project,
    produced: Collection[str],
    only: str | None,
    target: str,
    *,
    use_default: bool,
) -> str:
    """Resolve exactly one output name for commands that can only act on one
    at a time (``--stdout``, ``--output PATH``).

    ``only`` takes precedence, but must still name an output the target
    actually produces — the same requirement :func:`_select_output_names`
    enforces, with the same message. Being declared in the closure (all
    :func:`_check_only_output` checks) is weaker than being produced by *this*
    target, and callers index the render result by the returned name, so
    returning an unproduced name here would surface as a ``KeyError`` rather
    than a diagnostic.

    Otherwise, a target that produced exactly one output is unambiguous. With
    more than one produced output and no ``--only``: fall back to
    ``project.default_output`` when ``use_default`` is set (used by
    ``--stdout``); otherwise (``--output PATH``) require ``--only``
    explicitly. Raise :class:`ModuleError` naming the available outputs when
    none of the above resolves it.

    ``produced`` may be EMPTY: aggregate-scoped outputs are excluded from
    per-target rendering (README.md "Aggregate outputs"), so a target whose
    only inventory routing is into an aggregate output produces no per-target
    output at all. That case gets its own message — checked before ``only``,
    since "routes nothing into a per-target output" explains the failure
    better than naming one output that is missing — as the "produces multiple
    outputs" one below would otherwise report an empty list of them.
    """
    if not produced:
        raise ModuleError(
            f"{target!r} produces no per-target outputs; its inventory routing "
            f"only feeds aggregate output(s), which are rendered for the whole "
            f"run — use `render-all`/`validate-all` (optionally with --only NAME)"
        )
    if only is not None:
        if only not in produced:
            raise ModuleError(f"{target!r} does not produce output {only!r}")
        return only
    if len(produced) == 1:
        return next(iter(produced))
    if use_default and project.default_output is not None and project.default_output in produced:
        return project.default_output
    available = ", ".join(sorted(produced))
    hint = " (or mark one output `default: true`)" if use_default else ""
    raise ModuleError(
        f"{target!r} produces multiple outputs ({available}); specify --only NAME{hint}"
    )


# Shared option decorators. Path defaults are None so the resolved value comes
# from the closure unless the flag is given.
_inventory_option = click.option(
    "--inventory",
    "inventory_path",
    type=click.Path(path_type=Path),
    default=None,
    help="Inventory file or directory (a directory resolves to DIR/targets.yaml). Defaults to '.'.",
)
_fragments_option = click.option(
    "--fragments-dir",
    "fragments_dir_override",
    multiple=True,
    metavar="[NS=]DIR",
    callback=_parse_fragments_dir,
    help="Override a fragments directory: a bare DIR overrides the root's; "
    "repeatable NS=DIR overrides module NS's. The two forms cannot be mixed.",
)
_secrets_option = click.option(
    "--secrets",
    "secrets_path",
    type=click.Path(path_type=Path),
    default=None,
    help="Untracked secrets overlay file. Defaults to the inventory's "
    "sibling secrets.yaml when that exists.",
)
_var_option = click.option(
    "--var",
    "cli_variables",
    multiple=True,
    callback=_parse_var,
    metavar="KEY=VALUE",
    help="Override a variable. May be repeated.",
)
_validator_option = click.option(
    "--validator",
    "validators",
    multiple=True,
    metavar="NAME",
    help="Run a named validator from the closure. May be repeated. "
    "If omitted, the output's default validators run.",
)
_only_option = click.option(
    "--only",
    "only_output",
    default=None,
    metavar="NAME",
    help="Scope to a single named output (see the closure's `outputs`).",
)


@click.group()
@click.version_option()
def cli() -> None:
    """Compose structured YAML documents from an inventory and ordered fragments."""


@cli.command()
@click.argument("target")
@_inventory_option
@_fragments_option
@_secrets_option
@_var_option
@_validator_option
@_only_option
@click.option("--output", "output_path", type=click.Path(path_type=Path), default=None, help="Override the destination path. Requires --only or a target producing exactly one output.")
@click.option("--stdout", "to_stdout", is_flag=True, help="Print one rendered output to stdout instead of writing (see --only).")
@click.option("--dry-run", is_flag=True, help="Render and validate but write nothing.")
@click.option("--quiet-overrides", is_flag=True, help="Suppress override warnings.")
@click.option("--no-validate", "validate", is_flag=True, default=True, flag_value=False, help="Skip generic validation and validators.")
def render(
    target: str,
    inventory_path: Path | None,
    fragments_dir_override: tuple[Path | None, dict[str, Path]],
    secrets_path: Path | None,
    cli_variables: dict[str, str],
    validators: tuple[str, ...],
    only_output: str | None,
    output_path: Path | None,
    to_stdout: bool,
    dry_run: bool,
    quiet_overrides: bool,
    validate: bool,
) -> None:
    """Render one TARGET, writing every output it produces to its configured path.

    Aggregate-scoped outputs (README.md "Aggregate outputs") are silently
    skipped — they're not a function of one target. ``--only`` naming one is
    a config error pointing at ``render-all --only NAME`` instead.
    """
    closure = _load_closure(inventory_path, fragments_dir_override)
    session = render_mod.RenderSession(
        closure,
        cli_variables=cast(Variables, cli_variables),
        secrets_path=secrets_path,
    )
    _check_only_output(session.project, only_output)
    _check_only_not_aggregate_for_single_target(
        session.project, only_output, instead=f"render-all --only {only_output}"
    )

    produced = _produced_output_names(session.project, session.resolve(target))

    if to_stdout:
        name = _select_single_output(session.project, produced, only_output, target, use_default=True)
        result = session.render_target_outputs(target, only=name, validate=validate)
        rendered = result.outputs[name]
        _emit_overrides(name, rendered.overrides, quiet=quiet_overrides)
        text = render_mod.compose_output(rendered, session.project.outputs[name])
        click.echo(text, nl=False)
        return

    selected_names = _select_output_names(produced, only_output, target)
    if output_path is not None:
        selected_names = [
            _select_single_output(session.project, produced, only_output, target, use_default=False)
        ]

    result = session.render_target_outputs(
        target,
        only=selected_names[0] if len(selected_names) == 1 else None,
        validate=validate,
    )

    for name in selected_names:
        _emit_overrides(name, result.outputs[name].overrides, quiet=quiet_overrides)

    if dry_run:
        return

    for name in selected_names:
        rendered = result.outputs[name]
        output_spec = session.project.outputs[name]
        text = render_mod.compose_output(rendered, output_spec)
        out_path = render_mod.resolve_output_path(output_spec, target, override=output_path)
        render_mod.write_output(text, out_path)

        if validate:
            _run_selected_validators(session.project, output_spec, out_path, validators)


def _select_run_scope(project: Project, only: str | None) -> tuple[bool, list[str]]:
    """Decide, for `render-all`/`validate-all`, which per-target outputs to
    render and which aggregate outputs to compose for the whole run.

    Returns ``(render_per_target, aggregate_names)``:
    - with no ``--only``: every `scope: target` output is rendered per
      target, and every `scope: aggregate` output is composed once;
    - with ``--only NAME`` naming a ``scope: target`` output: only that
      output is rendered per target, and no aggregate output runs;
    - with ``--only NAME`` naming a ``scope: aggregate`` output: no per-target
      rendering happens at all, and only that aggregate output is composed.

    See README.md "Aggregate outputs" / "CLI usage".
    """
    if only is None:
        aggregate_names = [name for name, spec in project.outputs.items() if spec.scope == "aggregate"]
        return True, aggregate_names
    if project.outputs[only].scope == "aggregate":
        return False, [only]
    return True, []


@cli.command("render-all")
@_inventory_option
@_fragments_option
@_secrets_option
@_validator_option
@_only_option
@click.option(
    "--output",
    "output_path",
    type=click.Path(path_type=Path),
    default=None,
    help="Override the destination path. Only valid with --only naming an aggregate output.",
)
@click.option(
    "--stdout",
    "to_stdout",
    is_flag=True,
    help="Print the aggregate output to stdout instead of writing. Only valid with --only naming an aggregate output.",
)
@click.option("--quiet-overrides", is_flag=True)
@click.option("--no-validate", "validate", is_flag=True, default=True, flag_value=False)
def render_all(
    inventory_path: Path | None,
    fragments_dir_override: tuple[Path | None, dict[str, Path]],
    secrets_path: Path | None,
    validators: tuple[str, ...],
    only_output: str | None,
    output_path: Path | None,
    to_stdout: bool,
    quiet_overrides: bool,
    validate: bool,
) -> None:
    """Render every target in inventory order, then every aggregate output
    once; nonzero if any target fails.

    Do not leave a partial final output file for a failed target (atomic
    writes; README.md "CLI usage"). If any target fails, aggregate outputs
    are skipped entirely rather than writing a document built from only the
    targets that happened to succeed (README.md "Aggregate outputs").
    ``--only NAME`` scopes the run to a single output, of either scope — the
    iteration loop for authoring an aggregate output without re-rendering
    every target-scoped output too.
    """
    closure = _load_closure(inventory_path, fragments_dir_override)
    session = render_mod.RenderSession(closure, secrets_path=secrets_path)
    project = session.project
    _check_only_output(project, only_output)
    render_per_target, aggregate_names = _select_run_scope(project, only_output)

    if (output_path is not None or to_stdout) and (
        only_output is None or project.outputs[only_output].scope != "aggregate"
    ):
        raise ModuleError(
            "--output/--stdout on render-all require --only naming an aggregate output"
        )

    failed: list[str] = []
    if render_per_target:
        for target_name in project.targets:
            try:
                result = session.render_target_outputs(
                    target_name, only=only_output, validate=validate
                )
                names = [only_output] if only_output is not None else list(result.outputs)
                for name in names:
                    if name not in result.outputs:
                        continue
                    rendered = result.outputs[name]
                    _emit_overrides(name, rendered.overrides, quiet=quiet_overrides)
                    output_spec = project.outputs[name]
                    text = render_mod.compose_output(rendered, output_spec)
                    out_path = render_mod.resolve_output_path(output_spec, target_name)
                    render_mod.write_output(text, out_path)
                    if validate:
                        _run_selected_validators(project, output_spec, out_path, validators)
            except FragmintError as exc:
                click.echo(f"{target_name}: {exc}", err=True)
                failed.append(target_name)

    if aggregate_names:
        if failed:
            click.echo(
                f"render-all: skipping aggregate output(s) "
                f"{', '.join(aggregate_names)} because {len(failed)} target(s) "
                f"failed: {', '.join(failed)}",
                err=True,
            )
        else:
            for name in aggregate_names:
                aggregate_rendered = session.render_aggregate(name, validate=validate)
                if aggregate_rendered is None:
                    continue
                _emit_overrides(name, aggregate_rendered.overrides, quiet=quiet_overrides)
                output_spec = project.outputs[name]
                text = render_mod.compose_output(aggregate_rendered, output_spec)
                if to_stdout:
                    click.echo(text, nl=False)
                    continue
                out_path = render_mod.resolve_aggregate_output_path(output_spec, override=output_path)
                render_mod.write_output(text, out_path)
                if validate:
                    _run_selected_validators(project, output_spec, out_path, validators)

    if failed:
        raise FragmintError(
            f"render-all: {len(failed)} target(s) failed: {', '.join(failed)}"
        )


@cli.command()
@click.argument("target")
@_inventory_option
@_fragments_option
@_secrets_option
@_var_option
@_validator_option
@_only_option
def validate(
    target: str,
    inventory_path: Path | None,
    fragments_dir_override: tuple[Path | None, dict[str, Path]],
    secrets_path: Path | None,
    cli_variables: dict[str, str],
    validators: tuple[str, ...],
    only_output: str | None,
) -> None:
    """Render TARGET in memory and validate every output it produces, without
    writing anything.

    Aggregate-scoped outputs are silently skipped, same as ``render``; use
    ``validate-all --only NAME`` for one instead.
    """
    closure = _load_closure(inventory_path, fragments_dir_override)
    session = render_mod.RenderSession(
        closure,
        cli_variables=cast(Variables, cli_variables),
        secrets_path=secrets_path,
    )
    project = session.project
    _check_only_output(project, only_output)
    _check_only_not_aggregate_for_single_target(
        project, only_output, instead=f"validate-all --only {only_output}"
    )

    produced = _produced_output_names(project, session.resolve(target))
    selected_names = _select_output_names(produced, only_output, target)

    result = session.render_target_outputs(
        target,
        only=selected_names[0] if len(selected_names) == 1 else None,
        validate=True,
    )

    for name in selected_names:
        rendered = result.outputs[name]
        output_spec = project.outputs[name]
        text = render_mod.compose_output(rendered, output_spec)

        selected = render_mod.select_validators(project, output_spec, validators)
        if selected:
            with tempfile.TemporaryDirectory() as tmp_dir:
                tmp_path = Path(tmp_dir) / "output"
                render_mod.write_output(text, tmp_path)
                for validator_name in selected:
                    spec = project.validators[validator_name]
                    validation_mod.run_validator(tmp_path, spec.command)


def _validate_rendered(
    project: Project, name: str, rendered: RenderedOutput, requested: tuple[str, ...]
) -> None:
    output_spec = project.outputs[name]
    text = render_mod.compose_output(rendered, output_spec)
    selected = render_mod.select_validators(project, output_spec, requested)
    if selected:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir) / "output"
            render_mod.write_output(text, tmp_path)
            for validator_name in selected:
                spec = project.validators[validator_name]
                validation_mod.run_validator(tmp_path, spec.command)


@cli.command("validate-all")
@_inventory_option
@_fragments_option
@_secrets_option
@_validator_option
@_only_option
def validate_all(
    inventory_path: Path | None,
    fragments_dir_override: tuple[Path | None, dict[str, Path]],
    secrets_path: Path | None,
    validators: tuple[str, ...],
    only_output: str | None,
) -> None:
    """Validate every target in inventory order, then every aggregate
    output once; nonzero if any target fails.

    Mirrors ``render-all``: if any target fails, aggregate outputs are
    skipped entirely (README.md "Aggregate outputs"). ``--only NAME`` scopes
    the run to a single output, of either scope.
    """
    closure = _load_closure(inventory_path, fragments_dir_override)
    session = render_mod.RenderSession(closure, secrets_path=secrets_path)
    project = session.project
    _check_only_output(project, only_output)
    render_per_target, aggregate_names = _select_run_scope(project, only_output)

    failed: list[str] = []
    if render_per_target:
        for target_name in project.targets:
            try:
                result = session.render_target_outputs(
                    target_name, only=only_output, validate=True
                )
                names = [only_output] if only_output is not None else list(result.outputs)
                for name in names:
                    if name not in result.outputs:
                        continue
                    _validate_rendered(project, name, result.outputs[name], validators)
            except FragmintError as exc:
                click.echo(f"{target_name}: {exc}", err=True)
                failed.append(target_name)

    if aggregate_names:
        if failed:
            click.echo(
                f"validate-all: skipping aggregate output(s) "
                f"{', '.join(aggregate_names)} because {len(failed)} target(s) "
                f"failed: {', '.join(failed)}",
                err=True,
            )
        else:
            for name in aggregate_names:
                rendered = session.render_aggregate(name, validate=True)
                if rendered is None:
                    continue
                _validate_rendered(project, name, rendered, validators)

    if failed:
        raise FragmintError(
            f"validate-all: {len(failed)} target(s) failed: {', '.join(failed)}"
        )


@cli.command()
@click.argument("target", required=False)
@click.option("--path", "path", default=None, metavar="POINTER", help="Scope provenance to one JSON Pointer path and its descendants.")
@_inventory_option
@_fragments_option
@_secrets_option
@_var_option
@_only_option
def explain(
    target: str | None,
    path: str | None,
    inventory_path: Path | None,
    fragments_dir_override: tuple[Path | None, dict[str, Path]],
    secrets_path: Path | None,
    cli_variables: dict[str, str],
    only_output: str | None,
) -> None:
    """Show provenance for TARGET, optionally scoped to a single --path.

    Prints one section per output the target produces (headed by the output
    name); use ``--only`` to scope to a single output. TARGET may be omitted
    only when ``--only`` names an aggregate-scoped output (README.md
    "Aggregate outputs") — naming TARGET together with an aggregate
    ``--only`` is the same config error as ``render TARGET --only
    <aggregate>``. ``--path`` is a flag, independent of TARGET: it is
    accepted, and scopes provenance the same way, whether or not TARGET is
    given — including in the TARGET-less aggregate case, where it scopes the
    composed aggregate document's provenance. See README.md "Provenance and
    `explain`" for the expected output format.
    """
    closure = _load_closure(inventory_path, fragments_dir_override)
    session = render_mod.RenderSession(
        closure,
        cli_variables=cast(Variables, cli_variables),
        secrets_path=secrets_path,
        redact_sources=True,
        apply_assertions=False,
    )
    project = session.project
    _check_only_output(project, only_output)

    if target is None:
        if only_output is None or project.outputs[only_output].scope != "aggregate":
            raise ModuleError(
                "TARGET is required unless --only names an aggregate output"
            )
        rendered = session.render_aggregate(only_output, validate=False)
        if rendered is None:
            # No target routes anything into this output, so it is not
            # produced at all (README.md "Aggregate outputs") and there is no
            # provenance to show. A note on stderr rather than empty stdout,
            # so the absence is visible when stdout is piped.
            click.echo(
                f"no target contributes to output {only_output!r}; nothing to explain",
                err=True,
            )
            return
        click.echo(_explain_section(only_output, rendered, path=path))
        return

    _check_only_not_aggregate_for_single_target(
        project, only_output, instead=f"explain --only {only_output} (without a TARGET)"
    )

    produced = _produced_output_names(project, session.resolve(target))
    selected_names = _select_output_names(produced, only_output, target)

    # Redact sources so explain never executes captures or reveals secrets
    # (README.md "Resolution timing"); secret/capture-derived values appear as
    # non-executing placeholders in the provenance output. `validate=False`
    # skips generic structural validation only; `apply_assertions=False` on
    # the session (set above) is what lets a failing `assert` not defeat this
    # report (README.md "Provenance and `explain`").
    result = session.render_target_outputs(
        target,
        only=selected_names[0] if len(selected_names) == 1 else None,
        validate=False,
    )

    sections = [
        _explain_section(name, result.outputs[name], path=path) for name in selected_names
    ]
    click.echo("\n\n".join(sections))


@cli.group("list")
def list_group() -> None:
    """List targets, fragments, groups, outputs, or modules."""


@list_group.command("targets")
@_inventory_option
@_fragments_option
def list_targets(inventory_path: Path | None, fragments_dir_override: tuple[Path | None, dict[str, Path]]) -> None:
    """List every target name, in inventory declaration order."""
    closure = _load_closure(inventory_path, fragments_dir_override)
    for name in closure.root.targets:
        click.echo(name)


@list_group.command("groups")
@_inventory_option
@_fragments_option
def list_groups(inventory_path: Path | None, fragments_dir_override: tuple[Path | None, dict[str, Path]]) -> None:
    """List every group name defined anywhere in the closure."""
    closure = _load_closure(inventory_path, fragments_dir_override)
    project = modules_mod.flatten(closure)
    for name in project.groups:
        click.echo(name)


@list_group.command("outputs")
@_inventory_option
@_fragments_option
def list_outputs(inventory_path: Path | None, fragments_dir_override: tuple[Path | None, dict[str, Path]]) -> None:
    """List every output name, its scope, and the document that defines it."""
    closure = _load_closure(inventory_path, fragments_dir_override)
    project = modules_mod.flatten(closure)
    for name, spec in project.outputs.items():
        defining = next(doc for doc in closure.documents if name in doc.outputs)
        defined_by = "inventory" if defining.is_root else cast(str, defining.name)
        click.echo(f"{name}\t{spec.scope}\t{defined_by}")


@list_group.command("modules")
@_inventory_option
@_fragments_option
def list_modules(inventory_path: Path | None, fragments_dir_override: tuple[Path | None, dict[str, Path]]) -> None:
    """List every module in the closure — name, path, and its first importer."""
    closure = _load_closure(inventory_path, fragments_dir_override)
    for doc in closure.documents:
        if doc.is_root:
            continue
        importer = next(
            (candidate for candidate in closure.documents if doc.root in candidate.imports.values()),
            None,
        )
        importer_label = "inventory" if importer is None or importer.is_root else cast(str, importer.name)
        click.echo(f"{doc.name}\t{doc.root}\t{importer_label}")


@list_group.command("fragments")
@_inventory_option
@_fragments_option
def list_fragments(inventory_path: Path | None, fragments_dir_override: tuple[Path | None, dict[str, Path]]) -> None:
    """List every fragment reference in the closure, by document, qualified."""
    closure = _load_closure(inventory_path, fragments_dir_override)
    for doc in closure.documents:
        if doc.fragments_dir is None:
            continue
        names = sorted(
            str(p.relative_to(doc.fragments_dir).with_suffix("")).replace("\\", "/")
            for p in doc.fragments_dir.rglob("*.yaml")
        )
        for name in names:
            click.echo(modules_mod.display_ref(Ref(module=doc.name, path=name)))


def _declared_by_label(name: str | None) -> str:
    """The label `inspect --aggregate` prints for an entry's declaring
    document -- ``"inventory"`` for the root, else its module name. Matches
    the convention `list outputs`/`list modules` already use for the same
    root-vs-module distinction."""
    return "inventory" if name is None else name


def _check_only_is_aggregate(project: Project, only: str | None) -> None:
    """For ``inspect --aggregate``, ``--only`` must name a ``scope:
    aggregate`` output -- a ``scope: target`` output has no prologue/epilogue
    to show. Assumes ``only`` has already been checked against
    ``project.outputs`` by :func:`_check_only_output`."""
    if only is not None and project.outputs[only].scope != "aggregate":
        raise ModuleError(
            f"output {only!r} has scope `target`; `inspect --aggregate` covers "
            f"only `scope: aggregate` outputs"
        )


@cli.command()
@click.argument("target", required=False)
@_inventory_option
@_fragments_option
@_secrets_option
@_var_option
@_only_option
@click.option(
    "--aggregate",
    "aggregate_mode",
    is_flag=True,
    help="Show the aggregate scope instead of one target: effective prologue/"
    "epilogue order (with each entry's declaring document) and the "
    "(redacted) aggregate variable layer. TARGET must be omitted.",
)
@click.option("--show-secrets", is_flag=True, help="Do not redact secret-looking values (unsafe).")
def inspect(
    target: str | None,
    inventory_path: Path | None,
    fragments_dir_override: tuple[Path | None, dict[str, Path]],
    secrets_path: Path | None,
    cli_variables: dict[str, str],
    only_output: str | None,
    aggregate_mode: bool,
    show_secrets: bool,
) -> None:
    """Show resolved groups, per-output fragment order (qualified refs), and
    (redacted) variables for TARGET.

    ``--aggregate`` switches to the target-less aggregate scope instead:
    every aggregate output's effective prologue/epilogue order -- the
    closure-order concatenation across every document that contributes one,
    not derivable by reading any single document -- each entry naming its
    declaring document, plus the (redacted) aggregate variable layer.
    TARGET and ``--aggregate`` are mutually exclusive; ``--var`` has no effect
    in the aggregate scope (README.md "The aggregate scope") and is rejected
    rather than silently ignored. See README.md "CLI usage".
    """
    if aggregate_mode:
        if target is not None:
            raise ModuleError("TARGET and --aggregate cannot be combined")
        if cli_variables:
            raise ModuleError(
                "--var has no effect with --aggregate: group, target, and "
                "CLI-override variables are not part of the aggregate scope "
                "(README.md \"The aggregate scope\")"
            )
    elif target is None:
        raise ModuleError("TARGET is required unless --aggregate is given")
    elif only_output is not None:
        raise ModuleError("--only requires --aggregate; per-target inspect always shows every output")

    closure = _load_closure(inventory_path, fragments_dir_override)
    project = modules_mod.flatten(closure)
    _check_only_output(project, only_output)

    if aggregate_mode:
        _check_only_is_aggregate(project, only_output)
        details = modules_mod.aggregate_fragment_details(closure, project.outputs)
        if only_output is not None:
            details = {only_output: details[only_output]}

        def _entries_doc(entries: tuple[modules_mod.AggregateFragmentEntry, ...]) -> YamlValue:
            return cast(
                YamlValue,
                [
                    {
                        "ref": modules_mod.display_ref(entry.ref),
                        "declared_by": _declared_by_label(entry.declared_by),
                    }
                    for entry in entries
                ],
            )

        redacted_variables = render_mod.redact_variables(
            render_mod.layered_aggregate_variables(project), show_secrets=show_secrets
        )
        aggregate_doc: dict[str, YamlValue] = {
            "scope": "aggregate",
            "outputs": cast(
                YamlValue,
                {
                    name: {
                        "prologue": _entries_doc(prologue),
                        "epilogue": _entries_doc(epilogue),
                    }
                    for name, (prologue, epilogue) in details.items()
                },
            ),
            "variables": cast(YamlValue, redacted_variables),
        }
        click.echo(yamlio.dump_str(aggregate_doc), nl=False)
        return

    resolved = inventory_mod.resolve_target(
        project, cast(str, target), cli_variables=cast(Variables, cli_variables)
    )

    redacted_variables = render_mod.redact_variables(
        resolved.variables, show_secrets=show_secrets
    )
    output_doc: dict[str, YamlValue] = {
        "target": resolved.name,
        "groups": list(resolved.groups),
        "outputs": cast(
            YamlValue,
            {
                name: {"fragments": [modules_mod.display_ref(ref) for ref in fragments]}
                for name, fragments in resolved.output_fragments.items()
            },
        ),
        "variables": cast(YamlValue, redacted_variables),
    }
    click.echo(yamlio.dump_str(output_doc), nl=False)


def main(argv: list[str] | None = None) -> int:
    """Entry point. Run the CLI and translate errors into stable exit codes.

    Catches :class:`~fragmint.errors.FragmintError`, prints its message to
    STDERR, and returns ``exc.exit_code``. Click usage errors map to
    :data:`ExitCode.USAGE`. Returns the process exit code (never raises for
    known failures). See README.md "Exit codes".
    """
    try:
        cli.main(args=argv, standalone_mode=False)
    except FragmintError as exc:
        click.echo(str(exc), err=True)
        return int(exc.exit_code)
    except click.UsageError as exc:
        exc.show(file=sys.stderr)
        return int(ExitCode.USAGE)
    except click.ClickException as exc:
        exc.show(file=sys.stderr)
        return int(ExitCode.USAGE)
    except click.exceptions.Exit as exc:  # --help / --version
        return int(exc.exit_code)
    return int(ExitCode.SUCCESS)


if __name__ == "__main__":
    sys.exit(main())
