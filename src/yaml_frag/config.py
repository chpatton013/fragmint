"""Project-configuration loading.

The project-configuration file (``yaml-frag.yaml`` by default) is how a project
adapts the generic renderer to a specific document type without touching the
renderer code. It declares default input locations, the output spec (path
pattern + optional text template + optional document schema + default
validators), and named external validators.

See PLAN.md "Project configuration". Validate against
``schemas/project.schema.json`` and raise
:class:`~yaml_frag.errors.ConfigError` (with file/field context) on any problem.
"""

from __future__ import annotations

from pathlib import Path

from .models import ProjectConfig

#: Default project-config filename, relative to the working directory.
DEFAULT_CONFIG_PATH = Path("yaml-frag.yaml")

#: The only project-config version supported by this release.
SUPPORTED_CONFIG_VERSION = 1


def load_config(path: Path | None = None) -> ProjectConfig:
    """Load and validate the project configuration.

    When ``path`` is ``None``, use :data:`DEFAULT_CONFIG_PATH`. Apply documented
    defaults for any omitted optional fields (see :class:`~models.OutputSpec`).
    Raise :class:`~yaml_frag.errors.ConfigError` if the file is missing,
    unparseable, the wrong version, or fails schema validation.
    """
    raise NotImplementedError


def resolve_output_path(config: ProjectConfig, target: str, override: Path | None = None) -> Path:
    """Compute the destination path for ``target``.

    When ``override`` is given, use it verbatim. Otherwise substitute
    ``{target}`` into ``config.output.path``. See PLAN.md "Project
    configuration" and "Render one target".
    """
    raise NotImplementedError


def select_validators(
    config: ProjectConfig,
    requested: tuple[str, ...],
) -> tuple[str, ...]:
    """Resolve which validators to run.

    If ``requested`` is non-empty, use it (validating each name exists in
    ``config.validators``; unknown names raise
    :class:`~yaml_frag.errors.ConfigError`). Otherwise fall back to
    ``config.output.validators``. See PLAN.md "Named validators".
    """
    raise NotImplementedError
