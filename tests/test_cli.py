"""CLI behavior and exit-code tests. Covers PLAN.md "CLI tests" and "Exit codes".

Use ``click.testing.CliRunner`` (or invoke ``yaml_frag.cli.main`` with an argv
list) and assert on stdout/stderr separation and exit codes.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.skip(reason="TODO: implement — see PLAN.md 'CLI tests'")


def test_render_writes_configured_output_path() -> None:
    """`render <target>` writes exactly the project-configured output path."""
    raise NotImplementedError


def test_render_stdout_only_output_to_stdout() -> None:
    """`render --stdout` prints only the output to stdout; diagnostics to stderr."""
    raise NotImplementedError


def test_render_all_returns_nonzero_if_any_fails() -> None:
    """`render-all` exits nonzero when any target fails."""
    raise NotImplementedError


def test_validation_failure_exit_code() -> None:
    """A rendered-validation failure returns ExitCode.RENDERED_VALIDATION (6)."""
    raise NotImplementedError


def test_missing_target_exit_code() -> None:
    """An unknown target returns ExitCode.INVENTORY_VALIDATION (3)."""
    raise NotImplementedError


def test_validator_selection() -> None:
    """`--validator NAME` runs the named validator; unknown names error out."""
    raise NotImplementedError


def test_explain_output() -> None:
    """`explain <target>` prints per-path provenance in the documented format."""
    raise NotImplementedError


def test_inspect_redacts_secret_looking_variables() -> None:
    """`inspect` redacts password/secret/token/private/credential variables."""
    raise NotImplementedError


def test_inspect_show_secrets_reveals_values() -> None:
    """`inspect --show-secrets` prints unredacted values."""
    raise NotImplementedError


def test_atomic_output_no_partial_file_on_failure() -> None:
    """A failure mid-render leaves no partial final output file."""
    raise NotImplementedError


def test_diagnostics_go_to_stderr() -> None:
    """Warnings and errors are written to stderr, not stdout."""
    raise NotImplementedError
