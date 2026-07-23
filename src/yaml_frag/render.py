"""Top-level rendering orchestration.

Ties together project config, inventory resolution, fragment loading,
templating, merge operations, provenance, validation, serialization, and output
templating. See PLAN.md "Rendering algorithm".

The functions here are pure with respect to the filesystem where practical:
:func:`render_target` returns a :class:`~models.RenderResult` (in-memory),
:func:`compose_output` turns it into the final output text, and
:func:`write_output` handles the atomic on-disk write separately so
``validate``/``inspect``/``--stdout`` can render without writing.
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import replace
from pathlib import Path

from . import fragments as fragments_mod
from . import merge
from . import sources
from . import templating
from . import validation
from . import yamlio
from .errors import TemplateRenderError
from .inventory import load_inventory, load_secret_store, resolve_target
from .models import OutputSpec, ProjectConfig, ProvenanceEntry, RenderResult, SecretStore, Variables, YamlValue
from .provenance import ProvenanceTracker
from .sources import CommandRunner

#: Literal token an output template may contain; replaced by the serialized
#: YAML during :func:`compose_output`. See PLAN.md "Project configuration".
DOCUMENT_TOKEN = "{{ document }}"

#: Substring patterns that mark a variable name for redaction in ``inspect``
#: output. See PLAN.md "Show resolved inputs".
REDACT_SUBSTRINGS = ("password", "secret", "token", "private", "credential")


def render_target(
    target_name: str,
    *,
    config: ProjectConfig,
    inventory_path: Path,
    fragments_dir: Path,
    cli_variables: Variables | None = None,
    secrets_path: Path | None = None,
    runner: CommandRunner | None = None,
    validate: bool = True,
    redact_sources: bool = False,
) -> RenderResult:
    """Render a single target in memory.

    Steps (PLAN.md "Rendering algorithm"):
    1. load + validate inventory;
    2. resolve defaults/groups/target (fragment order + LAYERED-but-unresolved
       variable map) via :func:`inventory.resolve_target`;
    3. load the secret store from ``secrets_path`` (empty if not given), then
       resolve every variable source to a concrete value via
       :func:`yaml_frag.sources.resolve_variables` (executing any ``capture``
       subprocesses through ``runner``, default :class:`~sources.DefaultCommandRunner`);
    4. load + validate every referenced fragment;
    5. start from an empty document ``{}``;
    6. for each fragment in order: check required variables, render templates
       with the RESOLVED variables, apply operations in listed order while
       recording provenance, evaluating ``assert`` operations as encountered;
    7. run generic validation (unresolved-marker check + optional
       ``config.output.schema``) unless ``validate`` is False;
    8. return the :class:`~models.RenderResult`.

    ``runner`` is injectable so tests can resolve captures deterministically
    without running real programs. When ``redact_sources`` is true, secret and
    capture sources resolve to non-executing descriptions rather than being
    looked up or run — used by ``explain`` so it never executes captures or
    reveals secrets. Serialization and output templating happen in
    :func:`compose_output`, not here. No document-type knowledge lives here.
    """
    inventory = load_inventory(inventory_path)
    resolved = resolve_target(inventory, target_name, cli_variables=cli_variables)

    store = load_secret_store(secrets_path) if secrets_path is not None else SecretStore()
    variables = sources.resolve_variables(
        resolved.variables,
        secrets=store,
        runner=runner,
        target=target_name,
        redact=redact_sources,
    )

    doc: dict[str, YamlValue] = {}
    tracker = ProvenanceTracker()

    for ref in resolved.fragments:
        fragment = fragments_mod.load_fragment(fragments_dir, ref)

        for required_var in fragment.required_variables:
            if required_var not in variables:
                raise TemplateRenderError(
                    f"{target_name}: fragment {fragment.name}: missing required "
                    f"variable {required_var!r}"
                )

        for index, op in enumerate(fragment.operations):
            rendered_value = templating.render_value(
                op.value,
                variables,
                target=target_name,
                fragment=fragment.name,
                operation_index=index,
            )
            rendered_assertion = {
                key: templating.render_value(
                    value,
                    variables,
                    target=target_name,
                    fragment=fragment.name,
                    operation_index=index,
                )
                for key, value in op.assertion.items()
            }
            rendered_op = replace(op, value=rendered_value, assertion=rendered_assertion)
            entry = ProvenanceEntry(
                fragment=fragment.name, operation_index=index, operation=op.op
            )
            merge.apply_operation(doc, rendered_op, entry=entry, tracker=tracker)

    if validate:
        validation.check_unresolved_markers(doc)
        if config.output.schema:
            validation.validate_against_schema(doc, Path(config.output.schema))

    return RenderResult(
        target=target_name,
        document=doc,
        provenance=tracker.entries,
        overrides=tuple(tracker.overrides),
    )


def compose_output(result: RenderResult, output: OutputSpec) -> str:
    """Produce the final output text for a rendered target.

    Serialize ``result.document`` deterministically via
    :func:`yaml_frag.yamlio.dump_str`. If ``output.template`` is set, read that
    file and replace the literal :data:`DOCUMENT_TOKEN` with the serialized
    YAML; otherwise use the serialized YAML verbatim. This is how a project adds
    a header such as ``#cloud-config``. Ends with a single trailing newline.
    """
    text = yamlio.dump_str(result.document)

    if output.template:
        template_text = Path(output.template).read_text(encoding="utf-8")
        composed = template_text.replace(DOCUMENT_TOKEN, text)
    else:
        composed = text

    return composed.rstrip("\n") + "\n"


def write_output(text: str, path: Path) -> Path:
    """Atomically write ``text`` to ``path``.

    Create parent directories, write to a temporary file in the destination
    directory, then ``os.replace`` into place so a failure never leaves a
    partial final file (PLAN.md "Render all targets"). Return ``path``. Only the
    configured output file is written — no companion files are forced.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
        raise
    return path


def redact_variables(variables: Variables, *, show_secrets: bool = False) -> Variables:
    """Return a copy of ``variables`` with secret-looking values redacted.

    Operates on the UNRESOLVED variables so captures are never executed. A
    variable defined via a ``from: secret``/``from: capture`` source is always
    shown as a non-executing description (see :func:`sources.describe_source`),
    regardless of ``show_secrets``. Otherwise, a variable is redacted when its
    name contains any of :data:`REDACT_SUBSTRINGS` (case-insensitive) and
    ``show_secrets`` is False. Used by the ``inspect`` command (PLAN.md "Show
    resolved inputs").
    """
    redacted: Variables = {}
    for name, value in variables.items():
        if sources.is_source(value):
            redacted[name] = sources.describe_source(value)
        elif not show_secrets and any(
            substring in name.lower() for substring in REDACT_SUBSTRINGS
        ):
            redacted[name] = "<redacted>"
        else:
            redacted[name] = value
    return redacted
