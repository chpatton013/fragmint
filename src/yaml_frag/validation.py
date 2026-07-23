"""Generic rendered-document validation and external validators.

See PLAN.md "Validation". The renderer performs only document-AGNOSTIC checks;
any document-specific structural requirement is expressed by the project as
``assert`` operations in fragments (handled in :mod:`merge`) or as an external
validator (below). There is deliberately no built-in knowledge of autoinstall
or any other document type here.

Keep the validation concerns clearly separable so the CLI can report which one
failed:
1. input validation (inventory/fragment/config schemas — in their own modules);
2. generic rendered-document validation (:func:`check_unresolved_markers`,
   :func:`validate_against_schema`);
3. YAML parsing (handled in :mod:`yamlio`);
4. external named validators (:func:`run_validator`).
"""

from __future__ import annotations

from pathlib import Path

from .models import YamlValue

#: Names of the JSON schema files under ``schemas/`` used for INPUT validation.
INVENTORY_SCHEMA = "inventory.schema.json"
FRAGMENT_SCHEMA = "fragment.schema.json"
PROJECT_SCHEMA = "project.schema.json"


def check_unresolved_markers(document: YamlValue) -> None:
    """Reject a document containing unresolved template markers.

    Recurse through ``document`` and raise
    :class:`~yaml_frag.errors.ValidationError` if any string value contains
    ``{{`` or ``{%`` (a sign that templating did not fully resolve). This is the
    one universal, document-agnostic structural check. See PLAN.md "Generic
    rendered-document validation".
    """
    raise NotImplementedError


def validate_against_schema(document: YamlValue, schema_path: Path) -> None:
    """Validate the rendered document against a project-supplied JSON schema.

    Only invoked when ``config.output.schema`` is set. Raise
    :class:`~yaml_frag.errors.ValidationError` (with the failing path and
    message) on any schema violation. The renderer ships no default document
    schema; this is entirely project-driven.
    """
    raise NotImplementedError


def run_validator(output_path: Path, command: tuple[str, ...]) -> None:
    """Run a named external validator against the written output file.

    ``command`` comes from a project-config validator entry (PLAN.md "Named
    validators"), e.g. ``("python3", "tools/validate-autoinstall-user-data.py")``.
    The rendered output path is passed as the final argument. Raise
    :class:`~yaml_frag.errors.ValidationError` on nonzero exit, including
    captured stdout/stderr. Selected via ``--validator NAME``.
    """
    raise NotImplementedError
