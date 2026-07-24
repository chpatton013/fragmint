"""Command-line interface for ``yaml-frag``.

See README.md "CLI usage". This module wires up the command
structure, options, and the top-level error-to-exit-code mapping. Command
bodies are stubs to be implemented against :mod:`config`, :mod:`render`,
:mod:`inventory`, and :mod:`provenance`.

Domain defaults (inventory/fragments locations, output path/template,
validators) come from the project configuration (:mod:`config`); the CLI flags
below override those defaults per invocation.

Output discipline (README.md): rendered output goes to STDOUT only for
``--stdout``; ALL diagnostics (warnings, errors, progress) go to STDERR.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import cast

import click

from . import config as config_mod
from . import inventory as inventory_mod
from . import pointer as pointer_mod
from . import render as render_mod
from . import validation as validation_mod
from . import yamlio
from .config import DEFAULT_CONFIG_PATH
from .errors import ConfigError, YamlFragError
from .exit_codes import ExitCode
from .models import OutputSpec, ProjectConfig, RenderedOutput, Variables, YamlValue


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


def _resolve_inputs(
    config: ProjectConfig,
    inventory_path: Path | None,
    fragments_dir: Path | None,
) -> tuple[Path, Path]:
    """Apply CLI overrides over the project-config defaults for inventory
    and fragments-dir locations (README.md "CLI usage")."""
    resolved_inventory = inventory_path if inventory_path is not None else Path(config.inventory)
    resolved_fragments = fragments_dir if fragments_dir is not None else Path(config.fragments_dir)
    return resolved_inventory, resolved_fragments


def _emit_overrides(output_name: str, overrides: tuple[str, ...], *, quiet: bool) -> None:
    if quiet:
        return
    for warning in overrides:
        click.echo(f"[{output_name}] {warning}", err=True)


def _run_selected_validators(
    config: ProjectConfig,
    output: OutputSpec,
    output_path: Path,
    requested: tuple[str, ...],
) -> None:
    for name in config_mod.select_validators(config, output, requested):
        spec = config.validators[name]
        validation_mod.run_validator(output_path, spec.command)


def _check_only_output(config: ProjectConfig, only: str | None) -> None:
    """Raise :class:`ConfigError` if ``--only`` names an output the project
    configuration doesn't declare."""
    if only is not None and only not in config.outputs:
        raise ConfigError(f"unknown output {only!r} requested")


def _select_output_names(
    produced: dict[str, RenderedOutput],
    only: str | None,
    target: str,
) -> list[str]:
    """Which output names a command should act on.

    ``only`` (already validated against ``config.outputs`` by
    :func:`_check_only_output`) restricts to a single output, which must be one
    the target actually produced. With no ``--only``, act on every output the
    target produced.
    """
    if only is None:
        return list(produced)
    if only not in produced:
        raise ConfigError(f"{target!r} does not produce output {only!r}")
    return [only]


def _select_single_output(
    config: ProjectConfig,
    produced: dict[str, RenderedOutput],
    only: str | None,
    target: str,
    *,
    use_default: bool,
) -> str:
    """Resolve exactly one output name for commands that can only act on one
    at a time (``--stdout``, ``--output PATH``).

    ``only`` takes precedence. Otherwise, a target that produced exactly one
    output is unambiguous. With more than one produced output and no
    ``--only``: fall back to ``config.default_output`` when ``use_default`` is
    set (used by ``--stdout``); otherwise (``--output PATH``) require
    ``--only`` explicitly. Raise :class:`ConfigError` naming the available
    outputs when none of the above resolves it.
    """
    if only is not None:
        return only
    if len(produced) == 1:
        return next(iter(produced))
    if use_default and config.default_output is not None and config.default_output in produced:
        return config.default_output
    available = ", ".join(sorted(produced))
    hint = " (or mark one output `default: true`)" if use_default else ""
    raise ConfigError(
        f"{target!r} produces multiple outputs ({available}); specify --only NAME{hint}"
    )


# Shared option decorators. Path defaults are None so the resolved value comes
# from the project config unless the flag is given.
_config_option = click.option(
    "--config",
    "config_path",
    type=click.Path(path_type=Path),
    default=DEFAULT_CONFIG_PATH,
    show_default=True,
    help="Project configuration file.",
)
_inventory_option = click.option(
    "--inventory",
    "inventory_path",
    type=click.Path(path_type=Path),
    default=None,
    help="Inventory YAML file (overrides project config).",
)
_fragments_option = click.option(
    "--fragments-dir",
    type=click.Path(path_type=Path),
    default=None,
    help="Directory containing fragment files (overrides project config).",
)
_secrets_option = click.option(
    "--secrets",
    "secrets_path",
    type=click.Path(path_type=Path),
    default=None,
    help="Optional untracked secrets overlay file.",
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
    help="Run a named validator from the project config. May be repeated. "
    "If omitted, the output's default validators run.",
)
_only_option = click.option(
    "--only",
    "only_output",
    default=None,
    metavar="NAME",
    help="Scope to a single named output (see project config `outputs`).",
)


@click.group()
@click.version_option()
def cli() -> None:
    """Compose structured YAML documents from an inventory and ordered fragments."""


@cli.command()
@click.argument("target")
@_config_option
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
    config_path: Path,
    inventory_path: Path | None,
    fragments_dir: Path | None,
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
    """Render one TARGET, writing every output it produces to its configured path."""
    cfg = config_mod.load_config(config_path)
    inv_path, frags_dir = _resolve_inputs(cfg, inventory_path, fragments_dir)
    _check_only_output(cfg, only_output)

    result = render_mod.render_target(
        target,
        config=cfg,
        inventory_path=inv_path,
        fragments_dir=frags_dir,
        cli_variables=cast(Variables, cli_variables),
        secrets_path=secrets_path,
        validate=validate,
    )

    if to_stdout:
        name = _select_single_output(cfg, result.outputs, only_output, target, use_default=True)
        rendered = result.outputs[name]
        _emit_overrides(name, rendered.overrides, quiet=quiet_overrides)
        text = render_mod.compose_output(rendered, cfg.outputs[name])
        click.echo(text, nl=False)
        return

    selected_names = _select_output_names(result.outputs, only_output, target)
    if output_path is not None:
        selected_names = [
            _select_single_output(cfg, result.outputs, only_output, target, use_default=False)
        ]

    for name in selected_names:
        _emit_overrides(name, result.outputs[name].overrides, quiet=quiet_overrides)

    if dry_run:
        return

    for name in selected_names:
        rendered = result.outputs[name]
        output_spec = cfg.outputs[name]
        text = render_mod.compose_output(rendered, output_spec)
        out_path = config_mod.resolve_output_path(output_spec, target, override=output_path)
        render_mod.write_output(text, out_path)

        if validate:
            _run_selected_validators(cfg, output_spec, out_path, validators)


@cli.command("render-all")
@_config_option
@_inventory_option
@_fragments_option
@_secrets_option
@_validator_option
@click.option("--quiet-overrides", is_flag=True)
@click.option("--no-validate", "validate", is_flag=True, default=True, flag_value=False)
def render_all(
    config_path: Path,
    inventory_path: Path | None,
    fragments_dir: Path | None,
    secrets_path: Path | None,
    validators: tuple[str, ...],
    quiet_overrides: bool,
    validate: bool,
) -> None:
    """Render every target in inventory order; nonzero if any fails.

    Do not leave a partial final output file for a failed target (atomic
    writes; README.md "CLI usage").
    """
    cfg = config_mod.load_config(config_path)
    inv_path, frags_dir = _resolve_inputs(cfg, inventory_path, fragments_dir)
    inv = inventory_mod.load_inventory(inv_path)

    failed: list[str] = []
    for target_name in inv.targets:
        try:
            result = render_mod.render_target(
                target_name,
                config=cfg,
                inventory_path=inv_path,
                fragments_dir=frags_dir,
                secrets_path=secrets_path,
                validate=validate,
            )
            for name, rendered in result.outputs.items():
                _emit_overrides(name, rendered.overrides, quiet=quiet_overrides)
                output_spec = cfg.outputs[name]
                text = render_mod.compose_output(rendered, output_spec)
                out_path = config_mod.resolve_output_path(output_spec, target_name)
                render_mod.write_output(text, out_path)
                if validate:
                    _run_selected_validators(cfg, output_spec, out_path, validators)
        except YamlFragError as exc:
            click.echo(f"{target_name}: {exc}", err=True)
            failed.append(target_name)

    if failed:
        raise YamlFragError(
            f"render-all: {len(failed)} target(s) failed: {', '.join(failed)}"
        )


@cli.command()
@click.argument("target")
@_config_option
@_inventory_option
@_fragments_option
@_secrets_option
@_var_option
@_validator_option
@_only_option
def validate(
    target: str,
    config_path: Path,
    inventory_path: Path | None,
    fragments_dir: Path | None,
    secrets_path: Path | None,
    cli_variables: dict[str, str],
    validators: tuple[str, ...],
    only_output: str | None,
) -> None:
    """Render TARGET in memory and validate every output it produces, without
    writing anything."""
    cfg = config_mod.load_config(config_path)
    inv_path, frags_dir = _resolve_inputs(cfg, inventory_path, fragments_dir)
    _check_only_output(cfg, only_output)

    result = render_mod.render_target(
        target,
        config=cfg,
        inventory_path=inv_path,
        fragments_dir=frags_dir,
        cli_variables=cast(Variables, cli_variables),
        secrets_path=secrets_path,
        validate=True,
    )

    for name in _select_output_names(result.outputs, only_output, target):
        rendered = result.outputs[name]
        output_spec = cfg.outputs[name]
        text = render_mod.compose_output(rendered, output_spec)

        selected = config_mod.select_validators(cfg, output_spec, validators)
        if selected:
            with tempfile.TemporaryDirectory() as tmp_dir:
                tmp_path = Path(tmp_dir) / "output"
                render_mod.write_output(text, tmp_path)
                for validator_name in selected:
                    spec = cfg.validators[validator_name]
                    validation_mod.run_validator(tmp_path, spec.command)


@cli.command("validate-all")
@_config_option
@_inventory_option
@_fragments_option
@_secrets_option
@_validator_option
def validate_all(
    config_path: Path,
    inventory_path: Path | None,
    fragments_dir: Path | None,
    secrets_path: Path | None,
    validators: tuple[str, ...],
) -> None:
    """Validate every target in inventory order; nonzero if any fails."""
    cfg = config_mod.load_config(config_path)
    inv_path, frags_dir = _resolve_inputs(cfg, inventory_path, fragments_dir)
    inv = inventory_mod.load_inventory(inv_path)

    failed: list[str] = []
    for target_name in inv.targets:
        try:
            result = render_mod.render_target(
                target_name,
                config=cfg,
                inventory_path=inv_path,
                fragments_dir=frags_dir,
                secrets_path=secrets_path,
                validate=True,
            )
            for name, rendered in result.outputs.items():
                output_spec = cfg.outputs[name]
                text = render_mod.compose_output(rendered, output_spec)
                selected = config_mod.select_validators(cfg, output_spec, validators)
                if selected:
                    with tempfile.TemporaryDirectory() as tmp_dir:
                        tmp_path = Path(tmp_dir) / "output"
                        render_mod.write_output(text, tmp_path)
                        for validator_name in selected:
                            spec = cfg.validators[validator_name]
                            validation_mod.run_validator(tmp_path, spec.command)
        except YamlFragError as exc:
            click.echo(f"{target_name}: {exc}", err=True)
            failed.append(target_name)

    if failed:
        raise YamlFragError(
            f"validate-all: {len(failed)} target(s) failed: {', '.join(failed)}"
        )


@cli.command()
@click.argument("target")
@click.argument("path", required=False)
@_config_option
@_inventory_option
@_fragments_option
@_secrets_option
@_var_option
@_only_option
def explain(
    target: str,
    path: str | None,
    config_path: Path,
    inventory_path: Path | None,
    fragments_dir: Path | None,
    secrets_path: Path | None,
    cli_variables: dict[str, str],
    only_output: str | None,
) -> None:
    """Show provenance for TARGET, optionally scoped to a single PATH.

    Prints one section per output the target produces (headed by the output
    name); use ``--only`` to scope to a single output. See README.md
    "Provenance and `explain`" for the expected output format.
    """
    cfg = config_mod.load_config(config_path)
    inv_path, frags_dir = _resolve_inputs(cfg, inventory_path, fragments_dir)
    _check_only_output(cfg, only_output)

    # Redact sources so explain never executes captures or reveals secrets
    # (README.md "Resolution timing"); secret/capture-derived values appear as
    # non-executing placeholders in the provenance output.
    result = render_mod.render_target(
        target,
        config=cfg,
        inventory_path=inv_path,
        fragments_dir=frags_dir,
        cli_variables=cast(Variables, cli_variables),
        secrets_path=secrets_path,
        validate=False,
        redact_sources=True,
    )

    def _in_scope(doc_path: str) -> bool:
        if path is None:
            return True
        return doc_path == path or doc_path.startswith(path.rstrip("/") + "/")

    sections: list[str] = []
    for name in _select_output_names(result.outputs, only_output, target):
        rendered = result.outputs[name]
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
                    lines.append(f"    - {entry.fragment} operation {entry.operation_index}")
            else:
                last = entries[-1]
                lines.append(f"  value: {value}")
                lines.append(f"  source: {last.fragment} operation {last.operation_index}")
            blocks.append("\n".join(lines))
        sections.append(f"== {name} ==\n\n" + "\n\n".join(blocks))

    click.echo("\n\n".join(sections))


@cli.command("list")
@click.argument("kind", type=click.Choice(["targets", "fragments", "groups"]))
@_config_option
@_inventory_option
@_fragments_option
def list_(kind: str, config_path: Path, inventory_path: Path | None, fragments_dir: Path | None) -> None:
    """List ``targets``, ``fragments``, or ``groups``."""
    cfg = config_mod.load_config(config_path)
    inv_path, frags_dir = _resolve_inputs(cfg, inventory_path, fragments_dir)

    if kind == "fragments":
        if not frags_dir.is_dir():
            raise ConfigError(f"fragments directory not found: {frags_dir}")
        names = sorted(
            str(p.relative_to(frags_dir).with_suffix("")).replace("\\", "/")
            for p in frags_dir.rglob("*.yaml")
        )
        for name in names:
            click.echo(name)
        return

    inv = inventory_mod.load_inventory(inv_path)
    if kind == "targets":
        for name in inv.targets:
            click.echo(name)
    elif kind == "groups":
        for name in inv.groups:
            click.echo(name)


@cli.command()
@click.argument("target")
@_config_option
@_inventory_option
@_fragments_option
@_secrets_option
@_var_option
@click.option("--show-secrets", is_flag=True, help="Do not redact secret-looking values (unsafe).")
def inspect(
    target: str,
    config_path: Path,
    inventory_path: Path | None,
    fragments_dir: Path | None,
    secrets_path: Path | None,
    cli_variables: dict[str, str],
    show_secrets: bool,
) -> None:
    """Show resolved groups, per-output fragment order, and (redacted)
    variables.

    See README.md "CLI usage".
    """
    cfg = config_mod.load_config(config_path)
    inv_path, frags_dir = _resolve_inputs(cfg, inventory_path, fragments_dir)
    inv = inventory_mod.load_inventory(inv_path)
    resolved = inventory_mod.resolve_target(inv, target, cli_variables=cast(Variables, cli_variables))

    redacted_variables: Variables = render_mod.redact_variables(
        resolved.variables, show_secrets=show_secrets
    )
    output_doc: dict[str, YamlValue] = {
        "target": resolved.name,
        "groups": list(resolved.groups),
        "outputs": cast(
            YamlValue,
            {
                name: {"fragments": list(fragments)}
                for name, fragments in resolved.output_fragments.items()
            },
        ),
        "variables": cast(YamlValue, redacted_variables),
    }
    click.echo(yamlio.dump_str(output_doc), nl=False)


def main(argv: list[str] | None = None) -> int:
    """Entry point. Run the CLI and translate errors into stable exit codes.

    Catches :class:`~yaml_frag.errors.YamlFragError`, prints its message to
    STDERR, and returns ``exc.exit_code``. Click usage errors map to
    :data:`ExitCode.USAGE`. Returns the process exit code (never raises for
    known failures). See README.md "Exit codes".
    """
    try:
        cli.main(args=argv, standalone_mode=False)
    except YamlFragError as exc:
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
