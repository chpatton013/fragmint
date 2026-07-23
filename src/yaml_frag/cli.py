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
from pathlib import Path

import click

from .config import DEFAULT_CONFIG_PATH
from .errors import YamlFragError
from .exit_codes import ExitCode


def _parse_var(ctx: click.Context, param: click.Parameter, values: tuple[str, ...]) -> dict[str, str]:
    """Parse repeated ``--var KEY=VALUE`` options into a mapping.

    Raise ``click.BadParameter`` (which maps to ExitCode.USAGE) if any value is
    missing the ``=`` separator. Values are strings here; type coercion happens
    during templating (PLAN.md "Template rendering": typed values).
    """
    raise NotImplementedError


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
    raise NotImplementedError


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
    raise NotImplementedError


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
    raise NotImplementedError


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
    raise NotImplementedError


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
    raise NotImplementedError


@cli.command("list")
@click.argument("kind", type=click.Choice(["targets", "fragments", "groups"]))
@_config_option
@_inventory_option
@_fragments_option
def list_(kind: str, config_path: Path, inventory_path: Path | None, fragments_dir: Path | None) -> None:
    """List ``targets``, ``fragments``, or ``groups``."""
    raise NotImplementedError


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
    raise NotImplementedError


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
