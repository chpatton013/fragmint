"""Rendered-document validation and optional external Subiquity validation.

See PLAN.md "Validation" (subsections "Rendered-document validation" and
"Optional Subiquity validation").

Keep the three validation stages clearly separable so the CLI can report which
one failed:
1. internal renderer validation (this module, :func:`validate_rendered`);
2. YAML parsing (handled in :mod:`yamlio`);
3. optional external Subiquity validation (:func:`run_subiquity_validation`).
"""

from __future__ import annotations

from pathlib import Path

from .models import YamlValue

#: Names of the JSON schema files under ``schemas/``.
INVENTORY_SCHEMA = "inventory.schema.json"
FRAGMENT_SCHEMA = "fragment.schema.json"


def validate_rendered(document: YamlValue) -> None:
    """Validate the final merged document.

    Enforce PLAN.md "Rendered-document validation":
    - ``autoinstall.version == 1`` is present;
    - no unresolved template markers (reject text containing ``{{`` or ``{%``)
      anywhere in string values;
    - ``identity.hostname`` / ``identity.username`` / ``identity.password`` are
      nonempty strings;
    - ``ssh.install-server`` and ``ssh.allow-pw`` are Booleans;
    - ``storage`` exists;
    - when ``network`` exists, ``network.version == 2``;
    - when present, ``user-data.packages`` / ``user-data.write_files`` /
      ``user-data.runcmd`` are lists.

    Raise :class:`~autoinstall_renderer.errors.ValidationError` with a specific
    message on the first violation. Do not attempt to reimplement the full
    Subiquity schema.
    """
    raise NotImplementedError


def run_subiquity_validation(user_data_path: Path, command: list[str]) -> None:
    """Run the optional external Subiquity validator.

    ``command`` comes from the inventory ``validation.command`` config
    (PLAN.md "Optional Subiquity validation"), e.g.
    ``["python3", "tools/validate-autoinstall-user-data.py"]``. The rendered
    ``user-data`` path is passed to the command. Raise
    :class:`~autoinstall_renderer.errors.ValidationError` on nonzero exit,
    including captured output. This is only invoked when the user passes
    ``--subiquity``.
    """
    raise NotImplementedError
