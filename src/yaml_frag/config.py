"""Project-configuration loading.

The project-configuration file (``yaml-frag.yaml`` by default) is how a project
adapts the generic renderer to a specific document type without touching the
renderer code. It declares default input locations, the output spec (path
pattern + optional text template + optional document schema + default
validators), and named external validators.

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


def load_config(path: Path | None = None) -> ProjectConfig:
    """Load and validate the project configuration.

    When ``path`` is ``None``, use :data:`DEFAULT_CONFIG_PATH`. Apply documented
    defaults for any omitted optional fields (see :class:`~models.OutputSpec`).
    Raise :class:`~yaml_frag.errors.ConfigError` if the file is missing,
    unparseable, the wrong version, or fails schema validation.
    """
    config_path = path if path is not None else DEFAULT_CONFIG_PATH
    if not config_path.is_file():
        raise ConfigError(f"project configuration not found: {config_path}")

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

    output_raw = doc.get("output")
    if not isinstance(output_raw, dict):
        raise ConfigError(f"project configuration {config_path}: `output` is required")
    output_path = output_raw.get("path")
    if not isinstance(output_path, str) or not output_path:
        raise ConfigError(f"project configuration {config_path}: `output.path` is required")
    output = OutputSpec(
        path=output_path,
        template=output_raw.get("template"),
        schema=output_raw.get("schema"),
        validators=tuple(output_raw.get("validators", [])),
    )

    validators: dict[str, ValidatorSpec] = {}
    for name, spec in doc.get("validators", {}).items():
        if not isinstance(spec, dict) or "command" not in spec:
            raise ConfigError(
                f"project configuration {config_path}: validator {name!r} missing `command`"
            )
        validators[name] = ValidatorSpec(name=name, command=tuple(spec["command"]))

    for name in output.validators:
        if name not in validators:
            raise ConfigError(
                f"project configuration {config_path}: output default validator "
                f"{name!r} is not declared in `validators`"
            )

    return ProjectConfig(
        version=version,
        inventory=doc.get("inventory", DEFAULT_INVENTORY),
        fragments_dir=doc.get("fragments_dir", DEFAULT_FRAGMENTS_DIR),
        output=output,
        validators=validators,
    )


def resolve_output_path(config: ProjectConfig, target: str, override: Path | None = None) -> Path:
    """Compute the destination path for ``target``.

    When ``override`` is given, use it verbatim. Otherwise substitute
    ``{target}`` into ``config.output.path``. See README.md "Project
    configuration" and "CLI usage".
    """
    if override is not None:
        return override
    return Path(config.output.path.format(target=target))


def select_validators(
    config: ProjectConfig,
    requested: tuple[str, ...],
) -> tuple[str, ...]:
    """Resolve which validators to run.

    If ``requested`` is non-empty, use it (validating each name exists in
    ``config.validators``; unknown names raise
    :class:`~yaml_frag.errors.ConfigError`). Otherwise fall back to
    ``config.output.validators``. See README.md "Validation" (named
    validators).
    """
    if requested:
        for name in requested:
            if name not in config.validators:
                raise ConfigError(f"unknown validator {name!r} requested")
        return requested
    return config.output.validators


__all__ = [
    "DEFAULT_CONFIG_PATH",
    "SUPPORTED_CONFIG_VERSION",
    "load_config",
    "resolve_output_path",
    "select_validators",
]
