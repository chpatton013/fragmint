"""Project-configuration tests. See README.md "Project configuration"."""

from __future__ import annotations

from pathlib import Path

import pytest

from yaml_frag.config import (
    load_config,
    resolve_aggregate_output_path,
    resolve_output_path,
    select_validators,
)
from yaml_frag.errors import ConfigError
from yaml_frag.models import OutputSpec, ProjectConfig, ValidatorSpec


def _write(path: Path, text: str) -> Path:
    path.write_text(text)
    return path


def test_load_defaults(tmp_path: Path) -> None:
    """Omitted optional fields fall back to documented defaults, anchored to
    the config file's own directory (portability: see README.md "Project
    configuration")."""
    config_path = _write(
        tmp_path / "yaml-frag.yaml",
        "version: 1\noutputs:\n  main:\n    path: rendered/{target}\n",
    )
    config = load_config(config_path)
    assert config.version == 1
    assert config.inventory == str(tmp_path / "inventory/targets.yaml")
    assert config.fragments_dir == str(tmp_path / "fragments")
    assert config.outputs["main"].path == str(tmp_path / "rendered/{target}")
    assert config.outputs["main"].template is None
    assert config.outputs["main"].schema is None
    assert config.outputs["main"].validators == ()
    assert config.validators == {}


def test_paths_resolve_relative_to_config_directory(tmp_path: Path) -> None:
    """A project's declared paths are anchored to the config file's directory,
    not the process's working directory, so the project stays portable when
    invoked with a different CWD (README.md "Project configuration")."""
    project_dir = tmp_path / "some" / "nested" / "project"
    project_dir.mkdir(parents=True)
    config_path = _write(
        project_dir / "yaml-frag.yaml",
        "version: 1\n"
        "inventory: inventory/targets.yaml\n"
        "fragments_dir: fragments\n"
        "outputs:\n"
        "  user-data:\n"
        "    path: rendered/{target}/user-data\n"
        "    template: templates/user-data.tmpl\n",
    )
    config = load_config(config_path)
    assert config.inventory == str(project_dir / "inventory/targets.yaml")
    assert config.fragments_dir == str(project_dir / "fragments")
    assert config.outputs["user-data"].path == str(project_dir / "rendered/{target}/user-data")
    assert config.outputs["user-data"].template == str(project_dir / "templates/user-data.tmpl")


def test_absolute_paths_pass_through_unchanged(tmp_path: Path) -> None:
    """An already-absolute path in the config is not re-anchored."""
    abs_inventory = tmp_path / "elsewhere" / "targets.yaml"
    config_path = _write(
        tmp_path / "yaml-frag.yaml",
        f"version: 1\ninventory: {abs_inventory}\noutputs:\n  main:\n    path: rendered/{{target}}\n",
    )
    config = load_config(config_path)
    assert config.inventory == str(abs_inventory)


def test_output_path_substitution() -> None:
    """`{target}` in an output's path is substituted with the target name."""
    output = OutputSpec(path="rendered/{target}/user-data")
    path = resolve_output_path(output, "gb10-01")
    assert path == Path("rendered/gb10-01/user-data")


def test_output_path_override() -> None:
    """An explicit override path is used verbatim, ignoring the pattern."""
    output = OutputSpec(path="rendered/{target}/user-data")
    path = resolve_output_path(output, "gb10-01", override=Path("/tmp/out"))
    assert path == Path("/tmp/out")


def test_output_template_injects_document_token(tmp_path: Path) -> None:
    """The serialized YAML replaces the literal `{{ document }}` token."""
    template_path = _write(tmp_path / "tmpl.txt", "#cloud-config\n{{ document }}")
    config_path = _write(
        tmp_path / "yaml-frag.yaml",
        f"version: 1\noutputs:\n  main:\n    path: rendered/{{target}}\n    template: {template_path}\n",
    )
    config = load_config(config_path)
    assert config.outputs["main"].template == str(template_path)

    template_text = Path(config.outputs["main"].template).read_text()
    rendered = template_text.replace("{{ document }}", "autoinstall:\n  version: 1\n")
    assert rendered == "#cloud-config\nautoinstall:\n  version: 1\n"


def test_no_template_writes_yaml_verbatim(tmp_path: Path) -> None:
    """With no output template, the serialized YAML is written unchanged."""
    config_path = _write(
        tmp_path / "yaml-frag.yaml",
        "version: 1\noutputs:\n  main:\n    path: rendered/{target}\n",
    )
    config = load_config(config_path)
    assert config.outputs["main"].template is None


def test_single_output_is_implicit_default(tmp_path: Path) -> None:
    """A project with exactly one output treats it as the default, even
    without `default: true` (README.md "CLI usage")."""
    config_path = _write(
        tmp_path / "yaml-frag.yaml",
        "version: 1\noutputs:\n  main:\n    path: rendered/{target}\n",
    )
    config = load_config(config_path)
    assert config.default_output == "main"


def test_marked_default_output_among_several(tmp_path: Path) -> None:
    """With multiple outputs, the one marked `default: true` wins."""
    config_path = _write(
        tmp_path / "yaml-frag.yaml",
        "version: 1\n"
        "outputs:\n"
        "  user-data:\n"
        "    path: rendered/{target}/user-data\n"
        "    default: true\n"
        "  meta-data:\n"
        "    path: rendered/{target}/meta-data\n",
    )
    config = load_config(config_path)
    assert config.default_output == "user-data"


def test_no_default_among_several_unmarked_outputs(tmp_path: Path) -> None:
    """With multiple outputs and none marked default, there's no implicit
    default output."""
    config_path = _write(
        tmp_path / "yaml-frag.yaml",
        "version: 1\n"
        "outputs:\n"
        "  user-data:\n"
        "    path: rendered/{target}/user-data\n"
        "  meta-data:\n"
        "    path: rendered/{target}/meta-data\n",
    )
    config = load_config(config_path)
    assert config.default_output is None


def test_multiple_default_outputs_rejected(tmp_path: Path) -> None:
    """At most one output may be marked `default: true`."""
    config_path = _write(
        tmp_path / "yaml-frag.yaml",
        "version: 1\n"
        "outputs:\n"
        "  user-data:\n"
        "    path: rendered/{target}/user-data\n"
        "    default: true\n"
        "  meta-data:\n"
        "    path: rendered/{target}/meta-data\n"
        "    default: true\n",
    )
    with pytest.raises(ConfigError):
        load_config(config_path)


def test_validator_selection_resolution() -> None:
    """--validator names override an output's defaults; unknown names raise
    ConfigError."""
    output = OutputSpec(path="rendered/{target}", validators=("subiquity",))
    config = ProjectConfig(
        version=1,
        inventory="inventory/targets.yaml",
        fragments_dir="fragments",
        outputs={"main": output},
        default_output="main",
        validators={
            "subiquity": ValidatorSpec(name="subiquity", command=("true",)),
            "other": ValidatorSpec(name="other", command=("true",)),
        },
    )
    assert select_validators(config, output, ()) == ("subiquity",)
    assert select_validators(config, output, ("other",)) == ("other",)
    with pytest.raises(ConfigError):
        select_validators(config, output, ("nope",))


def test_output_validator_not_declared_raises(tmp_path: Path) -> None:
    """An output's default validator must be declared in the top-level
    `validators` map."""
    config_path = _write(
        tmp_path / "yaml-frag.yaml",
        "version: 1\n"
        "outputs:\n"
        "  main:\n"
        "    path: rendered/{target}\n"
        "    validators: [nope]\n",
    )
    with pytest.raises(ConfigError):
        load_config(config_path)


def test_invalid_config_raises_config_error(tmp_path: Path) -> None:
    """A malformed or wrong-version project config raises ConfigError (exit 7)."""
    missing = tmp_path / "does-not-exist.yaml"
    with pytest.raises(ConfigError):
        load_config(missing)

    wrong_version = _write(
        tmp_path / "yaml-frag.yaml",
        "version: 2\noutputs:\n  main:\n    path: rendered/{target}\n",
    )
    with pytest.raises(ConfigError):
        load_config(wrong_version)

    malformed = _write(tmp_path / "bad.yaml", "not: [valid, project: config\n")
    with pytest.raises(ConfigError):
        load_config(malformed)

    no_outputs = _write(tmp_path / "empty-outputs.yaml", "version: 1\noutputs: {}\n")
    with pytest.raises(ConfigError):
        load_config(no_outputs)


# --- Aggregate scope (README.md "Aggregate outputs") ------------------


def test_scope_defaults_to_target(tmp_path: Path) -> None:
    """An output with no `scope` field is `scope: target`, fully backward
    compatible with configs written before aggregate outputs existed."""
    config_path = _write(
        tmp_path / "yaml-frag.yaml",
        "version: 1\noutputs:\n  main:\n    path: rendered/{target}\n",
    )
    config = load_config(config_path)
    assert config.outputs["main"].scope == "target"


def test_scope_aggregate_parsed(tmp_path: Path) -> None:
    """`scope: aggregate` is parsed through onto the OutputSpec."""
    config_path = _write(
        tmp_path / "yaml-frag.yaml",
        "version: 1\n"
        "outputs:\n"
        "  ansible-inventory:\n"
        "    scope: aggregate\n"
        "    path: rendered/inventory.yaml\n",
    )
    config = load_config(config_path)
    assert config.outputs["ansible-inventory"].scope == "aggregate"


def test_scope_invalid_value_rejected(tmp_path: Path) -> None:
    """An unrecognized `scope` value is a config error."""
    config_path = _write(
        tmp_path / "yaml-frag.yaml",
        "version: 1\n"
        "outputs:\n"
        "  main:\n"
        "    scope: nonsense\n"
        "    path: rendered/{target}\n",
    )
    with pytest.raises(ConfigError):
        load_config(config_path)


def test_aggregate_output_rejects_target_placeholder_in_path(tmp_path: Path) -> None:
    """`scope: aggregate` + `{target}` in `path` is a config error: an
    aggregate output is a single fixed file, not per-target."""
    config_path = _write(
        tmp_path / "yaml-frag.yaml",
        "version: 1\n"
        "outputs:\n"
        "  ansible-inventory:\n"
        "    scope: aggregate\n"
        "    path: rendered/{target}/inventory.yaml\n",
    )
    with pytest.raises(ConfigError):
        load_config(config_path)


def test_aggregate_output_rejects_default_true(tmp_path: Path) -> None:
    """`scope: aggregate` + `default: true` is a config error: `default`
    exists solely to disambiguate per-target commands."""
    config_path = _write(
        tmp_path / "yaml-frag.yaml",
        "version: 1\n"
        "outputs:\n"
        "  ansible-inventory:\n"
        "    scope: aggregate\n"
        "    path: rendered/inventory.yaml\n"
        "    default: true\n",
    )
    with pytest.raises(ConfigError):
        load_config(config_path)


def test_target_scope_allows_default_and_target_placeholder(tmp_path: Path) -> None:
    """The aggregate-scope restrictions don't apply to `scope: target`
    outputs: `{target}` in the path and `default: true` are both valid."""
    config_path = _write(
        tmp_path / "yaml-frag.yaml",
        "version: 1\n"
        "outputs:\n"
        "  user-data:\n"
        "    path: rendered/{target}/user-data\n"
        "    default: true\n",
    )
    config = load_config(config_path)
    assert config.outputs["user-data"].scope == "target"
    assert config.outputs["user-data"].default is True


def test_resolve_aggregate_output_path_uses_fixed_path() -> None:
    """An aggregate output's path has no per-target substitution."""
    output = OutputSpec(path="rendered/inventory.yaml", scope="aggregate")
    assert resolve_aggregate_output_path(output) == Path("rendered/inventory.yaml")


def test_resolve_aggregate_output_path_override() -> None:
    output = OutputSpec(path="rendered/inventory.yaml", scope="aggregate")
    assert resolve_aggregate_output_path(output, override=Path("/tmp/out")) == Path("/tmp/out")


def test_resolve_aggregate_output_path_rejects_target_scope() -> None:
    """Calling the aggregate path resolver on a `scope: target` output is a
    programming error, not a user-facing one, but should still fail closed."""
    output = OutputSpec(path="rendered/{target}", scope="target")
    with pytest.raises(ConfigError):
        resolve_aggregate_output_path(output)


def test_real_project_config_loads(config_path: Path, example_root: Path) -> None:
    """The repo's example project config loads and exposes both outputs, with
    their paths anchored under example/ regardless of CWD."""
    config = load_config(config_path)
    assert config.outputs["user-data"].path == str(example_root / "rendered/{target}/user-data")
    assert config.outputs["meta-data"].path == str(example_root / "rendered/{target}/meta-data")
    assert config.default_output == "user-data"
    assert "subiquity" in config.validators
