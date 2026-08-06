"""Generic rendered-document validation and external validators.

See README.md "Validation". The renderer performs only document-AGNOSTIC checks;
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

import subprocess
from pathlib import Path
from typing import Any

import jsonschema

from .errors import ValidationError
from .models import DataValue
from .yamlio import load_file

#: Bounded timeout (seconds) for an external validator subprocess.
VALIDATOR_TIMEOUT = 60.0


def _schema_path(name: str) -> Path:
    """Resolve a packaged tool-format schema under ``fragmint/schemas/``."""
    return Path(__file__).resolve().parent / "schemas" / name


def check_unresolved_markers(document: DataValue) -> None:
    """Reject a document containing unresolved template markers.

    Recurse through ``document`` and raise
    :class:`~fragmint.errors.ValidationError` if any string value contains
    ``{{`` or ``{%`` (a sign that templating did not fully resolve). This is the
    one universal, document-agnostic structural check. See README.md
    "Validation".
    """

    def _walk(node: DataValue, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                _walk(value, f"{path}/{key}")
        elif isinstance(node, list):
            for index, item in enumerate(node):
                _walk(item, f"{path}/{index}")
        elif isinstance(node, str) and ("{{" in node or "{%" in node):
            raise ValidationError(
                f"unresolved template marker at {path or '/'}: {node!r}"
            )

    _walk(document, "")


def validate_against_schema(document: DataValue, schema_path: Path) -> None:
    """Validate the rendered document against a project-supplied JSON schema.

    Only invoked when ``config.output.schema`` is set. Raise
    :class:`~fragmint.errors.ValidationError` (with the failing path and
    message) on any schema violation. The renderer ships no default document
    schema; this is entirely project-driven.
    """
    if not schema_path.is_file():
        raise ValidationError(f"document schema not found: {schema_path}")

    schema: dict[str, Any] = load_file(schema_path)  # type: ignore[assignment]
    try:
        jsonschema.validate(instance=document, schema=schema)
    except jsonschema.ValidationError as exc:
        raise ValidationError(
            f"rendered document failed schema validation against {schema_path}: "
            f"{exc.message} (at {'/'.join(str(part) for part in exc.path)})"
        ) from exc


def run_validator(output_path: Path, command: tuple[str, ...]) -> None:
    """Run a named external validator against the written output file.

    ``command`` comes from a ``validators`` entry in any document in the
    closure (README.md "Validation": named validators), e.g.
    ``("python3", "tools/validate-autoinstall-user-data.py")``.
    The rendered output path is passed as the final argument. Raise
    :class:`~fragmint.errors.ValidationError` on nonzero exit, including
    captured stdout/stderr. Selected via ``--validator NAME``.
    """
    argv = [*command, str(output_path)]
    try:
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=VALIDATOR_TIMEOUT,
            shell=False,
            check=False,
        )
    except FileNotFoundError as exc:
        raise ValidationError(f"validator command not found: {argv[0]!r}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ValidationError(
            f"validator command {argv[0]!r} timed out after {VALIDATOR_TIMEOUT}s"
        ) from exc

    if completed.returncode != 0:
        raise ValidationError(
            f"validator {argv[0]!r} failed with exit {completed.returncode}\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
