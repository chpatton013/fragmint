"""CLI behavior and exit-code tests. See README.md "CLI usage" and "Exit codes".

Use ``click.testing.CliRunner`` (or invoke ``yaml_frag.cli.main`` with an argv
list) and assert on stdout/stderr separation and exit codes.

Tests that would execute a `from: capture` source through the real CLI (no way
to inject the stub runner through argv) are restricted to targets/paths that
don't require captures, or they monkeypatch
``yaml_frag.sources.DefaultCommandRunner`` so no real subprocess runs.
"""

from __future__ import annotations

import shutil
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


def _example_args(repo_root: Path) -> list[str]:
    return [
        "--inventory",
        str(repo_root / "example" / "targets.yaml"),
        "--secrets",
        str(repo_root / "example" / "secrets.example.yaml"),
    ]


AUTOINSTALL_RENDERED = "example/modules/autoinstall/rendered"
ANSIBLE_RENDERED = "example/modules/ansible/rendered"


def test_render_writes_configured_output_path(
    tmp_path: Path,
    repo_root: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`render <target>` writes exactly the configured output path."""
    out_dir = tmp_path / "rendered"
    exit_code = main(
        [
            "render",
            "gb10-01",
            *_example_args(repo_root),
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
    exit_code = main(["render", "gb10-01", *_example_args(repo_root), "--stdout"])
    assert exit_code == ExitCode.SUCCESS
    captured = capsys.readouterr()
    assert captured.out.startswith("#cloud-config\n")
    assert "autoinstall:" in captured.out


def test_render_writes_every_produced_output(
    tmp_path: Path,
    repo_root: Path,
) -> None:
    """`render <target>` (no --only) writes every output the target produces."""
    exit_code = main(["render", "gb10-01", *_example_args(repo_root)])
    assert exit_code == ExitCode.SUCCESS
    rendered_dir = repo_root / AUTOINSTALL_RENDERED / "gb10-01"
    try:
        assert (rendered_dir / "user-data").is_file()
        assert (rendered_dir / "meta-data").is_file()
        meta = (rendered_dir / "meta-data").read_text()
        assert "instance-id: gb10-01" in meta
    finally:
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
            *_example_args(repo_root),
            "--only",
            "meta-data",
            "--output",
            str(out_path),
        ]
    )
    assert exit_code == ExitCode.SUCCESS
    assert out_path.is_file()
    assert "instance-id: gb10-01" in out_path.read_text()


def test_render_only_unknown_output_is_module_error(repo_root: Path) -> None:
    """`--only` naming an output the closure doesn't declare fails."""
    exit_code = main(
        ["render", "gb10-01", *_example_args(repo_root), "--only", "no-such-output", "--stdout"]
    )
    assert exit_code == ExitCode.MODULE_ERROR


def test_render_all_returns_nonzero_if_any_fails(tmp_path: Path) -> None:
    """`render-all` exits nonzero when any target fails."""
    (tmp_path / "fragments").mkdir()
    (tmp_path / "fragments" / "ok.yaml").write_text(
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
    (tmp_path / "targets.yaml").write_text(
        f"""
version: 1
outputs:
  main:
    path: "{tmp_path}/rendered/{{target}}/output"
targets:
  good:
    outputs:
      main:
        fragments: [ok]
    variables: {{}}
  bad:
    outputs:
      main:
        fragments: [does-not-exist]
    variables: {{}}
"""
    )

    exit_code = main(["render-all", "--inventory", str(tmp_path / "targets.yaml")])
    assert exit_code != ExitCode.SUCCESS
    # The good target still rendered successfully despite the other failing.
    assert (tmp_path / "rendered" / "good" / "output").is_file()
    assert not (tmp_path / "rendered" / "bad").exists()


def test_validation_failure_exit_code(tmp_path: Path) -> None:
    """A failing fragment assertion surfaces as exit code 6."""
    (tmp_path / "fragments").mkdir()
    (tmp_path / "fragments" / "broken.yaml").write_text(
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
    (tmp_path / "targets.yaml").write_text(
        f"""
version: 1
outputs:
  main:
    path: "{tmp_path}/rendered/{{target}}/output"
targets:
  broken-target:
    outputs:
      main:
        fragments: [broken]
    variables: {{}}
"""
    )
    exit_code = main(
        ["render", "broken-target", "--inventory", str(tmp_path / "targets.yaml"), "--stdout"]
    )
    assert exit_code == ExitCode.RENDERED_VALIDATION


def test_missing_target_exit_code(repo_root: Path) -> None:
    """An unknown target returns ExitCode.INVENTORY_VALIDATION (3)."""
    exit_code = main(["render", "no-such-target", *_example_args(repo_root), "--stdout"])
    assert exit_code == ExitCode.INVENTORY_VALIDATION


def test_validator_selection(tmp_path: Path) -> None:
    """`--validator NAME` runs the named validator; unknown names error out."""
    (tmp_path / "fragments").mkdir()
    (tmp_path / "fragments" / "ok.yaml").write_text(
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
    # A validator that always succeeds ("true") / always fails ("false").
    (tmp_path / "targets.yaml").write_text(
        f"""
version: 1
outputs:
  main:
    path: "{tmp_path}/rendered/{{target}}/output"
validators:
  ok-validator:
    command: ["true"]
  fail-validator:
    command: ["false"]
targets:
  t:
    outputs:
      main:
        fragments: [ok]
    variables: {{}}
"""
    )
    inventory_path = tmp_path / "targets.yaml"

    exit_code = main(
        ["render", "t", "--inventory", str(inventory_path), "--validator", "ok-validator"]
    )
    assert exit_code == ExitCode.SUCCESS

    exit_code = main(
        ["render", "t", "--inventory", str(inventory_path), "--validator", "fail-validator"]
    )
    assert exit_code == ExitCode.RENDERED_VALIDATION

    exit_code = main(
        ["render", "t", "--inventory", str(inventory_path), "--validator", "no-such-validator"]
    )
    assert exit_code == ExitCode.MODULE_ERROR


def test_explain_output(repo_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`explain <target> --path` prints per-path provenance in the documented
    format, with fragment names shown as qualified refs."""
    exit_code = main(
        ["explain", "gb10-01", "--path", "/autoinstall/kernel", *_example_args(repo_root)]
    )
    assert exit_code == ExitCode.SUCCESS
    out = capsys.readouterr().out
    assert "/autoinstall/kernel" in out
    assert "value:" in out
    assert "source:" in out
    assert "autoinstall:hardware/gb10" in out


def test_explain_shows_contributors_for_lists(
    repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    exit_code = main(
        [
            "explain",
            "gb10-01",
            "--path",
            "/autoinstall/user-data/packages",
            *_example_args(repo_root),
        ]
    )
    assert exit_code == ExitCode.SUCCESS
    out = capsys.readouterr().out
    assert "contributors:" in out


def test_explain_shows_every_produced_output_by_default(
    repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`explain` with no --only shows a section per output the target produces."""
    exit_code = main(["explain", "gb10-01", *_example_args(repo_root)])
    assert exit_code == ExitCode.SUCCESS
    out = capsys.readouterr().out
    assert "== user-data ==" in out
    assert "== meta-data ==" in out
    assert "/instance-id" in out


def test_explain_only_scopes_to_single_output(
    repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`explain --only NAME` shows just the named output's section."""
    exit_code = main(["explain", "gb10-01", *_example_args(repo_root), "--only", "meta-data"])
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
            "--path",
            "/autoinstall/identity",
            "--inventory",
            str(repo_root / "example" / "targets.yaml"),
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
    exit_code = main(["inspect", "gb10-01", "--inventory", str(repo_root / "example" / "targets.yaml")])
    assert exit_code == ExitCode.SUCCESS
    out = capsys.readouterr().out
    assert "identity_password_hash" in out
    assert "<capture:" in out
    assert "correct horse" not in out


def test_inspect_show_secrets_reveals_values(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`inspect --show-secrets` prints unredacted (literal) values."""
    (tmp_path / "targets.yaml").write_text(
        f"""
version: 1
outputs:
  main:
    path: "{tmp_path}/rendered/{{target}}/output"
defaults:
  variables:
    identity_password_hint: "not-a-real-secret-value"
targets:
  t:
    variables: {{}}
"""
    )
    inventory_path = tmp_path / "targets.yaml"
    exit_code = main(["inspect", "t", "--inventory", str(inventory_path)])
    assert exit_code == ExitCode.SUCCESS
    redacted_out = capsys.readouterr().out
    assert "<redacted>" in redacted_out
    assert "not-a-real-secret-value" not in redacted_out

    exit_code = main(["inspect", "t", "--inventory", str(inventory_path), "--show-secrets"])
    assert exit_code == ExitCode.SUCCESS
    revealed_out = capsys.readouterr().out
    assert "not-a-real-secret-value" in revealed_out


def _secret_consuming_inventory(tmp_path: Path) -> Path:
    """An inventory directory whose single output echoes `from: secret` value
    ``token``, for the secrets-resolution tests below."""
    (tmp_path / "fragments").mkdir()
    (tmp_path / "fragments" / "emit.yaml").write_text(
        """
fragment:
  version: 1
  description: writes the token into the document
operations:
  - op: set
    path: /token
    value: "{{ token }}"
"""
    )
    (tmp_path / "targets.yaml").write_text(
        f"""
version: 1
outputs:
  main:
    path: "{tmp_path}/rendered/{{target}}/output"
defaults:
  outputs:
    main:
      fragments: [emit]
  variables:
    token:
      from: secret
      name: token
targets:
  t:
    variables: {{}}
"""
    )
    return tmp_path / "rendered" / "t" / "output"


def test_sibling_secrets_yaml_is_used_without_the_flag(tmp_path: Path) -> None:
    """An inventory's sibling secrets.yaml is picked up with no --secrets, for
    both the directory and the file form of --inventory (README "Secrets")."""
    output_path = _secret_consuming_inventory(tmp_path)
    (tmp_path / "secrets.yaml").write_text("secrets:\n  token: from-sibling\n")

    assert main(["render", "t", "--inventory", str(tmp_path)]) == ExitCode.SUCCESS
    assert "from-sibling" in output_path.read_text()

    output_path.unlink()
    assert main(["render", "t", "--inventory", str(tmp_path / "targets.yaml")]) == ExitCode.SUCCESS
    assert "from-sibling" in output_path.read_text()


def test_sibling_secrets_inference_is_conditional_on_existence(tmp_path: Path) -> None:
    """With no sibling secrets.yaml and nothing referencing a secret, the run
    succeeds — inference never demands a file that isn't needed."""
    (tmp_path / "targets.yaml").write_text(
        f"""
version: 1
outputs:
  main:
    path: "{tmp_path}/rendered/{{target}}/output"
targets:
  t:
    variables: {{}}
"""
    )
    assert not (tmp_path / "secrets.yaml").exists()
    assert main(["render", "t", "--inventory", str(tmp_path)]) == ExitCode.SUCCESS


def test_explicit_secrets_overrides_the_sibling_file(tmp_path: Path) -> None:
    """--secrets wins over the inferred sibling; the sibling is not merged in."""
    output_path = _secret_consuming_inventory(tmp_path)
    (tmp_path / "secrets.yaml").write_text("secrets:\n  token: from-sibling\n")
    elsewhere = tmp_path / "elsewhere.yaml"
    elsewhere.write_text("secrets:\n  token: from-flag\n")

    exit_code = main(
        ["render", "t", "--inventory", str(tmp_path), "--secrets", str(elsewhere)]
    )
    assert exit_code == ExitCode.SUCCESS
    text = output_path.read_text()
    assert "from-flag" in text
    assert "from-sibling" not in text


def test_explicit_secrets_file_must_exist(tmp_path: Path) -> None:
    """Unlike the inferred sibling, an explicit --secrets that names a missing
    file is an error rather than an empty store — a typo fails loudly."""
    _secret_consuming_inventory(tmp_path)
    (tmp_path / "secrets.yaml").write_text("secrets:\n  token: from-sibling\n")

    exit_code = main(
        [
            "render",
            "t",
            "--inventory",
            str(tmp_path),
            "--secrets",
            str(tmp_path / "typo.yaml"),
        ]
    )
    assert exit_code == ExitCode.INVENTORY_VALIDATION


def test_atomic_output_no_partial_file_on_failure(tmp_path: Path) -> None:
    """A failure mid-render leaves no partial final output file."""
    (tmp_path / "fragments").mkdir()
    (tmp_path / "fragments" / "broken.yaml").write_text(
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
    output_path = tmp_path / "rendered" / "user-data"
    (tmp_path / "targets.yaml").write_text(
        f"""
version: 1
outputs:
  main:
    path: "{output_path}"
targets:
  broken-target:
    outputs:
      main:
        fragments: [broken]
    variables: {{}}
"""
    )
    exit_code = main(["render", "broken-target", "--inventory", str(tmp_path / "targets.yaml")])
    assert exit_code != ExitCode.SUCCESS
    assert not output_path.exists()
    assert not output_path.parent.exists()


def test_diagnostics_go_to_stderr(repo_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Warnings and errors are written to stderr, not stdout."""
    exit_code = main(
        ["render", "no-such-target", "--inventory", str(repo_root / "example" / "targets.yaml"), "--stdout"]
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
    exit_code = main(["list", "targets", "--inventory", str(repo_root / "example" / "targets.yaml")])
    assert exit_code == ExitCode.SUCCESS
    out = capsys.readouterr().out
    assert "gb10-01" in out
    assert "gb10-02" in out
    assert "generic-vm-01" in out

    exit_code = main(["list", "groups", "--inventory", str(repo_root / "example" / "targets.yaml")])
    assert exit_code == ExitCode.SUCCESS
    out = capsys.readouterr().out
    assert "gb10" in out
    assert "general_servers" in out

    exit_code = main(["list", "fragments", "--inventory", str(repo_root / "example" / "targets.yaml")])
    assert exit_code == ExitCode.SUCCESS
    out = capsys.readouterr().out
    assert "autoinstall:hardware/gb10" in out
    assert "autoinstall:autoinstall/checks" in out
    assert "hosts/gb10-01" in out  # the inventory's own (root, bare) fragment


def test_list_modules_shows_closure(repo_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["list", "modules", "--inventory", str(repo_root / "example" / "targets.yaml")])
    assert exit_code == ExitCode.SUCCESS
    out = capsys.readouterr().out
    assert "autoinstall" in out
    assert "ansible" in out


# --- Aggregate outputs (README.md "Aggregate outputs") ----------------------


def test_list_outputs_shows_scope(repo_root: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """`list outputs` prints each output's name, scope, and defining document."""
    exit_code = main(["list", "outputs", "--inventory", str(repo_root / "example" / "targets.yaml")])
    assert exit_code == ExitCode.SUCCESS
    out = capsys.readouterr().out
    assert "user-data\ttarget\tautoinstall" in out
    assert "meta-data\ttarget\tautoinstall" in out
    assert "ansible-inventory\taggregate\tansible" in out


def test_render_target_silently_skips_aggregate_output(
    repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`render TARGET` with no --only skips the aggregate output — no note
    on stderr, since that would fire on every single-host render."""
    exit_code = main(["render", "gb10-01", *_example_args(repo_root), "--dry-run"])
    assert exit_code == ExitCode.SUCCESS
    captured = capsys.readouterr()
    assert "ansible-inventory" not in captured.err
    assert "ansible-inventory" not in captured.out


def test_render_target_only_aggregate_output_is_module_error(repo_root: Path) -> None:
    """`render TARGET --only <aggregate>` is a config error pointing at
    `render-all --only NAME` instead."""
    exit_code = main(
        ["render", "gb10-01", *_example_args(repo_root), "--only", "ansible-inventory", "--stdout"]
    )
    assert exit_code == ExitCode.MODULE_ERROR


def test_validate_target_only_aggregate_output_is_module_error(repo_root: Path) -> None:
    """`validate TARGET --only <aggregate>` mirrors `render`'s error."""
    exit_code = main(
        ["validate", "gb10-01", *_example_args(repo_root), "--only", "ansible-inventory"]
    )
    assert exit_code == ExitCode.MODULE_ERROR


def test_render_all_writes_both_per_target_and_aggregate_outputs(repo_root: Path) -> None:
    """`render-all` (no --only) writes every per-target output for every
    target, then the aggregate output once."""
    exit_code = main(["render-all", *_example_args(repo_root)])
    assert exit_code == ExitCode.SUCCESS
    autoinstall_dir = repo_root / AUTOINSTALL_RENDERED
    ansible_dir = repo_root / ANSIBLE_RENDERED
    try:
        assert (autoinstall_dir / "gb10-01" / "user-data").is_file()
        assert (autoinstall_dir / "gb10-02" / "meta-data").is_file()
        inventory_text = (ansible_dir / "inventory.yaml").read_text()
        assert "gb10-01" in inventory_text
        assert "gb10-02" in inventory_text
        assert "generic-vm-01" in inventory_text
    finally:
        for name in ("gb10-01", "gb10-02", "generic-vm-01"):
            shutil.rmtree(autoinstall_dir / name, ignore_errors=True)
        (ansible_dir / "inventory.yaml").unlink(missing_ok=True)


def test_render_all_only_scopes_to_single_aggregate_output(tmp_path: Path, repo_root: Path) -> None:
    """`render-all --only <aggregate>` renders no per-target output at all —
    just the one aggregate output."""
    out_path = tmp_path / "inventory.yaml"
    exit_code = main(
        [
            "render-all",
            *_example_args(repo_root),
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
    assert not (repo_root / AUTOINSTALL_RENDERED / "gb10-01").exists()


def test_render_all_only_scopes_to_single_target_output(repo_root: Path) -> None:
    """`render-all --only NAME` with a `scope: target` output writes just
    that output for every target, and no aggregate output runs."""
    exit_code = main(["render-all", *_example_args(repo_root), "--only", "meta-data"])
    assert exit_code == ExitCode.SUCCESS
    autoinstall_dir = repo_root / AUTOINSTALL_RENDERED
    try:
        assert (autoinstall_dir / "gb10-01" / "meta-data").is_file()
        assert not (autoinstall_dir / "gb10-01" / "user-data").exists()
        assert not (repo_root / ANSIBLE_RENDERED / "inventory.yaml").exists()
    finally:
        for name in ("gb10-01", "gb10-02", "generic-vm-01"):
            shutil.rmtree(autoinstall_dir / name, ignore_errors=True)


def test_render_all_skips_aggregate_output_when_a_target_fails(tmp_path: Path) -> None:
    """If any target fails, `render-all` skips aggregate outputs entirely
    (no partial aggregate file) and reports why on stderr."""
    (tmp_path / "fragments").mkdir()
    (tmp_path / "fragments" / "ok.yaml").write_text(
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
    (tmp_path / "targets.yaml").write_text(
        f"""
version: 1
outputs:
  combined:
    scope: aggregate
    path: "{tmp_path}/rendered/combined.yaml"
targets:
  good:
    outputs:
      combined:
        fragments: [ok]
    variables: {{}}
  bad:
    outputs:
      combined:
        fragments: [does-not-exist]
    variables: {{}}
"""
    )
    exit_code = main(["render-all", "--inventory", str(tmp_path / "targets.yaml")])
    assert exit_code != ExitCode.SUCCESS
    assert not (tmp_path / "rendered" / "combined.yaml").exists()


def test_explain_no_target_requires_aggregate_only(repo_root: Path) -> None:
    """`explain` with no TARGET and no (or non-aggregate) --only is a config
    error."""
    exit_code = main(["explain", "--inventory", str(repo_root / "example" / "targets.yaml")])
    assert exit_code == ExitCode.MODULE_ERROR

    exit_code = main(
        ["explain", "--inventory", str(repo_root / "example" / "targets.yaml"), "--only", "user-data"]
    )
    assert exit_code == ExitCode.MODULE_ERROR


def test_explain_no_target_with_aggregate_only_shows_provenance(
    repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`explain --only <aggregate>` with no TARGET shows the composed
    aggregate document's provenance, with each entry naming its
    contributing target."""
    exit_code = main(["explain", *_example_args(repo_root), "--only", "ansible-inventory"])
    assert exit_code == ExitCode.SUCCESS
    out = capsys.readouterr().out
    assert "== ansible-inventory ==" in out
    assert "(target gb10-01)" in out
    assert "(target generic-vm-01)" in out


def test_explain_path_is_independent_of_target(
    repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--path` scopes provenance whether or not a TARGET is given.

    It is a flag rather than a positional precisely so the two are
    independent: scoping an aggregate output's provenance by path requires
    omitting TARGET, which is impossible while PATH competes for the same
    positional slot.
    """
    exit_code = main(
        [
            "explain",
            *_example_args(repo_root),
            "--only",
            "ansible-inventory",
            "--path",
            "/all/children/gb10",
        ]
    )

    assert exit_code == ExitCode.SUCCESS
    out = capsys.readouterr().out
    assert "/all/children/gb10/hosts/gb10-01/ansible_host" in out
    # generic-vm-01 lives under a different group, so the path scope excludes it.
    assert "generic_vm" not in out


def test_explain_target_with_aggregate_only_is_module_error(repo_root: Path) -> None:
    """Naming TARGET together with an aggregate --only is the same config
    error as `render TARGET --only <aggregate>`."""
    exit_code = main(
        ["explain", "gb10-01", *_example_args(repo_root), "--only", "ansible-inventory"]
    )
    assert exit_code == ExitCode.MODULE_ERROR


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
    inventory_path = tmp_path / "targets.yaml"
    inventory_path.write_text(
        f"""
version: 1
outputs:
  agg:
    scope: aggregate
    path: "{tmp_path / 'out.yaml'}"
defaults:
  outputs:
    agg:
      fragments: [host]
targets:
  only-aggregate: {{}}
"""
    )
    return inventory_path


def test_render_target_producing_only_aggregate_outputs_writes_nothing(tmp_path: Path) -> None:
    """`render TARGET` silently skips aggregate outputs, so a target routing
    fragments only into one produces nothing and still succeeds."""
    inventory_path = _aggregate_only_project(tmp_path)

    exit_code = main(["render", "only-aggregate", "--inventory", str(inventory_path)])

    assert exit_code == ExitCode.SUCCESS
    assert not (tmp_path / "out.yaml").exists()


def test_stdout_on_target_with_no_per_target_outputs_explains_why(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--stdout` on a target that produces no PER-TARGET output must say so,
    not report that it "produces multiple outputs ()" — the empty case is
    reachable precisely because aggregate outputs are excluded from
    per-target rendering (README.md "Aggregate outputs")."""
    inventory_path = _aggregate_only_project(tmp_path)

    exit_code = main(["render", "only-aggregate", "--inventory", str(inventory_path), "--stdout"])

    assert exit_code == ExitCode.MODULE_ERROR
    message = capsys.readouterr().err
    assert "produces no per-target outputs" in message
    assert "render-all" in message
    assert "multiple outputs ()" not in message


def test_aggregate_only_error_hints_name_the_relevant_command(
    repo_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Each single-target command's aggregate `--only` error suggests its own
    remedy rather than a one-size-fits-all `render-all` hint."""
    common = [*_example_args(repo_root), "--only", "ansible-inventory"]

    assert main(["render", "gb10-01", *common]) == ExitCode.MODULE_ERROR
    assert "render-all --only ansible-inventory" in capsys.readouterr().err

    assert main(["validate", "gb10-01", *common]) == ExitCode.MODULE_ERROR
    assert "validate-all --only ansible-inventory" in capsys.readouterr().err

    assert main(["explain", "gb10-01", *common]) == ExitCode.MODULE_ERROR
    assert "without a TARGET" in capsys.readouterr().err


# --- --inventory resolution (README.md "The module model") ------------------


def test_inventory_option_directory_resolves_to_targets_yaml(tmp_path: Path) -> None:
    (tmp_path / "targets.yaml").write_text(
        f"version: 1\noutputs:\n  main:\n    path: \"{tmp_path}/out\"\ntargets:\n  t: {{}}\n"
    )
    exit_code = main(["list", "targets", "--inventory", str(tmp_path)])
    assert exit_code == ExitCode.SUCCESS


def test_inventory_option_file_used_as_given(tmp_path: Path) -> None:
    custom = tmp_path / "hosts.yaml"
    custom.write_text(
        f"version: 1\noutputs:\n  main:\n    path: \"{tmp_path}/out\"\ntargets:\n  t: {{}}\n"
    )
    exit_code = main(["list", "targets", "--inventory", str(custom)])
    assert exit_code == ExitCode.SUCCESS


def test_inventory_option_missing_directory_errors(tmp_path: Path) -> None:
    exit_code = main(["list", "targets", "--inventory", str(tmp_path / "nope")])
    assert exit_code == ExitCode.MODULE_ERROR


def test_inventory_option_defaults_to_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "targets.yaml").write_text(
        f"version: 1\noutputs:\n  main:\n    path: \"{tmp_path}/out\"\ntargets:\n  t: {{}}\n"
    )
    monkeypatch.chdir(tmp_path)
    exit_code = main(["list", "targets"])
    assert exit_code == ExitCode.SUCCESS


# --- --fragments-dir override forms (README.md "The module model") ---------


def test_fragments_dir_bare_override_overrides_root(tmp_path: Path) -> None:
    (tmp_path / "fragments").mkdir()
    (tmp_path / "fragments" / "ok.yaml").write_text(
        "fragment:\n  version: 1\n  description: x\noperations:\n  - op: set\n    path: /v\n    value: 1\n"
    )
    other = tmp_path / "elsewhere"
    other.mkdir()
    (other / "ok.yaml").write_text(
        "fragment:\n  version: 1\n  description: x\noperations:\n  - op: set\n    path: /v\n    value: 2\n"
    )
    (tmp_path / "targets.yaml").write_text(
        f"""
version: 1
outputs:
  main:
    path: "{tmp_path}/out-{{target}}"
targets:
  t:
    outputs:
      main:
        fragments: [ok]
    variables: {{}}
"""
    )
    exit_code = main(
        [
            "render",
            "t",
            "--inventory",
            str(tmp_path / "targets.yaml"),
            "--fragments-dir",
            str(other),
            "--stdout",
        ]
    )
    assert exit_code == ExitCode.SUCCESS


def test_fragments_dir_mixed_forms_rejected(tmp_path: Path) -> None:
    exit_code = main(
        [
            "list",
            "targets",
            "--inventory",
            str(tmp_path),
            "--fragments-dir",
            str(tmp_path),
            "--fragments-dir",
            f"ns={tmp_path}",
        ]
    )
    assert exit_code == ExitCode.USAGE


def test_fragments_dir_unknown_module_rejected(repo_root: Path, tmp_path: Path) -> None:
    exit_code = main(
        [
            "list",
            "targets",
            "--inventory",
            str(repo_root / "example" / "targets.yaml"),
            "--fragments-dir",
            f"no-such-module={tmp_path}",
        ]
    )
    assert exit_code == ExitCode.MODULE_ERROR
