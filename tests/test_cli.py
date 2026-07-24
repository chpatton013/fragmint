"""CLI behavior and exit-code tests. See README.md "CLI usage" and "Exit codes".

Use ``click.testing.CliRunner`` (or invoke ``yaml_frag.cli.main`` with an argv
list) and assert on stdout/stderr separation and exit codes.

Tests that would execute a `from: capture` source through the real CLI (no way
to inject the stub runner through argv) are restricted to targets/paths that
don't require captures, or they monkeypatch
``yaml_frag.sources.DefaultCommandRunner`` so no real subprocess runs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from yaml_frag import sources as sources_mod
from yaml_frag.cli import main
from yaml_frag.exit_codes import ExitCode


class _StubCommandRunner:
    """A DefaultCommandRunner replacement so CLI tests never spawn `openssl`."""

    def run(self, command, *, stdin, timeout):  # type: ignore[no-untyped-def]
        return "$6$stubsalt$stubhash\n"


@pytest.fixture(autouse=True)
def _stub_default_command_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure no CLI test can accidentally run a real subprocess."""
    monkeypatch.setattr(sources_mod, "DefaultCommandRunner", _StubCommandRunner)


def test_render_writes_configured_output_path(
    tmp_path: Path,
    repo_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`render <target>` writes exactly the project-configured output path."""
    out_dir = tmp_path / "rendered"
    exit_code = main(
        [
            "render",
            "gb10-01",
            "--config",
            str(repo_root / "yaml-frag.yaml"),
            "--secrets",
            str(repo_root / "inventory" / "secrets.example.yaml"),
            "--output",
            str(out_dir / "user-data"),
        ]
    )
    assert exit_code == ExitCode.SUCCESS
    written = out_dir / "user-data"
    assert written.is_file()
    text = written.read_text()
    assert text.startswith("#cloud-config\n")
    assert "gb10-01" in text
    # Nothing else was created alongside it.
    assert list(out_dir.iterdir()) == [written]


def test_render_stdout_only_output_to_stdout(
    repo_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`render --stdout` prints only the output to stdout; diagnostics to stderr."""
    exit_code = main(
        [
            "render",
            "gb10-01",
            "--config",
            str(repo_root / "yaml-frag.yaml"),
            "--secrets",
            str(repo_root / "inventory" / "secrets.example.yaml"),
            "--stdout",
        ]
    )
    assert exit_code == ExitCode.SUCCESS
    captured = capsys.readouterr()
    assert captured.out.startswith("#cloud-config\n")
    assert "autoinstall:" in captured.out


def test_render_all_returns_nonzero_if_any_fails(
    tmp_path: Path,
    repo_root: Path,
) -> None:
    """`render-all` exits nonzero when any target fails."""
    fragments_dir = tmp_path / "fragments"
    fragments_dir.mkdir()
    (fragments_dir / "ok.yaml").write_text(
        """
fragment:
  version: 1
  name: ok
  description: fine
operations:
  - op: set
    path: /value
    value: 1
"""
    )

    inventory_path = tmp_path / "targets.yaml"
    inventory_path.write_text(
        """
version: 1
defaults:
  variables: {}
  fragments: []
targets:
  good:
    fragments: [ok]
    variables: {}
  bad:
    fragments: [does-not-exist]
    variables: {}
"""
    )

    config_path = tmp_path / "yaml-frag.yaml"
    config_path.write_text(
        f"""
version: 1
inventory: {inventory_path}
fragments_dir: {fragments_dir}
output:
  path: "{tmp_path}/rendered/{{target}}/output"
"""
    )

    exit_code = main(["render-all", "--config", str(config_path)])
    assert exit_code != ExitCode.SUCCESS
    # The good target still rendered successfully despite the other failing.
    assert (tmp_path / "rendered" / "good" / "output").is_file()
    assert not (tmp_path / "rendered" / "bad").exists()


def test_validation_failure_exit_code(tmp_path: Path) -> None:
    """A failing fragment assertion surfaces as exit code 6."""
    fragments_dir = tmp_path / "fragments"
    fragments_dir.mkdir()
    (fragments_dir / "broken.yaml").write_text(
        """
fragment:
  version: 1
  name: broken
  description: fails an assertion
operations:
  - op: set
    path: /value
    value: 1
  - op: assert
    path: /value
    equals: 2
"""
    )
    inventory_path = tmp_path / "targets.yaml"
    inventory_path.write_text(
        """
version: 1
defaults:
  variables: {}
  fragments: []
targets:
  broken-target:
    fragments: [broken]
    variables: {}
"""
    )
    config_path = tmp_path / "yaml-frag.yaml"
    config_path.write_text(
        f"""
version: 1
inventory: {inventory_path}
fragments_dir: {fragments_dir}
output:
  path: "{tmp_path}/rendered/{{target}}/output"
"""
    )
    exit_code = main(["render", "broken-target", "--config", str(config_path), "--stdout"])
    assert exit_code == ExitCode.RENDERED_VALIDATION


def test_missing_target_exit_code(repo_root: Path) -> None:
    """An unknown target returns ExitCode.INVENTORY_VALIDATION (3)."""
    exit_code = main(
        [
            "render",
            "no-such-target",
            "--config",
            str(repo_root / "yaml-frag.yaml"),
            "--secrets",
            str(repo_root / "inventory" / "secrets.example.yaml"),
            "--stdout",
        ]
    )
    assert exit_code == ExitCode.INVENTORY_VALIDATION


def test_validator_selection(tmp_path: Path) -> None:
    """`--validator NAME` runs the named validator; unknown names error out."""
    fragments_dir = tmp_path / "fragments"
    fragments_dir.mkdir()
    (fragments_dir / "ok.yaml").write_text(
        """
fragment:
  version: 1
  name: ok
  description: fine
operations:
  - op: set
    path: /value
    value: 1
"""
    )
    inventory_path = tmp_path / "targets.yaml"
    inventory_path.write_text(
        """
version: 1
defaults:
  variables: {}
  fragments: []
targets:
  t:
    fragments: [ok]
    variables: {}
"""
    )
    # A validator that always succeeds ("true"/"cat").
    config_path = tmp_path / "yaml-frag.yaml"
    config_path.write_text(
        f"""
version: 1
inventory: {inventory_path}
fragments_dir: {fragments_dir}
output:
  path: "{tmp_path}/rendered/{{target}}/output"
validators:
  ok-validator:
    command: ["true"]
  fail-validator:
    command: ["false"]
"""
    )
    exit_code = main(
        ["render", "t", "--config", str(config_path), "--validator", "ok-validator"]
    )
    assert exit_code == ExitCode.SUCCESS

    exit_code = main(
        ["render", "t", "--config", str(config_path), "--validator", "fail-validator"]
    )
    assert exit_code == ExitCode.RENDERED_VALIDATION

    exit_code = main(
        ["render", "t", "--config", str(config_path), "--validator", "no-such-validator"]
    )
    assert exit_code == ExitCode.CONFIG_ERROR


def test_explain_output(repo_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`explain <target>` prints per-path provenance in the documented format."""
    exit_code = main(
        [
            "explain",
            "gb10-01",
            "/autoinstall/kernel",
            "--config",
            str(repo_root / "yaml-frag.yaml"),
            "--secrets",
            str(repo_root / "inventory" / "secrets.example.yaml"),
        ]
    )
    assert exit_code == ExitCode.SUCCESS
    out = capsys.readouterr().out
    assert "/autoinstall/kernel" in out
    assert "value:" in out
    assert "source:" in out
    assert "hardware/gb10" in out


def test_explain_shows_contributors_for_lists(
    repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(
        [
            "explain",
            "gb10-01",
            "/autoinstall/user-data/packages",
            "--config",
            str(repo_root / "yaml-frag.yaml"),
            "--secrets",
            str(repo_root / "inventory" / "secrets.example.yaml"),
        ]
    )
    assert exit_code == ExitCode.SUCCESS
    out = capsys.readouterr().out
    assert "contributors:" in out


def test_explain_does_not_execute_captures_or_reveal_secrets(
    repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`explain` works without --secrets and shows capture-derived values as a
    non-executing placeholder, never running the capture (README "Resolution
    timing"). The autouse stub would yield the stub hash if a capture ran, so
    its absence proves no capture executed."""
    exit_code = main(
        [
            "explain",
            "gb10-01",
            "/autoinstall/identity",
            "--config",
            str(repo_root / "yaml-frag.yaml"),
        ]
    )
    assert exit_code == ExitCode.SUCCESS
    out = capsys.readouterr().out
    assert "<capture: openssl passwd -6 -stdin>" in out
    assert "stubhash" not in out  # capture was NOT executed
    assert "$6$" not in out


def test_inspect_redacts_secret_looking_variables(
    repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`inspect` redacts password/secret/token/private/credential variables."""
    exit_code = main(
        [
            "inspect",
            "gb10-01",
            "--config",
            str(repo_root / "yaml-frag.yaml"),
        ]
    )
    assert exit_code == ExitCode.SUCCESS
    out = capsys.readouterr().out
    assert "identity_password_hash" in out
    assert "<capture:" in out
    assert "correct horse" not in out


def test_inspect_show_secrets_reveals_values(
    tmp_path: Path, repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`inspect --show-secrets` prints unredacted (literal) values."""
    inventory_path = tmp_path / "targets.yaml"
    inventory_path.write_text(
        """
version: 1
defaults:
  variables:
    identity_password_hint: "not-a-real-secret-value"
  fragments: []
targets:
  t:
    fragments: []
    variables: {}
"""
    )
    config_path = tmp_path / "yaml-frag.yaml"
    config_path.write_text(
        f"""
version: 1
inventory: {inventory_path}
fragments_dir: {tmp_path}
output:
  path: "{tmp_path}/rendered/{{target}}/output"
"""
    )
    exit_code = main(["inspect", "t", "--config", str(config_path)])
    assert exit_code == ExitCode.SUCCESS
    redacted_out = capsys.readouterr().out
    assert "<redacted>" in redacted_out
    assert "not-a-real-secret-value" not in redacted_out

    exit_code = main(["inspect", "t", "--config", str(config_path), "--show-secrets"])
    assert exit_code == ExitCode.SUCCESS
    revealed_out = capsys.readouterr().out
    assert "not-a-real-secret-value" in revealed_out


def test_atomic_output_no_partial_file_on_failure(
    tmp_path: Path, repo_root: Path
) -> None:
    """A failure mid-render leaves no partial final output file."""
    fragments_dir = tmp_path / "fragments"
    fragments_dir.mkdir()
    (fragments_dir / "broken.yaml").write_text(
        """
fragment:
  version: 1
  name: broken
  description: fails an assertion
operations:
  - op: set
    path: /value
    value: 1
  - op: assert
    path: /value
    equals: 2
"""
    )
    inventory_path = tmp_path / "targets.yaml"
    inventory_path.write_text(
        """
version: 1
defaults:
  variables: {}
  fragments: []
targets:
  broken-target:
    fragments: [broken]
    variables: {}
"""
    )
    output_path = tmp_path / "rendered" / "user-data"
    config_path = tmp_path / "yaml-frag.yaml"
    config_path.write_text(
        f"""
version: 1
inventory: {inventory_path}
fragments_dir: {fragments_dir}
output:
  path: "{output_path}"
"""
    )
    exit_code = main(["render", "broken-target", "--config", str(config_path)])
    assert exit_code != ExitCode.SUCCESS
    assert not output_path.exists()
    assert not output_path.parent.exists()


def test_diagnostics_go_to_stderr(
    repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Warnings and errors are written to stderr, not stdout."""
    # Missing target: an error goes to stderr, stdout stays empty.
    exit_code = main(
        [
            "render",
            "no-such-target",
            "--config",
            str(repo_root / "yaml-frag.yaml"),
            "--stdout",
        ]
    )
    assert exit_code != ExitCode.SUCCESS
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err != ""


def test_parse_var_rejects_missing_equals() -> None:
    from yaml_frag.cli import _parse_var
    import click

    with pytest.raises(click.BadParameter):
        _parse_var(None, None, ("no-equals-sign",))  # type: ignore[arg-type]


def test_parse_var_parses_key_value_pairs() -> None:
    from yaml_frag.cli import _parse_var

    result = _parse_var(None, None, ("a=1", "b=two=three"))  # type: ignore[arg-type]
    assert result == {"a": "1", "b": "two=three"}


def test_list_targets_groups_fragments(repo_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["list", "targets", "--config", str(repo_root / "yaml-frag.yaml")])
    assert exit_code == ExitCode.SUCCESS
    out = capsys.readouterr().out
    assert "gb10-01" in out
    assert "gb10-02" in out
    assert "generic-vm-01" in out

    exit_code = main(["list", "groups", "--config", str(repo_root / "yaml-frag.yaml")])
    assert exit_code == ExitCode.SUCCESS
    out = capsys.readouterr().out
    assert "gb10" in out
    assert "general_servers" in out

    exit_code = main(["list", "fragments", "--config", str(repo_root / "yaml-frag.yaml")])
    assert exit_code == ExitCode.SUCCESS
    out = capsys.readouterr().out
    assert "hardware/gb10" in out
    assert "autoinstall/checks" in out
