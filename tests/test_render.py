"""Rendering and snapshot tests.

Snapshot fixtures live in tests/fixtures/expected/<target>/user-data (per
target) and tests/fixtures/expected/ansible-inventory.yaml (the example
project's `scope: aggregate` output, composed once across every target). To
regenerate them, render and write the output there, then review the diff
before committing (see tests/fixtures/README.md).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from yaml_frag import config as config_mod
from yaml_frag import render as render_mod
from yaml_frag.errors import AssertionFailedError, ConfigError, ValidationError
from yaml_frag.models import OutputSpec, ProjectConfig

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
@pytest.mark.parametrize("output_name", ["user-data", "meta-data"])
def test_snapshot_matches_expected(
    target: str,
    output_name: str,
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
    text = render_mod.compose_output(result.outputs[output_name], cfg.outputs[output_name])
    expected_path = fixtures_dir / "expected" / target / output_name
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
    text = render_mod.compose_output(result.outputs["user-data"], cfg.outputs["user-data"])

    assert text.startswith("#cloud-config\n")
    doc = result.outputs["user-data"].document
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
    rendered = result.outputs["user-data"]
    text = render_mod.compose_output(rendered, cfg.outputs["user-data"])
    lines = text.splitlines()
    assert lines[0] == "#cloud-config"
    assert lines[1] == "autoinstall:"

    # Without a template, the header is absent.
    no_template_output = OutputSpec(path=cfg.outputs["user-data"].path, template=None)
    text_no_template = render_mod.compose_output(rendered, no_template_output)
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
    doc_1 = dict(result_1.outputs["user-data"].document)
    doc_2 = dict(result_2.outputs["user-data"].document)
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
targets:
  broken:
    outputs:
      user-data:
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
targets:
  broken:
    outputs:
      user-data:
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


def test_fragment_can_reference_current_target_name(
    config_path: Path,
    secrets_example_path: Path,
    stub_runner,
    tmp_path: Path,
) -> None:
    """A fragment can reference the current target's own name via the
    reserved `{{ target }}` variable."""
    inventory_path = tmp_path / "targets.yaml"
    inventory_path.write_text(
        """
version: 1
targets:
  my-target:
    outputs:
      user-data:
        fragments:
          - names-itself
    variables: {}
"""
    )
    frag_dir = tmp_path / "frags"
    frag_dir.mkdir()
    (frag_dir / "names-itself.yaml").write_text(
        """
fragment:
  version: 1
  description: echoes the reserved target variable
operations:
  - op: set
    path: /whoami
    value: "{{ target }}"
"""
    )

    cfg = config_mod.load_config(config_path)
    result = render_mod.render_target(
        "my-target",
        config=cfg,
        inventory_path=inventory_path,
        fragments_dir=frag_dir,
        secrets_path=secrets_example_path,
        runner=stub_runner,
    )
    assert result.outputs["user-data"].document["whoami"] == "my-target"


def test_fragment_can_reference_current_output_name(
    config_path: Path,
    secrets_example_path: Path,
    stub_runner,
    tmp_path: Path,
) -> None:
    """A fragment shared by two outputs of the same target sees a different
    `{{ output }}` value for each — it's reserved per output, not per target."""
    inventory_path = tmp_path / "targets.yaml"
    inventory_path.write_text(
        """
version: 1
defaults:
  outputs:
    user-data:
      fragments:
        - names-its-output
    meta-data:
      fragments:
        - names-its-output
targets:
  my-target:
    variables: {}
"""
    )
    frag_dir = tmp_path / "frags"
    frag_dir.mkdir()
    (frag_dir / "names-its-output.yaml").write_text(
        """
fragment:
  version: 1
  description: echoes the reserved output variable
operations:
  - op: set
    path: /which-output
    value: "{{ output }}"
"""
    )

    cfg = config_mod.load_config(config_path)
    result = render_mod.render_target(
        "my-target",
        config=cfg,
        inventory_path=inventory_path,
        fragments_dir=frag_dir,
        secrets_path=secrets_example_path,
        runner=stub_runner,
    )
    assert result.outputs["user-data"].document["which-output"] == "user-data"
    assert result.outputs["meta-data"].document["which-output"] == "meta-data"


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
    text_1 = render_mod.compose_output(result_1.outputs["user-data"], cfg.outputs["user-data"])
    text_2 = render_mod.compose_output(result_2.outputs["user-data"], cfg.outputs["user-data"])
    assert text_1 == text_2


def test_render_target_produces_every_declared_output(
    config_path: Path,
    inventory_path: Path,
    fragments_dir: Path,
    secrets_example_path: Path,
    stub_runner,
) -> None:
    """The example project's targets produce both `user-data` and `meta-data`."""
    _, result = _render(
        "gb10-01",
        config_path=config_path,
        inventory_path=inventory_path,
        fragments_dir=fragments_dir,
        secrets_example_path=secrets_example_path,
        stub_runner=stub_runner,
    )
    assert set(result.outputs) == {"user-data", "meta-data"}
    assert result.outputs["meta-data"].document == {
        "instance-id": "gb10-01",
        "local-hostname": "gb10-01",
    }


def test_undeclared_output_not_produced(
    config_path: Path,
    fragments_dir: Path,
    secrets_example_path: Path,
    stub_runner,
    tmp_path: Path,
) -> None:
    """An output no layer contributes fragments to is simply absent from the
    result, not present-but-empty."""
    inventory_path = tmp_path / "targets.yaml"
    inventory_path.write_text(
        """
version: 1
targets:
  bare:
    outputs:
      user-data:
        fragments: []
    variables: {}
"""
    )
    cfg = config_mod.load_config(config_path)
    result = render_mod.render_target(
        "bare",
        config=cfg,
        inventory_path=inventory_path,
        fragments_dir=fragments_dir,
        secrets_path=secrets_example_path,
        runner=stub_runner,
    )
    assert result.outputs == {}


def test_unknown_output_name_in_inventory_raises_config_error(
    fragments_dir: Path,
    secrets_example_path: Path,
    stub_runner,
    tmp_path: Path,
) -> None:
    """A target whose inventory references an output name absent from the
    project config's `outputs` fails with ConfigError."""
    inventory_path = tmp_path / "targets.yaml"
    inventory_path.write_text(
        """
version: 1
targets:
  t:
    outputs:
      not-declared-anywhere:
        fragments: [autoinstall/base]
    variables: {}
"""
    )
    config_path = tmp_path / "yaml-frag.yaml"
    config_path.write_text(
        f"""
version: 1
fragments_dir: {fragments_dir}
outputs:
  user-data:
    path: "{tmp_path}/rendered/{{target}}/user-data"
"""
    )
    cfg = config_mod.load_config(config_path)
    with pytest.raises(ConfigError):
        render_mod.render_target(
            "t",
            config=cfg,
            inventory_path=inventory_path,
            fragments_dir=fragments_dir,
            secrets_path=secrets_example_path,
            runner=stub_runner,
        )


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
    from yaml_frag.models import RenderedOutput

    result = RenderedOutput(name="main", document={"a": 1}, provenance={}, overrides=())
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


# --- Aggregate outputs (README.md "Aggregate outputs") ----------------------


def _write_fragment(fragments_dir: Path, ref: str, body: str) -> None:
    path = fragments_dir / f"{ref}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


def _aggregate_config(*, agg_path: Path, extra_outputs: dict[str, OutputSpec] | None = None) -> ProjectConfig:
    outputs: dict[str, OutputSpec] = {
        "combined": OutputSpec(path=str(agg_path), scope="aggregate"),
    }
    outputs.update(extra_outputs or {})
    return ProjectConfig(
        version=1,
        inventory="unused",
        fragments_dir="unused",
        outputs=outputs,
        default_output=None,
    )


def test_aggregate_composition_order_is_target_then_fragment_order(
    tmp_path: Path, stub_runner
) -> None:
    """Aggregate composition visits targets in inventory declaration order
    (never alphabetized) and, within each target, applies that target's
    resolved fragment list in order — the same positional rule as per-target
    rendering, across targets instead of within one."""
    inventory_path = tmp_path / "targets.yaml"
    inventory_path.write_text(
        """
version: 1
targets:
  second:
    outputs:
      combined:
        fragments: [append-a, append-b]
    variables: {}
  first:
    outputs:
      combined:
        fragments: [append-a, append-b]
    variables: {}
"""
    )
    frag_dir = tmp_path / "frags"
    _write_fragment(
        frag_dir,
        "append-a",
        """
fragment:
  version: 1
  description: appends "<target>-a"
operations:
  - op: append
    path: /log
    value: ["{{ target }}-a"]
""",
    )
    _write_fragment(
        frag_dir,
        "append-b",
        """
fragment:
  version: 1
  description: appends "<target>-b"
operations:
  - op: append
    path: /log
    value: ["{{ target }}-b"]
""",
    )

    cfg = _aggregate_config(agg_path=tmp_path / "out.yaml")
    session = render_mod.RenderSession(cfg, inventory_path, frag_dir, runner=stub_runner)
    rendered = session.render_aggregate("combined")
    assert rendered is not None
    assert rendered.document["log"] == ["second-a", "second-b", "first-a", "first-b"]


def test_aggregate_target_opt_out(tmp_path: Path, stub_runner) -> None:
    """A target that contributes no fragments for an aggregate output simply
    doesn't appear in it — that's the opt-out (README.md "Aggregate
    outputs")."""
    inventory_path = tmp_path / "targets.yaml"
    inventory_path.write_text(
        """
version: 1
targets:
  contributes:
    outputs:
      combined:
        fragments: [mark]
    variables: {}
  opts-out:
    variables: {}
"""
    )
    frag_dir = tmp_path / "frags"
    _write_fragment(
        frag_dir,
        "mark",
        """
fragment:
  version: 1
  description: marks the contributing target
operations:
  - op: set
    path: "/seen/{{ target }}"
    value: true
""",
    )
    cfg = _aggregate_config(agg_path=tmp_path / "out.yaml")
    session = render_mod.RenderSession(cfg, inventory_path, frag_dir, runner=stub_runner)
    rendered = session.render_aggregate("combined")
    assert rendered is not None
    assert rendered.document == {"seen": {"contributes": True}}


def test_aggregate_no_contributors_is_not_produced(tmp_path: Path, stub_runner) -> None:
    """If no target contributes to an aggregate output, it's not produced —
    render_aggregate returns None, consistent with "an output no layer
    contributes fragments to is not produced"."""
    inventory_path = tmp_path / "targets.yaml"
    inventory_path.write_text(
        """
version: 1
targets:
  t1:
    variables: {}
  t2:
    variables: {}
"""
    )
    frag_dir = tmp_path / "frags"
    frag_dir.mkdir()
    cfg = _aggregate_config(agg_path=tmp_path / "out.yaml")
    session = render_mod.RenderSession(cfg, inventory_path, frag_dir, runner=stub_runner)
    assert session.render_aggregate("combined") is None


def test_aggregate_cross_target_override_warning_names_both_targets(
    tmp_path: Path, stub_runner
) -> None:
    """Two targets writing the same path in an aggregate document is exactly
    the "two targets claimed the same key" bug the override warning should
    catch — and for aggregate scope the warning names both contributing
    targets, not just fragment names (README.md "Aggregate outputs")."""
    inventory_path = tmp_path / "targets.yaml"
    inventory_path.write_text(
        """
version: 1
targets:
  host-a:
    outputs:
      combined:
        fragments: [claim]
    variables: {}
  host-b:
    outputs:
      combined:
        fragments: [claim]
    variables: {}
"""
    )
    frag_dir = tmp_path / "frags"
    _write_fragment(
        frag_dir,
        "claim",
        """
fragment:
  version: 1
  description: both targets claim the same fixed key
operations:
  - op: set
    path: /hosts/shared-key
    value: "{{ target }}"
""",
    )
    cfg = _aggregate_config(agg_path=tmp_path / "out.yaml")
    session = render_mod.RenderSession(cfg, inventory_path, frag_dir, runner=stub_runner)
    rendered = session.render_aggregate("combined")
    assert rendered is not None
    assert len(rendered.overrides) == 1
    warning = rendered.overrides[0]
    assert "(target host-a)" in warning
    assert "(target host-b)" in warning


def test_aggregate_provenance_carries_target(tmp_path: Path, stub_runner) -> None:
    """Provenance entries produced during aggregate rendering carry the
    contributing target's name; per-target rendering leaves it `None`
    (redundant there)."""
    inventory_path = tmp_path / "targets.yaml"
    inventory_path.write_text(
        """
version: 1
targets:
  t1:
    outputs:
      combined:
        fragments: [mark]
    variables: {}
"""
    )
    frag_dir = tmp_path / "frags"
    _write_fragment(
        frag_dir,
        "mark",
        """
fragment:
  version: 1
  description: marks
operations:
  - op: set
    path: /value
    value: 1
""",
    )
    cfg = _aggregate_config(agg_path=tmp_path / "out.yaml")
    session = render_mod.RenderSession(cfg, inventory_path, frag_dir, runner=stub_runner)
    rendered = session.render_aggregate("combined")
    assert rendered is not None
    entry = rendered.provenance["/value"][-1]
    assert entry.target == "t1"

    # Per-target rendering of the same fragment (via a target-scoped output)
    # leaves `target` unset — it would be redundant with only one target.
    per_target_outputs = dict(cfg.outputs)
    per_target_outputs["solo"] = OutputSpec(path=str(tmp_path / "solo-{target}"))
    inventory_path.write_text(
        """
version: 1
targets:
  t1:
    outputs:
      solo:
        fragments: [mark]
    variables: {}
"""
    )
    cfg2 = ProjectConfig(
        version=1,
        inventory="unused",
        fragments_dir="unused",
        outputs=per_target_outputs,
        default_output="solo",
    )
    session2 = render_mod.RenderSession(cfg2, inventory_path, frag_dir, runner=stub_runner)
    result = session2.render_target_outputs("t1")
    solo_entry = result.outputs["solo"].provenance["/value"][-1]
    assert solo_entry.target is None


def test_captures_run_once_per_target_regardless_of_output_count(
    tmp_path: Path, stub_runner
) -> None:
    """Regression guard for the RenderSession memoization: a target's capture
    subprocess runs at most once per session, no matter how many outputs
    (per-target or aggregate) consume the resulting variable."""
    inventory_path = tmp_path / "targets.yaml"
    inventory_path.write_text(
        """
version: 1
targets:
  t1:
    outputs:
      out-a:
        fragments: [use-secret]
      out-b:
        fragments: [use-secret]
      combined:
        fragments: [use-secret]
    variables:
      secret_val:
        from: capture
        command: [echo, hi]
"""
    )
    frag_dir = tmp_path / "frags"
    _write_fragment(
        frag_dir,
        "use-secret",
        """
fragment:
  version: 1
  description: consumes the captured variable
requires:
  variables:
    - secret_val
operations:
  - op: set
    path: /value
    value: "{{ secret_val }}"
""",
    )
    cfg = ProjectConfig(
        version=1,
        inventory="unused",
        fragments_dir="unused",
        outputs={
            "out-a": OutputSpec(path=str(tmp_path / "a-{target}")),
            "out-b": OutputSpec(path=str(tmp_path / "b-{target}")),
            "combined": OutputSpec(path=str(tmp_path / "combined.yaml"), scope="aggregate"),
        },
        default_output=None,
    )
    session = render_mod.RenderSession(cfg, inventory_path, frag_dir, runner=stub_runner)

    result = session.render_target_outputs("t1")
    assert set(result.outputs) == {"out-a", "out-b"}
    session.render_aggregate("combined")

    assert len(stub_runner.calls) == 1


def test_aggregate_snapshot_matches_expected(
    config_path: Path,
    inventory_path: Path,
    fragments_dir: Path,
    secrets_example_path: Path,
    fixtures_dir: Path,
    stub_runner,
) -> None:
    """The example project's `ansible-inventory` aggregate output matches the
    committed snapshot, byte for byte."""
    cfg = config_mod.load_config(config_path)
    session = render_mod.RenderSession(
        cfg,
        inventory_path,
        fragments_dir,
        secrets_path=secrets_example_path,
        runner=stub_runner,
    )
    rendered = session.render_aggregate("ansible-inventory")
    assert rendered is not None
    text = render_mod.compose_output(rendered, cfg.outputs["ansible-inventory"])
    expected = (fixtures_dir / "expected" / "ansible-inventory.yaml").read_bytes()
    assert text.encode("utf-8") == expected


def test_render_target_outputs_skips_aggregate_scope(
    config_path: Path,
    inventory_path: Path,
    fragments_dir: Path,
    secrets_example_path: Path,
    stub_runner,
) -> None:
    """Per-target rendering never produces an aggregate-scoped output, even
    though gb10-01 contributes fragments to `ansible-inventory` (via
    `defaults`) — see README.md "Aggregate outputs"."""
    cfg = config_mod.load_config(config_path)
    session = render_mod.RenderSession(
        cfg,
        inventory_path,
        fragments_dir,
        secrets_path=secrets_example_path,
        runner=stub_runner,
    )
    result = session.render_target_outputs("gb10-01")
    assert "ansible-inventory" not in result.outputs
    assert set(result.outputs) == {"user-data", "meta-data"}
