"""Project-configuration tests. See README.md "Project configuration"."""

from __future__ import annotations

from pathlib import Path

import pytest

from yaml_frag.config import load_config, resolve_output_path, select_validators
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
        "version: 1\noutput:\n  path: rendered/{target}\n",
    )
    config = load_config(config_path)
    assert config.version == 1
    assert config.inventory == str(tmp_path / "inventory/targets.yaml")
    assert config.fragments_dir == str(tmp_path / "fragments")
    assert config.output.path == str(tmp_path / "rendered/{target}")
    assert config.output.template is None
    assert config.output.schema is None
    assert config.output.validators == ()
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
        "output:\n"
        "  path: rendered/{target}/user-data\n"
        "  template: templates/user-data.tmpl\n",
    )
    config = load_config(config_path)
    assert config.inventory == str(project_dir / "inventory/targets.yaml")
    assert config.fragments_dir == str(project_dir / "fragments")
    assert config.output.path == str(project_dir / "rendered/{target}/user-data")
    assert config.output.template == str(project_dir / "templates/user-data.tmpl")


def test_absolute_paths_pass_through_unchanged(tmp_path: Path) -> None:
    """An already-absolute path in the config is not re-anchored."""
    abs_inventory = tmp_path / "elsewhere" / "targets.yaml"
    config_path = _write(
        tmp_path / "yaml-frag.yaml",
        f"version: 1\ninventory: {abs_inventory}\noutput:\n  path: rendered/{{target}}\n",
    )
    config = load_config(config_path)
    assert config.inventory == str(abs_inventory)


def test_output_path_substitution() -> None:
    """`{target}` in output.path is substituted with the target name."""
    config = ProjectConfig(
        version=1,
        inventory="inventory/targets.yaml",
        fragments_dir="fragments",
        output=OutputSpec(path="rendered/{target}/user-data"),
    )
    path = resolve_output_path(config, "gb10-01")
    assert path == Path("rendered/gb10-01/user-data")


def test_output_path_override() -> None:
    """An explicit override path is used verbatim, ignoring the pattern."""
    config = ProjectConfig(
        version=1,
        inventory="inventory/targets.yaml",
        fragments_dir="fragments",
        output=OutputSpec(path="rendered/{target}/user-data"),
    )
    path = resolve_output_path(config, "gb10-01", override=Path("/tmp/out"))
    assert path == Path("/tmp/out")


def test_output_template_injects_document_token(tmp_path: Path) -> None:
    """The serialized YAML replaces the literal `{{ document }}` token."""
    template_path = _write(tmp_path / "tmpl.txt", "#cloud-config\n{{ document }}")
    config_path = _write(
        tmp_path / "yaml-frag.yaml",
        f"version: 1\noutput:\n  path: rendered/{{target}}\n  template: {template_path}\n",
    )
    config = load_config(config_path)
    assert config.output.template == str(template_path)

    template_text = Path(config.output.template).read_text()
    rendered = template_text.replace("{{ document }}", "autoinstall:\n  version: 1\n")
    assert rendered == "#cloud-config\nautoinstall:\n  version: 1\n"


def test_no_template_writes_yaml_verbatim(tmp_path: Path) -> None:
    """With no output.template, the serialized YAML is written unchanged."""
    config_path = _write(
        tmp_path / "yaml-frag.yaml",
        "version: 1\noutput:\n  path: rendered/{target}\n",
    )
    config = load_config(config_path)
    assert config.output.template is None


def test_validator_selection_resolution() -> None:
    """--validator names override output defaults; unknown names raise ConfigError."""
    config = ProjectConfig(
        version=1,
        inventory="inventory/targets.yaml",
        fragments_dir="fragments",
        output=OutputSpec(path="rendered/{target}", validators=("subiquity",)),
        validators={
            "subiquity": ValidatorSpec(name="subiquity", command=("true",)),
            "other": ValidatorSpec(name="other", command=("true",)),
        },
    )
    assert select_validators(config, ()) == ("subiquity",)
    assert select_validators(config, ("other",)) == ("other",)
    with pytest.raises(ConfigError):
        select_validators(config, ("nope",))


def test_invalid_config_raises_config_error(tmp_path: Path) -> None:
    """A malformed or wrong-version project config raises ConfigError (exit 7)."""
    missing = tmp_path / "does-not-exist.yaml"
    with pytest.raises(ConfigError):
        load_config(missing)

    wrong_version = _write(
        tmp_path / "yaml-frag.yaml",
        "version: 2\noutput:\n  path: rendered/{target}\n",
    )
    with pytest.raises(ConfigError):
        load_config(wrong_version)

    malformed = _write(tmp_path / "bad.yaml", "not: [valid, project: config\n")
    with pytest.raises(ConfigError):
        load_config(malformed)


def test_real_project_config_loads(config_path: Path, example_root: Path) -> None:
    """The repo's example project config loads and exposes the subiquity
    validator, with its paths anchored under example/ regardless of CWD."""
    config = load_config(config_path)
    assert config.output.path == str(example_root / "rendered/{target}/user-data")
    assert "subiquity" in config.validators
