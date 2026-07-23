"""Command-line interface for ``yaml-frag``.

See PLAN.md "Command-line interface". This module wires up the command
structure, options, and the top-level error-to-exit-code mapping. Command
bodies are stubs to be implemented against :mod:`config`, :mod:`render`,
:mod:`inventory`, and :mod:`provenance`.

Domain defaults (inventory/fragments locations, output path/template,
validators) come from the project configuration (:mod:`config`); the CLI flags
below override those defaults per invocation.

Output discipline (PLAN.md): rendered output goes to STDOUT only for
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
from .models import ProjectConfig, Variables, YamlValue


def _parse_var(ctx: click.Context, param: click.Parameter, values: tuple[str, ...]) -> dict[str, str]:
    """Parse repeated ``--var KEY=VALUE`` options into a mapping.

    Raise ``click.BadParameter`` (which maps to ExitCode.USAGE) if any value is
    missing the ``=`` separator. Values are strings here; type coercion happens
    during templating (PLAN.md "Template rendering": typed values).
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
    and fragments-dir locations (PLAN.md "Command-line interface")."""
    resolved_inventory = inventory_path if inventory_path is not None else Path(config.inventory)
    resolved_fragments = fragments_dir if fragments_dir is not None else Path(config.fragments_dir)
    return resolved_inventory, resolved_fragments


def _emit_overrides(overrides: tuple[str, ...], *, quiet: bool) -> None:
    if quiet:
        return
    for warning in overrides:
        click.echo(warning, err=True)


def _run_selected_validators(
    config: ProjectConfig,
    output_path: Path,
    requested: tuple[str, ...],
) -> None:
    for name in config_mod.select_validators(config, requested):
        spec = config.validators[name]
        validation_mod.run_validator(output_path, spec.command)


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
@click.option("--output", "output_path", type=click.Path(path_type=Path), default=None, help="Override the destination path for this render.")
@click.option("--stdout", "to_stdout", is_flag=True, help="Print rendered output to stdout instead of writing.")
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
    output_path: Path | None,
    to_stdout: bool,
    dry_run: bool,
    quiet_overrides: bool,
    validate: bool,
) -> None:
    """Render one TARGET to its configured output path."""
    cfg = config_mod.load_config(config_path)
    inv_path, frags_dir = _resolve_inputs(cfg, inventory_path, fragments_dir)

    result = render_mod.render_target(
        target,
        config=cfg,
        inventory_path=inv_path,
        fragments_dir=frags_dir,
        cli_variables=cast(Variables, cli_variables),
        secrets_path=secrets_path,
        validate=validate,
    )
    _emit_overrides(result.overrides, quiet=quiet_overrides)

    text = render_mod.compose_output(result, cfg.output)

    if to_stdout:
        click.echo(text, nl=False)
        return

    if dry_run:
        return

    out_path = config_mod.resolve_output_path(cfg, target, override=output_path)
    render_mod.write_output(text, out_path)

    if validate:
        _run_selected_validators(cfg, out_path, validators)


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
    writes; PLAN.md "Render all targets").
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
            _emit_overrides(result.overrides, quiet=quiet_overrides)
            text = render_mod.compose_output(result, cfg.output)
            out_path = config_mod.resolve_output_path(cfg, target_name)
            render_mod.write_output(text, out_path)
            if validate:
                _run_selected_validators(cfg, out_path, validators)
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
def validate(
    target: str,
    config_path: Path,
    inventory_path: Path | None,
    fragments_dir: Path | None,
    secrets_path: Path | None,
    cli_variables: dict[str, str],
    validators: tuple[str, ...],
) -> None:
    """Render TARGET in memory and validate without writing output."""
    cfg = config_mod.load_config(config_path)
    inv_path, frags_dir = _resolve_inputs(cfg, inventory_path, fragments_dir)

    result = render_mod.render_target(
        target,
        config=cfg,
        inventory_path=inv_path,
        fragments_dir=frags_dir,
        cli_variables=cast(Variables, cli_variables),
        secrets_path=secrets_path,
        validate=True,
    )
    text = render_mod.compose_output(result, cfg.output)

    selected = config_mod.select_validators(cfg, validators)
    if selected:
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir) / "output"
            render_mod.write_output(text, tmp_path)
            for name in selected:
                spec = cfg.validators[name]
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
            text = render_mod.compose_output(result, cfg.output)
            selected = config_mod.select_validators(cfg, validators)
            if selected:
                with tempfile.TemporaryDirectory() as tmp_dir:
                    tmp_path = Path(tmp_dir) / "output"
                    render_mod.write_output(text, tmp_path)
                    for name in selected:
                        spec = cfg.validators[name]
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
def explain(
    target: str,
    path: str | None,
    config_path: Path,
    inventory_path: Path | None,
    fragments_dir: Path | None,
    secrets_path: Path | None,
    cli_variables: dict[str, str],
) -> None:
    """Show provenance for TARGET, optionally scoped to a single PATH.

    See PLAN.md "Provenance tracking" for the expected output format.
    """
    cfg = config_mod.load_config(config_path)
    inv_path, frags_dir = _resolve_inputs(cfg, inventory_path, fragments_dir)

    # Redact sources so explain never executes captures or reveals secrets
    # (PLAN.md "Resolution timing"); secret/capture-derived values appear as
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

    blocks: list[str] = []
    for doc_path in sorted(result.provenance):
        if not _in_scope(doc_path):
            continue
        entries = result.provenance[doc_path]
        _, value = pointer_mod.get(result.document, doc_path)
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

    click.echo("\n\n".join(blocks))


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
    """Show resolved groups, fragment order, and (redacted) variables.

    See PLAN.md "Show resolved inputs".
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
        "fragments": list(resolved.fragments),
        "variables": cast(YamlValue, redacted_variables),
    }
    click.echo(yamlio.dump_str(output_doc), nl=False)


def main(argv: list[str] | None = None) -> int:
    """Entry point. Run the CLI and translate errors into stable exit codes.

    Catches :class:`~yaml_frag.errors.YamlFragError`, prints its message to
    STDERR, and returns ``exc.exit_code``. Click usage errors map to
    :data:`ExitCode.USAGE`. Returns the process exit code (never raises for
    known failures). See PLAN.md "Exit codes".
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
