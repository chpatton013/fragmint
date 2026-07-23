"""Command-line interface for ``autoinstall-render``.

See PLAN.md "Command-line interface". This module wires up the command
structure, options, and the top-level error-to-exit-code mapping. Command
bodies are stubs to be implemented against :mod:`render`, :mod:`inventory`,
and :mod:`provenance`.

Output discipline (PLAN.md): the rendered configuration goes to STDOUT only
for ``--stdout``; ALL diagnostics (warnings, errors, progress) go to STDERR.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click

from .errors import AutoinstallError
from .exit_codes import ExitCode

#: Default locations, overridable per-command.
DEFAULT_INVENTORY = Path("inventory/machines.yaml")
DEFAULT_FRAGMENTS_DIR = Path("fragments")
DEFAULT_OUTPUT_DIR = Path("rendered")


def _parse_var(ctx: click.Context, param: click.Parameter, values: tuple[str, ...]) -> dict[str, str]:
    """Parse repeated ``--var KEY=VALUE`` options into a mapping.

    Raise ``click.BadParameter`` (which maps to ExitCode.USAGE) if any value is
    missing the ``=`` separator. Values are strings here; type coercion happens
    during templating (PLAN.md "Template rendering": typed values).
    """
    raise NotImplementedError


# Shared option decorators ---------------------------------------------------

_inventory_option = click.option(
    "--inventory",
    "inventory_path",
    type=click.Path(path_type=Path),
    default=DEFAULT_INVENTORY,
    show_default=True,
    help="Path to the inventory YAML file.",
)
_fragments_option = click.option(
    "--fragments-dir",
    type=click.Path(path_type=Path),
    default=DEFAULT_FRAGMENTS_DIR,
    show_default=True,
    help="Directory containing fragment files.",
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


@click.group()
@click.version_option()
def cli() -> None:
    """Render Ubuntu autoinstall configurations from inventory + fragments."""


@cli.command()
@click.argument("machine")
@_inventory_option
@_fragments_option
@_secrets_option
@_var_option
@click.option("--output", "output_dir", type=click.Path(path_type=Path), default=DEFAULT_OUTPUT_DIR, show_default=True)
@click.option("--stdout", "to_stdout", is_flag=True, help="Print rendered config to stdout instead of writing files.")
@click.option("--dry-run", is_flag=True, help="Render and validate but write nothing.")
@click.option("--quiet-overrides", is_flag=True, help="Suppress override warnings.")
@click.option("--no-validate", "validate", is_flag=True, default=True, flag_value=False, help="Skip rendered-document validation.")
def render(
    machine: str,
    inventory_path: Path,
    fragments_dir: Path,
    secrets_path: Path | None,
    cli_variables: dict[str, str],
    output_dir: Path,
    to_stdout: bool,
    dry_run: bool,
    quiet_overrides: bool,
    validate: bool,
) -> None:
    """Render one MACHINE to ``rendered/<machine>/user-data`` (+ meta-data)."""
    raise NotImplementedError


@cli.command("render-all")
@_inventory_option
@_fragments_option
@_secrets_option
@click.option("--output", "output_dir", type=click.Path(path_type=Path), default=DEFAULT_OUTPUT_DIR, show_default=True)
@click.option("--quiet-overrides", is_flag=True)
@click.option("--no-validate", "validate", is_flag=True, default=True, flag_value=False)
def render_all(
    inventory_path: Path,
    fragments_dir: Path,
    secrets_path: Path | None,
    output_dir: Path,
    quiet_overrides: bool,
    validate: bool,
) -> None:
    """Render every machine in inventory order; nonzero if any fails.

    Do not leave a partial final output file for a failed machine (atomic
    writes; PLAN.md "Render all machines").
    """
    raise NotImplementedError


@cli.command()
@click.argument("machine")
@_inventory_option
@_fragments_option
@_secrets_option
@_var_option
@click.option("--subiquity", is_flag=True, help="Also run optional external Subiquity validation.")
def validate(
    machine: str,
    inventory_path: Path,
    fragments_dir: Path,
    secrets_path: Path | None,
    cli_variables: dict[str, str],
    subiquity: bool,
) -> None:
    """Render MACHINE in memory and validate without writing output."""
    raise NotImplementedError


@cli.command("validate-all")
@_inventory_option
@_fragments_option
@_secrets_option
@click.option("--subiquity", is_flag=True)
def validate_all(
    inventory_path: Path,
    fragments_dir: Path,
    secrets_path: Path | None,
    subiquity: bool,
) -> None:
    """Validate every machine in inventory order; nonzero if any fails."""
    raise NotImplementedError


@cli.command()
@click.argument("machine")
@click.argument("path", required=False)
@_inventory_option
@_fragments_option
@_secrets_option
@_var_option
def explain(
    machine: str,
    path: str | None,
    inventory_path: Path,
    fragments_dir: Path,
    secrets_path: Path | None,
    cli_variables: dict[str, str],
) -> None:
    """Show provenance for MACHINE, optionally scoped to a single PATH.

    See PLAN.md "Provenance tracking" for the expected output format.
    """
    raise NotImplementedError


@cli.command("list")
@click.argument("kind", type=click.Choice(["machines", "fragments", "groups"]))
@_inventory_option
@_fragments_option
def list_(kind: str, inventory_path: Path, fragments_dir: Path) -> None:
    """List ``machines``, ``fragments``, or ``groups``."""
    raise NotImplementedError


@cli.command()
@click.argument("machine")
@_inventory_option
@_fragments_option
@_secrets_option
@_var_option
@click.option("--show-secrets", is_flag=True, help="Do not redact secret-looking values (unsafe).")
def inspect(
    machine: str,
    inventory_path: Path,
    fragments_dir: Path,
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

    Catches :class:`~autoinstall_renderer.errors.AutoinstallError`, prints its
    message to STDERR, and returns ``exc.exit_code``. Click usage errors map to
    :data:`ExitCode.USAGE`. Returns the process exit code (never raises for
    known failures). See PLAN.md "Exit codes".
    """
    try:
        cli.main(args=argv, standalone_mode=False)
    except AutoinstallError as exc:
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
