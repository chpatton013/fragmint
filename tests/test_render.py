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

from yaml_frag import render as render_mod
from yaml_frag.errors import AssertionFailedError, ModuleError, ValidationError
from yaml_frag.models import OutputSpec

SNAPSHOT_TARGETS = ["generic-vm-01", "gb10-01", "gb10-02"]


def _render(target: str, *, example_closure, secrets_example_path: Path, stub_runner, validate: bool = True):
    session = render_mod.RenderSession(
        example_closure,
        secrets_path=secrets_example_path,
        runner=stub_runner,
    )
    result = session.render_target_outputs(target, validate=validate)
    return session, result


@pytest.mark.parametrize("target", SNAPSHOT_TARGETS)
@pytest.mark.parametrize("output_name", ["user-data", "meta-data"])
def test_snapshot_matches_expected(
    target: str,
    output_name: str,
    example_closure,
    secrets_example_path: Path,
    fixtures_dir: Path,
    stub_runner,
) -> None:
    """Rendered output equals the committed expected fixture, byte for byte."""
    session, result = _render(
        target, example_closure=example_closure, secrets_example_path=secrets_example_path, stub_runner=stub_runner
    )
    text = render_mod.compose_output(result.outputs[output_name], session.project.outputs[output_name])
    expected_path = fixtures_dir / "expected" / target / output_name
    expected = expected_path.read_bytes()
    assert text.encode("utf-8") == expected


def test_gb10_matches_readme_expected_render(
    example_closure, secrets_example_path: Path, stub_runner
) -> None:
    """gb10-01 renders the expected YAML shown in README.md's "Example project:
    Ubuntu autoinstall" section, using a stubbed CommandRunner for the
    capture-derived password hash."""
    session, result = _render(
        "gb10-01", example_closure=example_closure, secrets_example_path=secrets_example_path, stub_runner=stub_runner
    )
    text = render_mod.compose_output(result.outputs["user-data"], session.project.outputs["user-data"])

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
    example_closure, secrets_example_path: Path, stub_runner
) -> None:
    """The output template wraps the YAML with a `#cloud-config` header."""
    session, result = _render(
        "gb10-01", example_closure=example_closure, secrets_example_path=secrets_example_path, stub_runner=stub_runner
    )
    rendered = result.outputs["user-data"]
    text = render_mod.compose_output(rendered, session.project.outputs["user-data"])
    lines = text.splitlines()
    assert lines[0] == "#cloud-config"
    assert lines[1] == "autoinstall:"

    # Without a template, the header is absent.
    no_template_output = OutputSpec(path=session.project.outputs["user-data"].path, template=None)
    text_no_template = render_mod.compose_output(rendered, no_template_output)
    assert not text_no_template.startswith("#cloud-config")
    assert text_no_template.startswith("autoinstall:")


def test_gb10_hosts_differ_only_in_host_variables(
    example_closure, secrets_example_path: Path, stub_runner
) -> None:
    """gb10-01 and gb10-02 differ only where host variables differ."""
    _, result_1 = _render(
        "gb10-01", example_closure=example_closure, secrets_example_path=secrets_example_path, stub_runner=stub_runner
    )
    _, result_2 = _render(
        "gb10-02", example_closure=example_closure, secrets_example_path=secrets_example_path, stub_runner=stub_runner
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
    closure_from_tree, secrets_example_path: Path, stub_runner
) -> None:
    """A failed fragment `assert` (e.g. autoinstall/checks) fails the render with
    context. Structural checks are data, not built-in renderer logic."""
    closure = closure_from_tree(
        {
            "targets.yaml": """
version: 1
outputs:
  user-data:
    path: "rendered/{target}/user-data"
targets:
  broken:
    outputs:
      user-data:
        fragments:
          - failing-assert
    variables: {}
""",
            "fragments/failing-assert.yaml": """
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
""",
        }
    )
    with pytest.raises(AssertionFailedError) as excinfo:
        render_mod.render_target(
            "broken", closure=closure, secrets_path=secrets_example_path, runner=stub_runner
        )
    assert "/autoinstall/version" in str(excinfo.value)


def test_generic_validation_rejects_unresolved_markers(
    closure_from_tree, secrets_example_path: Path, stub_runner
) -> None:
    """Generic validation fails on leftover `{{`/`{%` markers.

    A variable whose own (literal) value happens to contain "{{" survives
    templating unchanged (rendering succeeds — the template was a single
    whole-variable reference), but the resulting document still contains an
    unresolved-looking marker, so generic validation must reject it.
    """
    closure = closure_from_tree(
        {
            "targets.yaml": """
version: 1
defaults:
  variables:
    echo_var: "{{ nested }}"
outputs:
  user-data:
    path: "rendered/{target}/user-data"
targets:
  broken:
    outputs:
      user-data:
        fragments:
          - echoes
    variables: {}
""",
            "fragments/echoes.yaml": """
fragment:
  version: 1
  description: echoes a variable whose value itself contains template braces
operations:
  - op: set
    path: /leftover
    value: "{{ echo_var }}"
""",
        }
    )
    with pytest.raises(ValidationError, match="unresolved template marker"):
        render_mod.render_target(
            "broken", closure=closure, secrets_path=secrets_example_path, runner=stub_runner
        )


def test_fragment_can_reference_current_target_name(
    closure_from_tree, secrets_example_path: Path, stub_runner
) -> None:
    """A fragment can reference the current target's own name via the
    reserved `{{ target }}` variable."""
    closure = closure_from_tree(
        {
            "targets.yaml": """
version: 1
outputs:
  user-data:
    path: "rendered/{target}/user-data"
targets:
  my-target:
    outputs:
      user-data:
        fragments:
          - names-itself
    variables: {}
""",
            "fragments/names-itself.yaml": """
fragment:
  version: 1
  description: echoes the reserved target variable
operations:
  - op: set
    path: /whoami
    value: "{{ target }}"
""",
        }
    )
    result = render_mod.render_target(
        "my-target", closure=closure, secrets_path=secrets_example_path, runner=stub_runner
    )
    assert result.outputs["user-data"].document["whoami"] == "my-target"


def test_fragment_can_reference_current_output_name(
    closure_from_tree, secrets_example_path: Path, stub_runner
) -> None:
    """A fragment shared by two outputs of the same target sees a different
    `{{ output }}` value for each — it's reserved per output, not per target."""
    closure = closure_from_tree(
        {
            "targets.yaml": """
version: 1
outputs:
  user-data:
    path: "rendered/{target}/user-data"
  meta-data:
    path: "rendered/{target}/meta-data"
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
""",
            "fragments/names-its-output.yaml": """
fragment:
  version: 1
  description: echoes the reserved output variable
operations:
  - op: set
    path: /which-output
    value: "{{ output }}"
""",
        }
    )
    result = render_mod.render_target(
        "my-target", closure=closure, secrets_path=secrets_example_path, runner=stub_runner
    )
    assert result.outputs["user-data"].document["which-output"] == "user-data"
    assert result.outputs["meta-data"].document["which-output"] == "meta-data"


def test_deterministic_output(example_closure, secrets_example_path: Path, stub_runner) -> None:
    """Rendering the same target twice yields identical bytes."""
    session, result_1 = _render(
        "gb10-01", example_closure=example_closure, secrets_example_path=secrets_example_path, stub_runner=stub_runner
    )
    _, result_2 = _render(
        "gb10-01", example_closure=example_closure, secrets_example_path=secrets_example_path, stub_runner=stub_runner
    )
    text_1 = render_mod.compose_output(result_1.outputs["user-data"], session.project.outputs["user-data"])
    text_2 = render_mod.compose_output(result_2.outputs["user-data"], session.project.outputs["user-data"])
    assert text_1 == text_2


def test_render_target_produces_every_declared_output(
    example_closure, secrets_example_path: Path, stub_runner
) -> None:
    """The example project's targets produce both `user-data` and `meta-data`."""
    _, result = _render(
        "gb10-01", example_closure=example_closure, secrets_example_path=secrets_example_path, stub_runner=stub_runner
    )
    assert set(result.outputs) == {"user-data", "meta-data"}
    assert result.outputs["meta-data"].document == {
        "instance-id": "gb10-01",
        "local-hostname": "gb10-01",
    }


def test_undeclared_output_not_produced(closure_from_tree, secrets_example_path: Path, stub_runner) -> None:
    """An output no layer contributes fragments to is simply absent from the
    result, not present-but-empty."""
    closure = closure_from_tree(
        {
            "targets.yaml": """
version: 1
outputs:
  user-data:
    path: "rendered/{target}/user-data"
targets:
  bare:
    outputs:
      user-data:
        fragments: []
    variables: {}
""",
        }
    )
    result = render_mod.render_target(
        "bare", closure=closure, secrets_path=secrets_example_path, runner=stub_runner
    )
    assert result.outputs == {}


def test_unknown_output_name_in_inventory_raises_module_error(
    closure_from_tree, secrets_example_path: Path, stub_runner
) -> None:
    """A target whose inventory references an output name absent from the
    closure fails with ModuleError."""
    closure = closure_from_tree(
        {
            "targets.yaml": """
version: 1
outputs:
  user-data:
    path: "rendered/{target}/user-data"
targets:
  t:
    outputs:
      not-declared-anywhere:
        fragments: [base]
    variables: {}
""",
        }
    )
    with pytest.raises(ModuleError):
        render_mod.render_target(
            "t", closure=closure, secrets_path=secrets_example_path, runner=stub_runner
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


def test_aggregate_composition_order_is_target_then_fragment_order(
    closure_from_tree, stub_runner
) -> None:
    """Aggregate composition visits targets in inventory declaration order
    (never alphabetized) and, within each target, applies that target's
    resolved fragment list in order — the same positional rule as per-target
    rendering, across targets instead of within one."""
    closure = closure_from_tree(
        {
            "targets.yaml": """
version: 1
outputs:
  combined:
    scope: aggregate
    path: "out.yaml"
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
""",
            "fragments/append-a.yaml": """
fragment:
  version: 1
  description: appends "<target>-a"
operations:
  - op: append
    path: /log
    value: ["{{ target }}-a"]
""",
            "fragments/append-b.yaml": """
fragment:
  version: 1
  description: appends "<target>-b"
operations:
  - op: append
    path: /log
    value: ["{{ target }}-b"]
""",
        }
    )
    session = render_mod.RenderSession(closure, runner=stub_runner)
    rendered = session.render_aggregate("combined")
    assert rendered is not None
    assert rendered.document["log"] == ["second-a", "second-b", "first-a", "first-b"]


def test_aggregate_target_opt_out(closure_from_tree, stub_runner) -> None:
    """A target that contributes no fragments for an aggregate output simply
    doesn't appear in it — that's the opt-out (README.md "Aggregate
    outputs")."""
    closure = closure_from_tree(
        {
            "targets.yaml": """
version: 1
outputs:
  combined:
    scope: aggregate
    path: "out.yaml"
targets:
  contributes:
    outputs:
      combined:
        fragments: [mark]
    variables: {}
  opts-out:
    variables: {}
""",
            "fragments/mark.yaml": """
fragment:
  version: 1
  description: marks the contributing target
operations:
  - op: set
    path: "/seen/{{ target }}"
    value: true
""",
        }
    )
    session = render_mod.RenderSession(closure, runner=stub_runner)
    rendered = session.render_aggregate("combined")
    assert rendered is not None
    assert rendered.document == {"seen": {"contributes": True}}


def test_aggregate_no_contributors_is_not_produced(closure_from_tree, stub_runner) -> None:
    """If no target contributes to an aggregate output, it's not produced —
    render_aggregate returns None, consistent with "an output no layer
    contributes fragments to is not produced"."""
    closure = closure_from_tree(
        {
            "targets.yaml": """
version: 1
outputs:
  combined:
    scope: aggregate
    path: "out.yaml"
targets:
  t1:
    variables: {}
  t2:
    variables: {}
""",
        }
    )
    session = render_mod.RenderSession(closure, runner=stub_runner)
    assert session.render_aggregate("combined") is None


def test_aggregate_cross_target_override_warning_names_both_targets(
    closure_from_tree, stub_runner
) -> None:
    """Two targets writing the same path in an aggregate document is exactly
    the "two targets claimed the same key" bug the override warning should
    catch — and for aggregate scope the warning names both contributing
    targets, not just fragment names (README.md "Aggregate outputs")."""
    closure = closure_from_tree(
        {
            "targets.yaml": """
version: 1
outputs:
  combined:
    scope: aggregate
    path: "out.yaml"
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
""",
            "fragments/claim.yaml": """
fragment:
  version: 1
  description: both targets claim the same fixed key
operations:
  - op: set
    path: /hosts/shared-key
    value: "{{ target }}"
""",
        }
    )
    session = render_mod.RenderSession(closure, runner=stub_runner)
    rendered = session.render_aggregate("combined")
    assert rendered is not None
    assert len(rendered.overrides) == 1
    warning = rendered.overrides[0]
    assert "(target host-a)" in warning
    assert "(target host-b)" in warning


def test_aggregate_provenance_carries_target(closure_from_tree, stub_runner) -> None:
    """Provenance entries produced during aggregate rendering carry the
    contributing target's name; per-target rendering leaves it `None`
    (redundant there)."""
    closure = closure_from_tree(
        {
            "targets.yaml": """
version: 1
outputs:
  combined:
    scope: aggregate
    path: "out.yaml"
  solo:
    path: "solo-{target}"
targets:
  t1:
    outputs:
      combined:
        fragments: [mark]
      solo:
        fragments: [mark]
    variables: {}
""",
            "fragments/mark.yaml": """
fragment:
  version: 1
  description: marks
operations:
  - op: set
    path: /value
    value: 1
""",
        }
    )
    session = render_mod.RenderSession(closure, runner=stub_runner)
    rendered = session.render_aggregate("combined")
    assert rendered is not None
    entry = rendered.provenance["/value"][-1]
    assert entry.target == "t1"

    # Per-target rendering of the same fragment (via a target-scoped output)
    # leaves `target` unset — it would be redundant with only one target.
    result = session.render_target_outputs("t1")
    solo_entry = result.outputs["solo"].provenance["/value"][-1]
    assert solo_entry.target is None


def test_captures_run_once_per_target_regardless_of_output_count(
    closure_from_tree, stub_runner
) -> None:
    """Regression guard for the RenderSession memoization: a target's capture
    subprocess runs at most once per session, no matter how many outputs
    (per-target or aggregate) consume the resulting variable."""
    closure = closure_from_tree(
        {
            "targets.yaml": """
version: 1
outputs:
  out-a:
    path: "a-{target}"
  out-b:
    path: "b-{target}"
  combined:
    scope: aggregate
    path: "combined.yaml"
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
""",
            "fragments/use-secret.yaml": """
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
        }
    )
    session = render_mod.RenderSession(closure, runner=stub_runner)

    result = session.render_target_outputs("t1")
    assert set(result.outputs) == {"out-a", "out-b"}
    session.render_aggregate("combined")

    assert len(stub_runner.calls) == 1


def test_aggregate_snapshot_matches_expected(
    example_closure, secrets_example_path: Path, fixtures_dir: Path, stub_runner
) -> None:
    """The example project's `ansible-inventory` aggregate output matches the
    committed snapshot, byte for byte."""
    session = render_mod.RenderSession(
        example_closure, secrets_path=secrets_example_path, runner=stub_runner
    )
    rendered = session.render_aggregate("ansible-inventory")
    assert rendered is not None
    text = render_mod.compose_output(rendered, session.project.outputs["ansible-inventory"])
    expected = (fixtures_dir / "expected" / "ansible-inventory.yaml").read_bytes()
    assert text.encode("utf-8") == expected


def test_render_target_outputs_skips_aggregate_scope(
    example_closure, secrets_example_path: Path, stub_runner
) -> None:
    """Per-target rendering never produces an aggregate-scoped output, even
    though gb10-01 contributes fragments to `ansible-inventory` (via a
    module's `defaults`) — see README.md "Aggregate outputs"."""
    session = render_mod.RenderSession(
        example_closure, secrets_path=secrets_example_path, runner=stub_runner
    )
    result = session.render_target_outputs("gb10-01")
    assert "ansible-inventory" not in result.outputs
    assert set(result.outputs) == {"user-data", "meta-data"}


# --- aggregate: epilogue (README.md "Aggregate outputs") --------------------


def test_aggregate_epilogue_runs_after_every_target(closure_from_tree, stub_runner) -> None:
    """An epilogue `append` lands after every target's own append, in target
    order — outside the per-target loop, applied last."""
    closure = closure_from_tree(
        {
            "targets.yaml": """
version: 1
outputs:
  combined:
    scope: aggregate
    path: "out.yaml"
aggregate:
  outputs:
    combined:
      epilogue: [append-epilogue]
targets:
  first:
    outputs:
      combined:
        fragments: [append-target]
    variables: {}
  second:
    outputs:
      combined:
        fragments: [append-target]
    variables: {}
""",
            "fragments/append-target.yaml": """
fragment:
  version: 1
  description: appends "<target>"
operations:
  - op: append
    path: /log
    value: ["{{ target }}"]
""",
            "fragments/append-epilogue.yaml": """
fragment:
  version: 1
  description: appends "epilogue"
operations:
  - op: append
    path: /log
    value: ["epilogue"]
""",
        }
    )
    session = render_mod.RenderSession(closure, runner=stub_runner)
    rendered = session.render_aggregate("combined")
    assert rendered is not None
    assert rendered.document["log"] == ["first", "second", "epilogue"]


def test_aggregate_epilogue_assertion_sees_whole_document(closure_from_tree, stub_runner) -> None:
    """Two targets each add a host; an epilogue assertion requiring both to be
    present passes, because it runs after every target has contributed — the
    negative control below shows the same assertion inside a per-target
    fragment fails, which is the entire point of the feature."""
    closure = closure_from_tree(
        {
            "targets.yaml": """
version: 1
outputs:
  combined:
    scope: aggregate
    path: "out.yaml"
aggregate:
  outputs:
    combined:
      epilogue: [checks]
targets:
  host-a:
    outputs:
      combined:
        fragments: [add-host]
    variables: {}
  host-b:
    outputs:
      combined:
        fragments: [add-host]
    variables: {}
""",
            "fragments/add-host.yaml": """
fragment:
  version: 1
  description: adds this target as a host
operations:
  - op: set
    path: "/hosts/{{ target }}"
    value: true
""",
            "fragments/checks.yaml": """
fragment:
  version: 1
  description: both hosts must be present
operations:
  - op: assert
    path: /hosts/host-a
    exists: true
  - op: assert
    path: /hosts/host-b
    exists: true
""",
        }
    )
    session = render_mod.RenderSession(closure, runner=stub_runner)
    rendered = session.render_aggregate("combined")
    assert rendered is not None
    assert rendered.document == {"hosts": {"host-a": True, "host-b": True}}


def test_aggregate_epilogue_assertion_negative_control_partial_document(
    closure_from_tree, stub_runner
) -> None:
    """The same "both hosts present" assertion placed in a PER-TARGET
    aggregate fragment fails, because it runs during that target's own turn
    and sees only the partial document built so far — the contrast that
    justifies the epilogue mechanism."""
    closure = closure_from_tree(
        {
            "targets.yaml": """
version: 1
outputs:
  combined:
    scope: aggregate
    path: "out.yaml"
targets:
  host-a:
    outputs:
      combined:
        fragments: [add-host, checks]
    variables: {}
  host-b:
    outputs:
      combined:
        fragments: [add-host]
    variables: {}
""",
            "fragments/add-host.yaml": """
fragment:
  version: 1
  description: adds this target as a host
operations:
  - op: set
    path: "/hosts/{{ target }}"
    value: true
""",
            "fragments/checks.yaml": """
fragment:
  version: 1
  description: both hosts must be present -- but this runs during host-a's turn
operations:
  - op: assert
    path: /hosts/host-a
    exists: true
  - op: assert
    path: /hosts/host-b
    exists: true
""",
        }
    )
    session = render_mod.RenderSession(closure, runner=stub_runner)
    with pytest.raises(AssertionFailedError):
        session.render_aggregate("combined")


def test_aggregate_epilogue_assertion_failure_raises_assertion_failed(
    closure_from_tree, stub_runner
) -> None:
    closure = closure_from_tree(
        {
            "targets.yaml": """
version: 1
outputs:
  combined:
    scope: aggregate
    path: "out.yaml"
aggregate:
  outputs:
    combined:
      epilogue: [checks]
targets:
  t1:
    outputs:
      combined:
        fragments: [mark]
    variables: {}
""",
            "fragments/mark.yaml": """
fragment:
  version: 1
  description: marks
operations:
  - op: set
    path: /value
    value: 1
""",
            "fragments/checks.yaml": """
fragment:
  version: 1
  description: fails on purpose
operations:
  - op: assert
    path: /value
    equals: 2
""",
        }
    )
    session = render_mod.RenderSession(closure, runner=stub_runner)
    with pytest.raises(AssertionFailedError):
        session.render_aggregate("combined")


def test_target_variable_is_undefined_in_aggregate_scope(closure_from_tree, stub_runner) -> None:
    """`{{ target }}` in an epilogue fragment raises TemplateRenderError,
    naming the aggregate epilogue scope label."""
    from yaml_frag.errors import TemplateRenderError

    closure = closure_from_tree(
        {
            "targets.yaml": """
version: 1
outputs:
  combined:
    scope: aggregate
    path: "out.yaml"
aggregate:
  outputs:
    combined:
      epilogue: [uses-target]
targets:
  t1:
    outputs:
      combined:
        fragments: [mark]
    variables: {}
""",
            "fragments/mark.yaml": """
fragment:
  version: 1
  description: marks
operations:
  - op: set
    path: /value
    value: 1
""",
            "fragments/uses-target.yaml": """
fragment:
  version: 1
  description: refers to the undefined `target`
operations:
  - op: set
    path: /bad
    value: "{{ target }}"
""",
        }
    )
    session = render_mod.RenderSession(closure, runner=stub_runner)
    with pytest.raises(TemplateRenderError) as exc_info:
        session.render_aggregate("combined")
    assert "target" in str(exc_info.value)
    assert "<aggregate combined epilogue>" in str(exc_info.value)


def test_required_target_variable_in_aggregate_scope_fails_closed(
    closure_from_tree, stub_runner
) -> None:
    from yaml_frag.errors import TemplateRenderError

    closure = closure_from_tree(
        {
            "targets.yaml": """
version: 1
outputs:
  combined:
    scope: aggregate
    path: "out.yaml"
aggregate:
  outputs:
    combined:
      epilogue: [requires-target]
targets:
  t1:
    outputs:
      combined:
        fragments: [mark]
    variables: {}
""",
            "fragments/mark.yaml": """
fragment:
  version: 1
  description: marks
operations:
  - op: set
    path: /value
    value: 1
""",
            "fragments/requires-target.yaml": """
fragment:
  version: 1
  description: requires the undefined `target`
requires:
  variables:
    - target
operations: []
""",
        }
    )
    session = render_mod.RenderSession(closure, runner=stub_runner)
    with pytest.raises(TemplateRenderError):
        session.render_aggregate("combined")


def test_output_variable_is_set_in_aggregate_scope(closure_from_tree, stub_runner) -> None:
    closure = closure_from_tree(
        {
            "targets.yaml": """
version: 1
outputs:
  combined:
    scope: aggregate
    path: "out.yaml"
aggregate:
  outputs:
    combined:
      epilogue: [uses-output]
targets:
  t1:
    outputs:
      combined:
        fragments: [mark]
    variables: {}
""",
            "fragments/mark.yaml": """
fragment:
  version: 1
  description: marks
operations:
  - op: set
    path: /value
    value: 1
""",
            "fragments/uses-output.yaml": """
fragment:
  version: 1
  description: refers to the current output's own name
operations:
  - op: set
    path: /output_name
    value: "{{ output }}"
""",
        }
    )
    session = render_mod.RenderSession(closure, runner=stub_runner)
    rendered = session.render_aggregate("combined")
    assert rendered is not None
    assert rendered.document["output_name"] == "combined"


def test_aggregate_scope_variables_layer_defaults_then_aggregate(
    closure_from_tree, stub_runner
) -> None:
    closure = closure_from_tree(
        {
            "targets.yaml": """
version: 1
outputs:
  combined:
    scope: aggregate
    path: "out.yaml"
defaults:
  variables:
    x: from-defaults
    y: from-defaults-only
aggregate:
  variables:
    x: from-aggregate
  outputs:
    combined:
      epilogue: [record]
targets:
  t1:
    outputs:
      combined:
        fragments: [mark]
    variables: {}
""",
            "fragments/mark.yaml": """
fragment:
  version: 1
  description: marks
operations:
  - op: set
    path: /value
    value: 1
""",
            "fragments/record.yaml": """
fragment:
  version: 1
  description: records the layered variables
operations:
  - op: set
    path: /x
    value: "{{ x }}"
  - op: set
    path: /y
    value: "{{ y }}"
""",
        }
    )
    session = render_mod.RenderSession(closure, runner=stub_runner)
    rendered = session.render_aggregate("combined")
    assert rendered is not None
    assert rendered.document["x"] == "from-aggregate"
    assert rendered.document["y"] == "from-defaults-only"


@pytest.mark.parametrize("reserved_name", ["target", "output"])
def test_aggregate_scope_rejects_reserved_variable_names(
    closure_from_tree, stub_runner, reserved_name: str
) -> None:
    from yaml_frag.errors import InventoryError

    closure = closure_from_tree(
        {
            "targets.yaml": f"""
version: 1
outputs:
  combined:
    scope: aggregate
    path: "out.yaml"
aggregate:
  variables:
    {reserved_name}: x
  outputs:
    combined:
      epilogue: [mark]
targets:
  t1:
    outputs:
      combined:
        fragments: [mark]
    variables: {{}}
""",
            "fragments/mark.yaml": """
fragment:
  version: 1
  description: marks
operations:
  - op: set
    path: /value
    value: 1
""",
        }
    )
    session = render_mod.RenderSession(closure, runner=stub_runner)
    with pytest.raises(InventoryError):
        session.render_aggregate("combined")


def test_aggregate_epilogue_skipped_when_no_target_contributes(
    closure_from_tree, stub_runner
) -> None:
    """An epilogue exists but no target contributes: render_aggregate returns
    None, and the epilogue's assertions (which would fail on an empty
    document) never run."""
    closure = closure_from_tree(
        {
            "targets.yaml": """
version: 1
outputs:
  combined:
    scope: aggregate
    path: "out.yaml"
aggregate:
  outputs:
    combined:
      epilogue: [checks]
targets:
  t1:
    variables: {}
""",
            "fragments/checks.yaml": """
fragment:
  version: 1
  description: would fail on an empty document
operations:
  - op: assert
    path: /value
    exists: true
""",
        }
    )
    session = render_mod.RenderSession(closure, runner=stub_runner)
    assert session.render_aggregate("combined") is None


def test_aggregate_scope_variables_not_resolved_without_prologue_or_epilogue(
    closure_from_tree, stub_runner
) -> None:
    """The `has_outer` laziness guard: a `defaults.variables` capture plus an
    aggregate output with no `aggregate:` block leaves the stub runner's call
    count at the per-target count -- the aggregate scope's own variable
    resolution never runs."""
    closure = closure_from_tree(
        {
            "targets.yaml": """
version: 1
outputs:
  combined:
    scope: aggregate
    path: "out.yaml"
defaults:
  variables:
    secret_val:
      from: capture
      command: [echo, hi]
targets:
  t1:
    outputs:
      combined:
        fragments: [use-secret]
    variables: {}
""",
            "fragments/use-secret.yaml": """
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
        }
    )
    session = render_mod.RenderSession(closure, runner=stub_runner)
    session.render_aggregate("combined")
    assert len(stub_runner.calls) == 1


def test_aggregate_epilogue_runs_even_with_validate_false(closure_from_tree, stub_runner) -> None:
    """Epilogue assertions run during composition, not validation, so
    `validate=False` does not suppress a failing epilogue assertion."""
    closure = closure_from_tree(
        {
            "targets.yaml": """
version: 1
outputs:
  combined:
    scope: aggregate
    path: "out.yaml"
aggregate:
  outputs:
    combined:
      epilogue: [checks]
targets:
  t1:
    outputs:
      combined:
        fragments: [mark]
    variables: {}
""",
            "fragments/mark.yaml": """
fragment:
  version: 1
  description: marks
operations:
  - op: set
    path: /value
    value: 1
""",
            "fragments/checks.yaml": """
fragment:
  version: 1
  description: fails on purpose
operations:
  - op: assert
    path: /value
    equals: 2
""",
        }
    )
    session = render_mod.RenderSession(closure, runner=stub_runner)
    with pytest.raises(AssertionFailedError):
        session.render_aggregate("combined", validate=False)


# --- aggregate: prologue (README.md "Aggregate outputs") --------------------


def test_aggregate_prologue_runs_before_every_target(closure_from_tree, stub_runner) -> None:
    """A prologue `append` lands before every target's own append, in target
    order -- outside the per-target loop, applied first."""
    closure = closure_from_tree(
        {
            "targets.yaml": """
version: 1
outputs:
  combined:
    scope: aggregate
    path: "out.yaml"
aggregate:
  outputs:
    combined:
      prologue: [append-prologue]
targets:
  first:
    outputs:
      combined:
        fragments: [append-target]
    variables: {}
  second:
    outputs:
      combined:
        fragments: [append-target]
    variables: {}
""",
            "fragments/append-target.yaml": """
fragment:
  version: 1
  description: appends "<target>"
operations:
  - op: append
    path: /log
    value: ["{{ target }}"]
""",
            "fragments/append-prologue.yaml": """
fragment:
  version: 1
  description: appends "prologue"
operations:
  - op: append
    path: /log
    value: ["prologue"]
""",
        }
    )
    session = render_mod.RenderSession(closure, runner=stub_runner)
    rendered = session.render_aggregate("combined")
    assert rendered is not None
    assert rendered.document["log"] == ["prologue", "first", "second"]


def test_aggregate_prologue_epilogue_order_across_contributing_documents(
    closure_from_tree, stub_runner
) -> None:
    """A module and the inventory each contribute a prologue and an epilogue;
    all four positions land in the expected order: module-prologue,
    inventory-prologue, targets, module-epilogue, inventory-epilogue."""
    closure = closure_from_tree(
        {
            "targets.yaml": """
version: 1
imports:
  a: modules/a
outputs:
  combined:
    scope: aggregate
    path: "out.yaml"
aggregate:
  outputs:
    combined:
      prologue: [inv-pre]
      epilogue: [inv-post]
targets:
  t1:
    outputs:
      combined:
        fragments: [mark]
    variables: {}
""",
            "fragments/inv-pre.yaml": """
fragment:
  version: 1
  description: inventory prologue
operations:
  - op: append
    path: /log
    value: [inv-pre]
""",
            "fragments/inv-post.yaml": """
fragment:
  version: 1
  description: inventory epilogue
operations:
  - op: append
    path: /log
    value: [inv-post]
""",
            "fragments/mark.yaml": """
fragment:
  version: 1
  description: target's own contribution
operations:
  - op: append
    path: /log
    value: ["{{ target }}"]
""",
            "modules/a/yaml-frag.yaml": """
version: 1
aggregate:
  outputs:
    combined:
      prologue: [mod-pre]
      epilogue: [mod-post]
""",
            "modules/a/fragments/mod-pre.yaml": """
fragment:
  version: 1
  description: module prologue
operations:
  - op: append
    path: /log
    value: [mod-pre]
""",
            "modules/a/fragments/mod-post.yaml": """
fragment:
  version: 1
  description: module epilogue
operations:
  - op: append
    path: /log
    value: [mod-post]
""",
        }
    )
    session = render_mod.RenderSession(closure, runner=stub_runner)
    rendered = session.render_aggregate("combined")
    assert rendered is not None
    assert rendered.document["log"] == ["mod-pre", "inv-pre", "t1", "mod-post", "inv-post"]


def test_aggregate_prologue_provenance_has_no_target(closure_from_tree, stub_runner) -> None:
    """A prologue provenance entry's `.target` is `None`; a target's own
    contribution in the same document carries its target name."""
    closure = closure_from_tree(
        {
            "targets.yaml": """
version: 1
outputs:
  combined:
    scope: aggregate
    path: "out.yaml"
aggregate:
  outputs:
    combined:
      prologue: [pre]
targets:
  t1:
    outputs:
      combined:
        fragments: [mark]
    variables: {}
""",
            "fragments/pre.yaml": """
fragment:
  version: 1
  description: prologue
operations:
  - op: set
    path: /pre_value
    value: 1
""",
            "fragments/mark.yaml": """
fragment:
  version: 1
  description: marks
operations:
  - op: set
    path: /value
    value: 1
""",
        }
    )
    session = render_mod.RenderSession(closure, runner=stub_runner)
    rendered = session.render_aggregate("combined")
    assert rendered is not None
    pre_entry = rendered.provenance["/pre_value"][-1]
    assert pre_entry.target is None
    target_entry = rendered.provenance["/value"][-1]
    assert target_entry.target == "t1"


def test_prologue_then_target_override_warning_names_only_the_target_it_has(
    closure_from_tree, stub_runner
) -> None:
    """A prologue write later overridden by a target's write produces one
    override warning whose `new source` carries a `(target ...)`
    parenthetical and whose `previous source` does not."""
    closure = closure_from_tree(
        {
            "targets.yaml": """
version: 1
outputs:
  combined:
    scope: aggregate
    path: "out.yaml"
aggregate:
  outputs:
    combined:
      prologue: [pre]
targets:
  t1:
    outputs:
      combined:
        fragments: [claim]
    variables: {}
""",
            "fragments/pre.yaml": """
fragment:
  version: 1
  description: prologue claims the key first
operations:
  - op: set
    path: /shared
    value: pre
""",
            "fragments/claim.yaml": """
fragment:
  version: 1
  description: the target overrides it
operations:
  - op: set
    path: /shared
    value: "{{ target }}"
    overwrite_ok: false
""",
        }
    )
    session = render_mod.RenderSession(closure, runner=stub_runner)
    rendered = session.render_aggregate("combined")
    assert rendered is not None
    assert len(rendered.overrides) == 1
    warning = rendered.overrides[0]
    assert "new source" in warning
    new_source_line = next(line for line in warning.splitlines() if "new source" in line)
    previous_source_line = next(line for line in warning.splitlines() if "previous source" in line)
    assert "(target t1)" in new_source_line
    assert "(target " not in previous_source_line
