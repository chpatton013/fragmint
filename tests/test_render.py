"""Rendering and snapshot tests.

Snapshot fixtures live in tests/fixtures/expected/<target>/user-data. To
regenerate them, render each target and write the output there, then review
the diff before committing (see tests/fixtures/README.md).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from yaml_frag import config as config_mod
from yaml_frag import render as render_mod
from yaml_frag.errors import AssertionFailedError, ValidationError
from yaml_frag.models import OutputSpec

SNAPSHOT_TARGETS = ["generic-vm-01", "gb10-01", "gb10-02"]


def _render(
    target: str,
    *,
    config_path: Path,
    inventory_path: Path,
    fragments_dir: Path,
    secrets_example_path: Path,
    stub_runner,
    validate: bool = True,
):
    cfg = config_mod.load_config(config_path)
    result = render_mod.render_target(
        target,
        config=cfg,
        inventory_path=inventory_path,
        fragments_dir=fragments_dir,
        secrets_path=secrets_example_path,
        runner=stub_runner,
        validate=validate,
    )
    return cfg, result


@pytest.mark.parametrize("target", SNAPSHOT_TARGETS)
def test_snapshot_matches_expected(
    target: str,
    config_path: Path,
    inventory_path: Path,
    fragments_dir: Path,
    secrets_example_path: Path,
    fixtures_dir: Path,
    stub_runner,
) -> None:
    """Rendered output equals the committed expected fixture, byte for byte."""
    cfg, result = _render(
        target,
        config_path=config_path,
        inventory_path=inventory_path,
        fragments_dir=fragments_dir,
        secrets_example_path=secrets_example_path,
        stub_runner=stub_runner,
    )
    text = render_mod.compose_output(result, cfg.output)
    expected_path = fixtures_dir / "expected" / target / "user-data"
    expected = expected_path.read_bytes()
    assert text.encode("utf-8") == expected


def test_gb10_matches_readme_expected_render(
    config_path: Path,
    inventory_path: Path,
    fragments_dir: Path,
    secrets_example_path: Path,
    stub_runner,
) -> None:
    """gb10-01 renders the expected YAML shown in README.md's "Example project:
    Ubuntu autoinstall" section, using a stubbed CommandRunner for the
    capture-derived password hash."""
    cfg, result = _render(
        "gb10-01",
        config_path=config_path,
        inventory_path=inventory_path,
        fragments_dir=fragments_dir,
        secrets_example_path=secrets_example_path,
        stub_runner=stub_runner,
    )
    text = render_mod.compose_output(result, cfg.output)

    assert text.startswith("#cloud-config\n")
    doc = result.document
    autoinstall = doc["autoinstall"]
    assert autoinstall["version"] == 1
    assert autoinstall["kernel"] == {"package": "linux-generic-hwe-24.04"}
    assert autoinstall["source"] == {
        "id": "ubuntu-server-minimal",
        "search_drivers": False,
    }
    assert autoinstall["keyboard"] == {"layout": "us"}
    assert autoinstall["locale"] == "en_US.UTF-8"
    assert autoinstall["identity"]["hostname"] == "gb10-01"
    assert autoinstall["identity"]["username"] == "chris"
    assert autoinstall["identity"]["password"] == "$6$stubsalt$stubhash"
    assert autoinstall["storage"] == {
        "layout": {"name": "lvm", "sizing-policy": "all"}
    }
    assert autoinstall["network"] == {
        "version": 2,
        "ethernets": {
            "primary": {
                "match": {"name": "enP7s7"},
                "dhcp4": True,
                "dhcp6": False,
            }
        },
    }
    assert autoinstall["ssh"] == {
        "install-server": True,
        "allow-pw": False,
        "import-id": ["gh:chpatton013"],
    }
    user_data = autoinstall["user-data"]
    assert user_data["package_update"] is True
    assert user_data["package_upgrade"] is False
    assert user_data["packages"] == [
        "bind9-dnsutils",
        "ca-certificates",
        "curl",
        "iputils-ping",
        "vim-tiny",
    ]
    assert user_data["write_files"] == [
        {
            "path": "/etc/sudoers.d/90-chris-nopasswd",
            "owner": "root:root",
            "permissions": "0440",
            "content": "chris ALL=(ALL:ALL) NOPASSWD:ALL\n",
        }
    ]
    assert user_data["runcmd"] == [
        'visudo --check --file="/etc/sudoers.d/90-chris-nopasswd"'
    ]


def test_output_template_prepends_cloud_config_header(
    config_path: Path,
    inventory_path: Path,
    fragments_dir: Path,
    secrets_example_path: Path,
    stub_runner,
) -> None:
    """The project output template wraps the YAML with a `#cloud-config` header."""
    cfg, result = _render(
        "gb10-01",
        config_path=config_path,
        inventory_path=inventory_path,
        fragments_dir=fragments_dir,
        secrets_example_path=secrets_example_path,
        stub_runner=stub_runner,
    )
    text = render_mod.compose_output(result, cfg.output)
    lines = text.splitlines()
    assert lines[0] == "#cloud-config"
    assert lines[1] == "autoinstall:"

    # Without a template, the header is absent.
    no_template_output = OutputSpec(path=cfg.output.path, template=None)
    text_no_template = render_mod.compose_output(result, no_template_output)
    assert not text_no_template.startswith("#cloud-config")
    assert text_no_template.startswith("autoinstall:")


def test_gb10_hosts_differ_only_in_host_variables(
    config_path: Path,
    inventory_path: Path,
    fragments_dir: Path,
    secrets_example_path: Path,
    stub_runner,
) -> None:
    """gb10-01 and gb10-02 differ only where host variables differ."""
    _, result_1 = _render(
        "gb10-01",
        config_path=config_path,
        inventory_path=inventory_path,
        fragments_dir=fragments_dir,
        secrets_example_path=secrets_example_path,
        stub_runner=stub_runner,
    )
    _, result_2 = _render(
        "gb10-02",
        config_path=config_path,
        inventory_path=inventory_path,
        fragments_dir=fragments_dir,
        secrets_example_path=secrets_example_path,
        stub_runner=stub_runner,
    )
    doc_1 = dict(result_1.document)
    doc_2 = dict(result_2.document)
    assert doc_1["autoinstall"]["identity"]["hostname"] == "gb10-01"
    assert doc_2["autoinstall"]["identity"]["hostname"] == "gb10-02"

    # Neutralize the only variable that legitimately differs, then compare.
    doc_1["autoinstall"]["identity"]["hostname"] = "SAME"
    doc_2["autoinstall"]["identity"]["hostname"] = "SAME"
    assert doc_1 == doc_2


def test_fragment_assertion_failure_is_reported(
    config_path: Path,
    fragments_dir: Path,
    secrets_example_path: Path,
    stub_runner,
    tmp_path: Path,
) -> None:
    """A failed fragment `assert` (e.g. autoinstall/checks) fails the render with
    context. Structural checks are data, not built-in renderer logic."""
    inventory_path = tmp_path / "targets.yaml"
    inventory_path.write_text(
        """
version: 1
defaults:
  variables: {}
  fragments: []
targets:
  broken:
    fragments:
      - failing-assert
    variables: {}
"""
    )
    frag_dir = tmp_path / "frags"
    frag_dir.mkdir()
    (frag_dir / "failing-assert.yaml").write_text(
        """
fragment:
  version: 1
  name: failing-assert
  description: deliberately fails an assertion
operations:
  - op: set
    path: /autoinstall
    value:
      version: 1
  - op: assert
    path: /autoinstall/version
    equals: 99
"""
    )

    cfg = config_mod.load_config(config_path)
    with pytest.raises(AssertionFailedError) as excinfo:
        render_mod.render_target(
            "broken",
            config=cfg,
            inventory_path=inventory_path,
            fragments_dir=frag_dir,
            secrets_path=secrets_example_path,
            runner=stub_runner,
        )
    assert "/autoinstall/version" in str(excinfo.value)


def test_generic_validation_rejects_unresolved_markers(
    config_path: Path,
    secrets_example_path: Path,
    stub_runner,
    tmp_path: Path,
) -> None:
    """Generic validation fails on leftover `{{`/`{%` markers.

    A variable whose own (literal) value happens to contain "{{" survives
    templating unchanged (rendering succeeds — the template was a single
    whole-variable reference), but the resulting document still contains an
    unresolved-looking marker, so generic validation must reject it.
    """
    inventory_path = tmp_path / "targets.yaml"
    inventory_path.write_text(
        """
version: 1
defaults:
  variables:
    echo_var: "{{ nested }}"
  fragments: []
targets:
  broken:
    fragments:
      - echoes
    variables: {}
"""
    )
    frag_dir = tmp_path / "frags"
    frag_dir.mkdir()
    (frag_dir / "echoes.yaml").write_text(
        """
fragment:
  version: 1
  name: echoes
  description: echoes a variable whose value itself contains template braces
operations:
  - op: set
    path: /leftover
    value: "{{ echo_var }}"
"""
    )

    cfg = config_mod.load_config(config_path)
    with pytest.raises(ValidationError, match="unresolved template marker"):
        render_mod.render_target(
            "broken",
            config=cfg,
            inventory_path=inventory_path,
            fragments_dir=frag_dir,
            secrets_path=secrets_example_path,
            runner=stub_runner,
        )


def test_deterministic_output(
    config_path: Path,
    inventory_path: Path,
    fragments_dir: Path,
    secrets_example_path: Path,
    stub_runner,
) -> None:
    """Rendering the same target twice yields identical bytes."""
    cfg, result_1 = _render(
        "gb10-01",
        config_path=config_path,
        inventory_path=inventory_path,
        fragments_dir=fragments_dir,
        secrets_example_path=secrets_example_path,
        stub_runner=stub_runner,
    )
    _, result_2 = _render(
        "gb10-01",
        config_path=config_path,
        inventory_path=inventory_path,
        fragments_dir=fragments_dir,
        secrets_example_path=secrets_example_path,
        stub_runner=stub_runner,
    )
    text_1 = render_mod.compose_output(result_1, cfg.output)
    text_2 = render_mod.compose_output(result_2, cfg.output)
    assert text_1 == text_2


# --- Additional unit-style tests for render.py's own responsibilities -------


def test_redact_variables_redacts_by_name() -> None:
    variables = {
        "identity_password_hash": "supersecret",
        "identity_username": "chris",
        "api_token": "tok-123",
    }
    redacted = render_mod.redact_variables(variables)
    assert redacted["identity_password_hash"] == "<redacted>"
    assert redacted["api_token"] == "<redacted>"
    assert redacted["identity_username"] == "chris"


def test_redact_variables_show_secrets_reveals_literal_values() -> None:
    variables = {"identity_password_hash": "supersecret"}
    redacted = render_mod.redact_variables(variables, show_secrets=True)
    assert redacted["identity_password_hash"] == "supersecret"


def test_redact_variables_never_reveals_source_descriptions_even_with_show_secrets() -> None:
    variables = {
        "identity_password_hash": {
            "from": "capture",
            "command": ["openssl", "passwd", "-6", "-stdin"],
            "stdin": {"from": "secret", "name": "gb10-01_password"},
        }
    }
    redacted = render_mod.redact_variables(variables, show_secrets=True)
    assert redacted["identity_password_hash"] == "<capture: openssl passwd -6 -stdin>"


def test_redact_variables_secret_source_shown_regardless_of_name() -> None:
    """A variable defined via `secret`/`capture` is redacted regardless of its
    name (README.md "Variable value sources": sensitive-by-source redaction)."""
    variables = {"totally_innocuous_name": {"from": "secret", "name": "x"}}
    redacted = render_mod.redact_variables(variables)
    assert redacted["totally_innocuous_name"] == "<secret x>"


def test_compose_output_ends_with_single_trailing_newline() -> None:
    from yaml_frag.models import RenderResult

    result = RenderResult(target="t", document={"a": 1}, provenance={}, overrides=())
    output = OutputSpec(path="rendered/{target}", template=None)
    text = render_mod.compose_output(result, output)
    assert text.endswith("\n")
    assert not text.endswith("\n\n")


def test_write_output_is_atomic_and_creates_parent_dirs(tmp_path: Path) -> None:
    target_path = tmp_path / "nested" / "dir" / "user-data"
    returned = render_mod.write_output("hello\n", target_path)
    assert returned == target_path
    assert target_path.read_text() == "hello\n"
    # No leftover temp files.
    leftovers = list(target_path.parent.glob(".*"))
    assert leftovers == []


def test_write_output_never_leaves_partial_file_on_failure(tmp_path: Path, monkeypatch) -> None:
    target_path = tmp_path / "user-data"
    target_path.write_text("original\n")

    def _boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("os.replace", _boom)
    with pytest.raises(OSError):
        render_mod.write_output("new content\n", target_path)

    # Original file untouched, and no leftover temp file.
    assert target_path.read_text() == "original\n"
    leftovers = [p for p in tmp_path.iterdir() if p.name != "user-data"]
    assert leftovers == []
