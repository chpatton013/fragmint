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
            str(repo_root / "example" / "yaml-frag.yaml"),
            "--secrets",
            str(repo_root / "example" / "inventory" / "secrets.example.yaml"),
            "--only",
            "user-data",
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
            str(repo_root / "example" / "yaml-frag.yaml"),
            "--secrets",
            str(repo_root / "example" / "inventory" / "secrets.example.yaml"),
            "--stdout",
        ]
    )
    assert exit_code == ExitCode.SUCCESS
    captured = capsys.readouterr()
    assert captured.out.startswith("#cloud-config\n")
    assert "autoinstall:" in captured.out


def test_render_writes_every_produced_output(
    tmp_path: Path,
    repo_root: Path,
) -> None:
    """`render <target>` (no --only) writes every output the target produces."""
    exit_code = main(
        [
            "render",
            "gb10-01",
            "--config",
            str(repo_root / "example" / "yaml-frag.yaml"),
            "--secrets",
            str(repo_root / "example" / "inventory" / "secrets.example.yaml"),
        ]
    )
    assert exit_code == ExitCode.SUCCESS
    rendered_dir = repo_root / "example" / "rendered" / "gb10-01"
    try:
        assert (rendered_dir / "user-data").is_file()
        assert (rendered_dir / "meta-data").is_file()
        meta = (rendered_dir / "meta-data").read_text()
        assert "instance-id: gb10-01" in meta
    finally:
        import shutil

        shutil.rmtree(rendered_dir, ignore_errors=True)


def test_render_only_scopes_to_single_output(
    tmp_path: Path,
    repo_root: Path,
) -> None:
    """`render --only NAME` writes/prints only the named output."""
    out_path = tmp_path / "meta-data"
    exit_code = main(
        [
            "render",
            "gb10-01",
            "--config",
            str(repo_root / "example" / "yaml-frag.yaml"),
            "--secrets",
            str(repo_root / "example" / "inventory" / "secrets.example.yaml"),
            "--only",
            "meta-data",
            "--output",
            str(out_path),
        ]
    )
    assert exit_code == ExitCode.SUCCESS
    assert out_path.is_file()
    assert "instance-id: gb10-01" in out_path.read_text()


def test_render_only_unknown_output_is_config_error(repo_root: Path) -> None:
    """`--only` naming an output the project config doesn't declare fails."""
    exit_code = main(
        [
            "render",
            "gb10-01",
            "--config",
            str(repo_root / "example" / "yaml-frag.yaml"),
            "--secrets",
            str(repo_root / "example" / "inventory" / "secrets.example.yaml"),
            "--only",
            "no-such-output",
            "--stdout",
        ]
    )
    assert exit_code == ExitCode.CONFIG_ERROR


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
targets:
  good:
    outputs:
      main:
        fragments: [ok]
    variables: {}
  bad:
    outputs:
      main:
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
outputs:
  main:
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
targets:
  broken-target:
    outputs:
      main:
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
outputs:
  main:
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
            str(repo_root / "example" / "yaml-frag.yaml"),
            "--secrets",
            str(repo_root / "example" / "inventory" / "secrets.example.yaml"),
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
targets:
  t:
    outputs:
      main:
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
outputs:
  main:
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
            str(repo_root / "example" / "yaml-frag.yaml"),
            "--secrets",
            str(repo_root / "example" / "inventory" / "secrets.example.yaml"),
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
            str(repo_root / "example" / "yaml-frag.yaml"),
            "--secrets",
            str(repo_root / "example" / "inventory" / "secrets.example.yaml"),
        ]
    )
    assert exit_code == ExitCode.SUCCESS
    out = capsys.readouterr().out
    assert "contributors:" in out


def test_explain_shows_every_produced_output_by_default(
    repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`explain` with no --only shows a section per output the target produces."""
    exit_code = main(
        [
            "explain",
            "gb10-01",
            "--config",
            str(repo_root / "example" / "yaml-frag.yaml"),
            "--secrets",
            str(repo_root / "example" / "inventory" / "secrets.example.yaml"),
        ]
    )
    assert exit_code == ExitCode.SUCCESS
    out = capsys.readouterr().out
    assert "== user-data ==" in out
    assert "== meta-data ==" in out
    assert "/instance-id" in out


def test_explain_only_scopes_to_single_output(
    repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`explain --only NAME` shows just the named output's section."""
    exit_code = main(
        [
            "explain",
            "gb10-01",
            "--config",
            str(repo_root / "example" / "yaml-frag.yaml"),
            "--secrets",
            str(repo_root / "example" / "inventory" / "secrets.example.yaml"),
            "--only",
            "meta-data",
        ]
    )
    assert exit_code == ExitCode.SUCCESS
    out = capsys.readouterr().out
    assert "== meta-data ==" in out
    assert "== user-data ==" not in out


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
            str(repo_root / "example" / "yaml-frag.yaml"),
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
            str(repo_root / "example" / "yaml-frag.yaml"),
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
targets:
  t:
    variables: {}
"""
    )
    config_path = tmp_path / "yaml-frag.yaml"
    config_path.write_text(
        f"""
version: 1
inventory: {inventory_path}
fragments_dir: {tmp_path}
outputs:
  main:
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
targets:
  broken-target:
    outputs:
      main:
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
outputs:
  main:
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
            str(repo_root / "example" / "yaml-frag.yaml"),
            "--stdout",
        ]
    )
    assert exit_code != ExitCode.SUCCESS
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err != ""


def test_parse_var_rejects_missing_equals() -> None:
    import click

    from yaml_frag.cli import _parse_var

    with pytest.raises(click.BadParameter):
        _parse_var(None, None, ("no-equals-sign",))  # type: ignore[arg-type]


def test_parse_var_parses_key_value_pairs() -> None:
    from yaml_frag.cli import _parse_var

    result = _parse_var(None, None, ("a=1", "b=two=three"))  # type: ignore[arg-type]
    assert result == {"a": "1", "b": "two=three"}


def test_list_targets_groups_fragments(repo_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["list", "targets", "--config", str(repo_root / "example" / "yaml-frag.yaml")])
    assert exit_code == ExitCode.SUCCESS
    out = capsys.readouterr().out
    assert "gb10-01" in out
    assert "gb10-02" in out
    assert "generic-vm-01" in out

    exit_code = main(["list", "groups", "--config", str(repo_root / "example" / "yaml-frag.yaml")])
    assert exit_code == ExitCode.SUCCESS
    out = capsys.readouterr().out
    assert "gb10" in out
    assert "general_servers" in out

    exit_code = main(["list", "fragments", "--config", str(repo_root / "example" / "yaml-frag.yaml")])
    assert exit_code == ExitCode.SUCCESS
    out = capsys.readouterr().out
    assert "hardware/gb10" in out
    assert "autoinstall/checks" in out


# --- Aggregate outputs (README.md "Aggregate outputs") ----------------------


def test_list_outputs_shows_scope(repo_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`list outputs` prints each output's name and scope."""
    exit_code = main(["list", "outputs", "--config", str(repo_root / "example" / "yaml-frag.yaml")])
    assert exit_code == ExitCode.SUCCESS
    out = capsys.readouterr().out
    assert "user-data\ttarget" in out
    assert "meta-data\ttarget" in out
    assert "ansible-inventory\taggregate" in out


def test_render_target_silently_skips_aggregate_output(
    repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`render TARGET` with no --only skips the aggregate output — no note
    on stderr, since that would fire on every single-host render."""
    exit_code = main(
        [
            "render",
            "gb10-01",
            "--config",
            str(repo_root / "example" / "yaml-frag.yaml"),
            "--secrets",
            str(repo_root / "example" / "inventory" / "secrets.example.yaml"),
            "--dry-run",
        ]
    )
    assert exit_code == ExitCode.SUCCESS
    captured = capsys.readouterr()
    assert "ansible-inventory" not in captured.err
    assert "ansible-inventory" not in captured.out


def test_render_target_only_aggregate_output_is_config_error(repo_root: Path) -> None:
    """`render TARGET --only <aggregate>` is a config error pointing at
    `render-all --only NAME` instead."""
    exit_code = main(
        [
            "render",
            "gb10-01",
            "--config",
            str(repo_root / "example" / "yaml-frag.yaml"),
            "--secrets",
            str(repo_root / "example" / "inventory" / "secrets.example.yaml"),
            "--only",
            "ansible-inventory",
            "--stdout",
        ]
    )
    assert exit_code == ExitCode.CONFIG_ERROR


def test_validate_target_only_aggregate_output_is_config_error(repo_root: Path) -> None:
    """`validate TARGET --only <aggregate>` mirrors `render`'s error."""
    exit_code = main(
        [
            "validate",
            "gb10-01",
            "--config",
            str(repo_root / "example" / "yaml-frag.yaml"),
            "--secrets",
            str(repo_root / "example" / "inventory" / "secrets.example.yaml"),
            "--only",
            "ansible-inventory",
        ]
    )
    assert exit_code == ExitCode.CONFIG_ERROR


def test_render_all_writes_both_per_target_and_aggregate_outputs(
    tmp_path: Path, repo_root: Path
) -> None:
    """`render-all` (no --only) writes every per-target output for every
    target, then the aggregate output once."""
    exit_code = main(
        [
            "render-all",
            "--config",
            str(repo_root / "example" / "yaml-frag.yaml"),
            "--secrets",
            str(repo_root / "example" / "inventory" / "secrets.example.yaml"),
        ]
    )
    assert exit_code == ExitCode.SUCCESS
    rendered_dir = repo_root / "example" / "rendered"
    try:
        assert (rendered_dir / "gb10-01" / "user-data").is_file()
        assert (rendered_dir / "gb10-02" / "meta-data").is_file()
        inventory_text = (rendered_dir / "inventory.yaml").read_text()
        assert "gb10-01" in inventory_text
        assert "gb10-02" in inventory_text
        assert "generic-vm-01" in inventory_text
    finally:
        import shutil

        for name in ("gb10-01", "gb10-02", "generic-vm-01"):
            shutil.rmtree(rendered_dir / name, ignore_errors=True)
        (rendered_dir / "inventory.yaml").unlink(missing_ok=True)


def test_render_all_only_scopes_to_single_aggregate_output(
    tmp_path: Path, repo_root: Path
) -> None:
    """`render-all --only <aggregate>` renders no per-target output at all —
    just the one aggregate output."""
    out_path = tmp_path / "inventory.yaml"
    exit_code = main(
        [
            "render-all",
            "--config",
            str(repo_root / "example" / "yaml-frag.yaml"),
            "--secrets",
            str(repo_root / "example" / "inventory" / "secrets.example.yaml"),
            "--only",
            "ansible-inventory",
            "--output",
            str(out_path),
        ]
    )
    assert exit_code == ExitCode.SUCCESS
    assert out_path.is_file()
    text = out_path.read_text()
    assert "gb10-01" in text
    assert "generic-vm-01" in text
    # No per-target directories were created alongside it.
    rendered_dir = repo_root / "example" / "rendered"
    assert not (rendered_dir / "gb10-01").exists()


def test_render_all_only_scopes_to_single_target_output(
    tmp_path: Path, repo_root: Path
) -> None:
    """`render-all --only NAME` with a `scope: target` output writes just
    that output for every target, and no aggregate output runs."""
    exit_code = main(
        [
            "render-all",
            "--config",
            str(repo_root / "example" / "yaml-frag.yaml"),
            "--secrets",
            str(repo_root / "example" / "inventory" / "secrets.example.yaml"),
            "--only",
            "meta-data",
        ]
    )
    assert exit_code == ExitCode.SUCCESS
    rendered_dir = repo_root / "example" / "rendered"
    try:
        assert (rendered_dir / "gb10-01" / "meta-data").is_file()
        assert not (rendered_dir / "gb10-01" / "user-data").exists()
        assert not (rendered_dir / "inventory.yaml").exists()
    finally:
        import shutil

        for name in ("gb10-01", "gb10-02", "generic-vm-01"):
            shutil.rmtree(rendered_dir / name, ignore_errors=True)


def test_render_all_skips_aggregate_output_when_a_target_fails(tmp_path: Path) -> None:
    """If any target fails, `render-all` skips aggregate outputs entirely
    (no partial aggregate file) and reports why on stderr."""
    fragments_dir = tmp_path / "fragments"
    fragments_dir.mkdir()
    (fragments_dir / "ok.yaml").write_text(
        """
fragment:
  version: 1
  description: fine
operations:
  - op: set
    path: "/hosts/{{ target }}"
    value: true
"""
    )
    inventory_path = tmp_path / "targets.yaml"
    inventory_path.write_text(
        """
version: 1
targets:
  good:
    outputs:
      combined:
        fragments: [ok]
    variables: {}
  bad:
    outputs:
      combined:
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
outputs:
  combined:
    scope: aggregate
    path: "{tmp_path}/rendered/combined.yaml"
"""
    )
    exit_code = main(["render-all", "--config", str(config_path)])
    assert exit_code != ExitCode.SUCCESS
    assert not (tmp_path / "rendered" / "combined.yaml").exists()


def test_explain_no_target_requires_aggregate_only(repo_root: Path) -> None:
    """`explain` with no TARGET and no (or non-aggregate) --only is a config
    error."""
    exit_code = main(["explain", "--config", str(repo_root / "example" / "yaml-frag.yaml")])
    assert exit_code == ExitCode.CONFIG_ERROR

    exit_code = main(
        [
            "explain",
            "--config",
            str(repo_root / "example" / "yaml-frag.yaml"),
            "--only",
            "user-data",
        ]
    )
    assert exit_code == ExitCode.CONFIG_ERROR


def test_explain_no_target_with_aggregate_only_shows_provenance(
    repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`explain --only <aggregate>` with no TARGET shows the composed
    aggregate document's provenance, with each entry naming its
    contributing target."""
    exit_code = main(
        [
            "explain",
            "--config",
            str(repo_root / "example" / "yaml-frag.yaml"),
            "--secrets",
            str(repo_root / "example" / "inventory" / "secrets.example.yaml"),
            "--only",
            "ansible-inventory",
        ]
    )
    assert exit_code == ExitCode.SUCCESS
    out = capsys.readouterr().out
    assert "== ansible-inventory ==" in out
    assert "(target gb10-01)" in out
    assert "(target generic-vm-01)" in out


def test_explain_target_with_aggregate_only_is_config_error(repo_root: Path) -> None:
    """Naming TARGET together with an aggregate --only is the same config
    error as `render TARGET --only <aggregate>`."""
    exit_code = main(
        [
            "explain",
            "gb10-01",
            "--config",
            str(repo_root / "example" / "yaml-frag.yaml"),
            "--secrets",
            str(repo_root / "example" / "inventory" / "secrets.example.yaml"),
            "--only",
            "ansible-inventory",
        ]
    )
    assert exit_code == ExitCode.CONFIG_ERROR


def _aggregate_only_project(tmp_path: Path) -> Path:
    """Write a minimal project whose sole target routes fragments ONLY into an
    aggregate output, so the target produces no per-target output at all."""
    (tmp_path / "fragments").mkdir()
    (tmp_path / "fragments" / "host.yaml").write_text(
        """
fragment:
  version: 1
  description: register the target in the aggregate document
operations:
  - op: set
    path: "/hosts/{{ target }}"
    value: true
"""
    )
    (tmp_path / "inventory.yaml").write_text(
        """
version: 1
defaults:
  outputs:
    agg:
      fragments: [host]
targets:
  only-aggregate: {}
"""
    )
    config_path = tmp_path / "yaml-frag.yaml"
    config_path.write_text(
        f"""
version: 1
inventory: inventory.yaml
fragments_dir: fragments
outputs:
  agg:
    scope: aggregate
    path: "{tmp_path / 'out.yaml'}"
"""
    )
    return config_path


def test_render_target_producing_only_aggregate_outputs_writes_nothing(
    tmp_path: Path,
) -> None:
    """`render TARGET` silently skips aggregate outputs, so a target routing
    fragments only into one produces nothing and still succeeds."""
    config_path = _aggregate_only_project(tmp_path)

    exit_code = main(["render", "only-aggregate", "--config", str(config_path)])

    assert exit_code == ExitCode.SUCCESS
    assert not (tmp_path / "out.yaml").exists()


def test_stdout_on_target_with_no_per_target_outputs_explains_why(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--stdout` on a target that produces no PER-TARGET output must say so,
    not report that it "produces multiple outputs ()" — the empty case is
    reachable precisely because aggregate outputs are excluded from
    per-target rendering (README.md "Aggregate outputs")."""
    config_path = _aggregate_only_project(tmp_path)

    exit_code = main(
        ["render", "only-aggregate", "--config", str(config_path), "--stdout"]
    )

    assert exit_code == ExitCode.CONFIG_ERROR
    message = capsys.readouterr().err
    assert "produces no per-target outputs" in message
    assert "render-all" in message
    assert "multiple outputs ()" not in message


def test_aggregate_only_error_hints_name_the_relevant_command(
    repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Each single-target command's aggregate `--only` error suggests its own
    remedy rather than a one-size-fits-all `render-all` hint."""
    common = [
        "--config",
        str(repo_root / "example" / "yaml-frag.yaml"),
        "--only",
        "ansible-inventory",
    ]

    assert main(["render", "gb10-01", *common]) == ExitCode.CONFIG_ERROR
    assert "render-all --only ansible-inventory" in capsys.readouterr().err

    assert main(["validate", "gb10-01", *common]) == ExitCode.CONFIG_ERROR
    assert "validate-all --only ansible-inventory" in capsys.readouterr().err

    assert main(["explain", "gb10-01", *common]) == ExitCode.CONFIG_ERROR
    assert "without a TARGET" in capsys.readouterr().err
