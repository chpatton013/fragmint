"""Project-configuration loading.

The project-configuration file (``yaml-frag.yaml`` by default) is how a project
adapts the generic renderer to a specific document type without touching the
renderer code. It declares default input locations, one or more named outputs
(each: path pattern + optional text template + optional document schema +
default validators + optional ``default`` marker), and named external
validators.

For portability, every path a project declares (``inventory``,
``fragments_dir``, and each output's ``path``/``template``/``schema``) is
resolved RELATIVE TO THE CONFIG FILE'S OWN DIRECTORY, not the process's current
working directory. This lets a project directory (e.g. ``example/``) be
invoked from anywhere via ``--config path/to/yaml-frag.yaml`` and still find
its own inventory, fragments, and templates. An already-absolute path in the
config is left unchanged. CLI overrides (``--inventory``, ``--fragments-dir``,
``--output``) are given directly on the command line and are resolved relative
to the CWD as usual, matching ordinary CLI conventions.

See README.md "Project configuration". Validate against
``schemas/project.schema.json`` and raise
:class:`~yaml_frag.errors.ConfigError` (with file/field context) on any problem.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import jsonschema

from .errors import ConfigError
from .models import OutputSpec, ProjectConfig, ValidatorSpec
from .yamlio import load_file

#: Default project-config filename, relative to the working directory.
DEFAULT_CONFIG_PATH = Path("yaml-frag.yaml")

#: The only project-config version supported by this release.
SUPPORTED_CONFIG_VERSION = 1

#: Documented defaults for optional top-level fields.
DEFAULT_INVENTORY = "inventory/targets.yaml"
DEFAULT_FRAGMENTS_DIR = "fragments"


def _schema_path(name: str) -> Path:
    """Resolve a project-shipped tool-format schema under ``<repo>/schemas/``."""
    return Path(__file__).resolve().parents[2] / "schemas" / name


def _resolve_relative(config_dir: Path, value: str) -> str:
    """Anchor a project-declared path to ``config_dir``.

    An already-absolute ``value`` is returned unchanged (``Path.__truediv__``
    discards the left operand when the right one is absolute). This is what
    makes a project directory portable: its declared paths are always relative
    to itself, never to the caller's working directory.
    """
    return str(config_dir / value)


def _parse_output(config_path: Path, config_dir: Path, name: str, output_raw: Any) -> OutputSpec:
    if not isinstance(output_raw, dict):
        raise ConfigError(
            f"project configuration {config_path}: `outputs.{name}` must be a mapping"
        )
    output_path = output_raw.get("path")
    if not isinstance(output_path, str) or not output_path:
        raise ConfigError(
            f"project configuration {config_path}: `outputs.{name}.path` is required"
        )
    output_template = output_raw.get("template")
    output_schema = output_raw.get("schema")
    return OutputSpec(
        path=_resolve_relative(config_dir, output_path),
        template=_resolve_relative(config_dir, output_template) if output_template else None,
        schema=_resolve_relative(config_dir, output_schema) if output_schema else None,
        validators=tuple(output_raw.get("validators", [])),
        default=bool(output_raw.get("default", False)),
    )


def load_config(path: Path | None = None) -> ProjectConfig:
    """Load and validate the project configuration.

    When ``path`` is ``None``, use :data:`DEFAULT_CONFIG_PATH`. Apply documented
    defaults for any omitted optional fields (see :class:`~models.OutputSpec`).
    Every path field in the returned :class:`~models.ProjectConfig` is resolved
    relative to ``path``'s own directory (see the module docstring).
    Raise :class:`~yaml_frag.errors.ConfigError` if the file is missing,
    unparseable, the wrong version, fails schema validation, declares no
    outputs, or marks more than one output ``default: true``.
    """
    config_path = path if path is not None else DEFAULT_CONFIG_PATH
    if not config_path.is_file():
        raise ConfigError(f"project configuration not found: {config_path}")
    config_dir = config_path.resolve().parent

    try:
        raw = load_file(config_path)
    except Exception as exc:  # noqa: BLE001 - re-raise with config context
        raise ConfigError(f"cannot parse project configuration {config_path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"project configuration {config_path} must be a mapping")

    schema: dict[str, Any] = cast(dict[str, Any], load_file(_schema_path("project.schema.json")))
    try:
        jsonschema.validate(instance=raw, schema=schema)
    except jsonschema.ValidationError as exc:
        raise ConfigError(
            f"project configuration {config_path} failed schema validation: {exc.message} "
            f"(at {'/'.join(str(part) for part in exc.path)})"
        ) from exc

    # Schema validation above guarantees the shape README.md documents; treat the
    # parsed document as loosely-typed data from here on rather than fighting
    # the recursive YamlValue union.
    doc = cast(dict[str, Any], raw)

    version = doc.get("version")
    if version != SUPPORTED_CONFIG_VERSION:
        raise ConfigError(
            f"project configuration {config_path}: unsupported version {version!r}, "
            f"expected {SUPPORTED_CONFIG_VERSION}"
        )

    outputs_raw = doc.get("outputs")
    if not isinstance(outputs_raw, dict) or not outputs_raw:
        raise ConfigError(
            f"project configuration {config_path}: `outputs` is required and must "
            f"declare at least one output"
        )

    outputs: dict[str, OutputSpec] = {
        name: _parse_output(config_path, config_dir, name, output_raw)
        for name, output_raw in outputs_raw.items()
    }

    default_candidates = [name for name, spec in outputs.items() if spec.default]
    if len(default_candidates) > 1:
        raise ConfigError(
            f"project configuration {config_path}: only one output may be marked "
            f"`default: true`, got {sorted(default_candidates)}"
        )
    if default_candidates:
        default_output: str | None = default_candidates[0]
    elif len(outputs) == 1:
        default_output = next(iter(outputs))
    else:
        default_output = None

    validators: dict[str, ValidatorSpec] = {}
    for name, spec in doc.get("validators", {}).items():
        if not isinstance(spec, dict) or "command" not in spec:
            raise ConfigError(
                f"project configuration {config_path}: validator {name!r} missing `command`"
            )
        validators[name] = ValidatorSpec(name=name, command=tuple(spec["command"]))

    for output_name, output_spec in outputs.items():
        for validator_name in output_spec.validators:
            if validator_name not in validators:
                raise ConfigError(
                    f"project configuration {config_path}: output {output_name!r} default "
                    f"validator {validator_name!r} is not declared in `validators`"
                )

    return ProjectConfig(
        version=version,
        inventory=_resolve_relative(config_dir, doc.get("inventory", DEFAULT_INVENTORY)),
        fragments_dir=_resolve_relative(config_dir, doc.get("fragments_dir", DEFAULT_FRAGMENTS_DIR)),
        outputs=outputs,
        default_output=default_output,
        validators=validators,
    )


def resolve_output_path(output: OutputSpec, target: str, override: Path | None = None) -> Path:
    """Compute the destination path for ``target`` under a single output.

    When ``override`` is given, use it verbatim. Otherwise substitute
    ``{target}`` into ``output.path``. See README.md "Project configuration"
    and "CLI usage".
    """
    if override is not None:
        return override
    return Path(output.path.format(target=target))


def select_validators(
    config: ProjectConfig,
    output: OutputSpec,
    requested: tuple[str, ...],
) -> tuple[str, ...]:
    """Resolve which validators to run for a single output.

    If ``requested`` is non-empty, use it (validating each name exists in
    ``config.validators``; unknown names raise
    :class:`~yaml_frag.errors.ConfigError`). Otherwise fall back to
    ``output.validators``. See README.md "Validation" (named validators).
    """
    if requested:
        for name in requested:
            if name not in config.validators:
                raise ConfigError(f"unknown validator {name!r} requested")
        return requested
    return output.validators


__all__ = [
    "DEFAULT_CONFIG_PATH",
    "SUPPORTED_CONFIG_VERSION",
    "load_config",
    "resolve_output_path",
    "select_validators",
]
