"""Variable value sources: literal, secret, and subprocess capture.

A variable value may be a plain literal (the default) or a tagged *source* that
is resolved AFTER the normal defaults/group/target/CLI layering but BEFORE
templating. See README.md "Variable value sources".

Syntax (``from:`` discriminator). A mapping is treated as a source only when it
has a ``from`` key whose value is one of ``literal``, ``secret``, ``capture``;
any other value (scalar, list, or mapping without that discriminator) is a
literal.

    keyboard_layout: us                      # literal (unchanged)

    identity_password:                       # secret reference
      from: secret
      name: gb10_password

    identity_password_hash:                  # subprocess capture
      from: capture
      command: [openssl, passwd, -6, -stdin]
      stdin: { from: secret, name: gb10_password }
      trim: true                             # strip trailing newline (default)

    weird_literal:                           # escape hatch for a literal mapping
      from: literal                          #   that itself contains a `from` key
      value: { from: "us-east-1" }

Sources nest: a capture's ``stdin`` and each element of its ``command`` are
themselves sources (literal or secret). Variables do NOT reference other
variables in this version.

Security (enforced here so it can be reviewed in one place):
- subprocesses run with an argv list and ``shell=False`` — no shell parsing;
- resolved secret values and secret-sourced arguments/stdin are NEVER logged;
- a bounded timeout applies; failures raise :class:`~errors.CaptureError` with
  the command name and stderr, secret-sourced args redacted.

Testability: capture execution goes through the injectable
:class:`CommandRunner` protocol so unit tests can supply a deterministic stub
instead of running real programs (see ``tests/test_sources.py``).
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from typing import Protocol

from .errors import CaptureError, ConfigError, SecretNotFoundError
from .models import (
    CaptureSource,
    LiteralSource,
    SecretSource,
    SecretStore,
    Variables,
    VariableSource,
    YamlValue,
)

#: Default wall-clock timeout (seconds) for a capture subprocess.
DEFAULT_CAPTURE_TIMEOUT = 30.0

#: The discriminator key and its recognized values.
DISCRIMINATOR = "from"
SOURCE_KINDS = ("literal", "secret", "capture")


class CommandRunner(Protocol):
    """Runs a capture subprocess and returns its stdout as text.

    Implementations must not use a shell. ``stdin`` (already-resolved text) is
    fed to the process's standard input when not ``None``.
    """

    def run(self, command: Sequence[str], *, stdin: str | None, timeout: float) -> str:
        ...


class DefaultCommandRunner:
    """Production :class:`CommandRunner` backed by :mod:`subprocess`.

    Implemented (rather than stubbed) because it is the security boundary for
    this feature: argv list, ``shell=False``, bounded timeout, captured output.
    """

    def run(self, command: Sequence[str], *, stdin: str | None, timeout: float) -> str:
        argv = list(command)
        try:
            # An argv list with shell=False, by design: there is no shell for a
            # captured value to be injected into (README.md "Security").
            completed = subprocess.run(
                argv,
                input=stdin,
                capture_output=True,
                text=True,
                timeout=timeout,
                shell=False,
                check=False,
            )
        except FileNotFoundError as exc:
            raise CaptureError(f"capture command not found: {argv[0]!r}") from exc
        except subprocess.TimeoutExpired as exc:
            raise CaptureError(f"capture command {argv[0]!r} timed out after {timeout}s") from exc
        if completed.returncode != 0:
            raise CaptureError(
                f"capture command {argv[0]!r} failed with exit {completed.returncode}: "
                f"{completed.stderr.strip()}"
            )
        return completed.stdout


def is_source(raw: YamlValue) -> bool:
    """Return whether ``raw`` should be interpreted as a tagged source.

    True only when ``raw`` is a mapping whose :data:`DISCRIMINATOR` value is in
    :data:`SOURCE_KINDS`. Everything else is a literal.
    """
    if not isinstance(raw, dict):
        return False
    kind = raw.get(DISCRIMINATOR)
    return isinstance(kind, str) and kind in SOURCE_KINDS


def parse_source(raw: YamlValue) -> VariableSource:
    """Parse a raw variable value into a :class:`~models.VariableSource`.

    Untagged values become a ``LiteralSource``. A tagged mapping is validated
    for its kind (correct fields present, ``command`` non-empty for capture,
    ``name`` present for secret) and its nested ``command``/``stdin`` sources
    are parsed recursively. Raise :class:`~errors.VariableResolutionError` (or
    :class:`~errors.ConfigError` for a structurally invalid source) with the
    offending shape described.
    """
    if not is_source(raw):
        return LiteralSource(value=raw)
    assert isinstance(raw, dict)
    kind = raw[DISCRIMINATOR]

    if kind == "literal":
        if "value" not in raw:
            raise ConfigError("invalid `from: literal` source: missing `value` field")
        return LiteralSource(value=raw["value"])

    if kind == "secret":
        name = raw.get("name")
        if not isinstance(name, str) or not name:
            raise ConfigError("invalid `from: secret` source: missing or empty `name` field")
        return SecretSource(name=name)

    if kind == "capture":
        command_raw = raw.get("command")
        if not isinstance(command_raw, list) or not command_raw:
            raise ConfigError(
                "invalid `from: capture` source: `command` must be a non-empty list"
            )
        command = tuple(parse_source(item) for item in command_raw)
        stdin_raw = raw.get("stdin")
        stdin = parse_source(stdin_raw) if stdin_raw is not None else None
        trim_raw = raw.get("trim", True)
        if not isinstance(trim_raw, bool):
            raise ConfigError("invalid `from: capture` source: `trim` must be a boolean")
        return CaptureSource(command=command, stdin=stdin, trim=trim_raw)

    raise ConfigError(f"invalid variable source: unrecognized `from` value {kind!r}")


def _is_sensitive(source: VariableSource) -> bool:
    return isinstance(source, (SecretSource, CaptureSource))


def _redact_source_for_error(source: VariableSource) -> str:
    """Render a source for inclusion in a description/error, redacting anything
    secret-derived. Literal values are rendered as plain text (not Python
    repr) so a command's literal arguments read naturally, e.g.
    ``<capture: openssl passwd -6 -stdin>``."""
    if isinstance(source, LiteralSource):
        return str(source.value)
    if isinstance(source, SecretSource):
        return f"<secret {source.name}>"
    if isinstance(source, CaptureSource):
        parts = [_redact_source_for_error(part) for part in source.command]
        return f"<capture: {' '.join(parts)}>"
    return "<unknown>"


def resolve_source(
    source: VariableSource,
    *,
    secrets: SecretStore,
    runner: CommandRunner,
    target: str,
    variable: str,
    timeout: float = DEFAULT_CAPTURE_TIMEOUT,
    redact: bool = False,
) -> YamlValue:
    """Resolve a single source to a concrete value.

    - ``LiteralSource`` -> its value.
    - ``SecretSource`` -> the named secret; raise
      :class:`~errors.SecretNotFoundError` (naming ``target``/``variable``, not
      the value) if absent.
    - ``CaptureSource`` -> recursively resolve each ``command`` element (to
      strings) and ``stdin``, run via ``runner``, then strip one trailing
      newline when ``trim``. Raise :class:`~errors.CaptureError` on failure.

    When ``redact`` is true, a ``SecretSource``/``CaptureSource`` resolves to
    its non-executing description (``<secret NAME>`` / ``<capture: ...>``)
    WITHOUT looking up the secret store or running any subprocess. This is what
    ``explain`` uses so it can build the document for provenance without
    executing captures or revealing secrets (README.md "Resolution timing").

    Never log resolved secret values or secret-sourced arguments.
    """
    if redact and isinstance(source, (SecretSource, CaptureSource)):
        return _redact_source_for_error(source)

    if isinstance(source, LiteralSource):
        return source.value

    if isinstance(source, SecretSource):
        if source.name not in secrets.secrets:
            raise SecretNotFoundError(
                f"target {target!r}: variable {variable!r}: "
                f"secret {source.name!r} not found in secret store"
            )
        return secrets.secrets[source.name]

    if isinstance(source, CaptureSource):
        argv: list[str] = []
        for element in source.command:
            resolved = resolve_source(
                element,
                secrets=secrets,
                runner=runner,
                target=target,
                variable=variable,
                timeout=timeout,
            )
            argv.append(str(resolved))

        stdin_text: str | None = None
        if source.stdin is not None:
            resolved_stdin = resolve_source(
                source.stdin,
                secrets=secrets,
                runner=runner,
                target=target,
                variable=variable,
                timeout=timeout,
            )
            stdin_text = str(resolved_stdin)

        command_name = argv[0] if argv else "<empty command>"
        try:
            stdout = runner.run(argv, stdin=stdin_text, timeout=timeout)
        except CaptureError:
            raise
        except Exception as exc:
            raise CaptureError(
                f"target {target!r}: variable {variable!r}: "
                f"capture command {command_name!r} failed: {exc}"
            ) from exc

        if source.trim and stdout.endswith("\n"):
            stdout = stdout[:-1]
        return stdout

    raise ConfigError(f"unrecognized variable source: {source!r}")


def resolve_variables(
    raw: Variables,
    *,
    secrets: SecretStore,
    runner: CommandRunner | None = None,
    target: str,
    timeout: float = DEFAULT_CAPTURE_TIMEOUT,
    redact: bool = False,
) -> Variables:
    """Resolve every variable in a layered map to concrete values.

    Parses each value with :func:`parse_source` and resolves it with
    :func:`resolve_source`. ``runner`` defaults to :class:`DefaultCommandRunner`.
    Returns a new mapping of concrete values suitable for templating. Called by
    :mod:`render` once, after inventory resolution.

    When ``redact`` is true, secret/capture sources resolve to non-executing
    descriptions (no store lookup, no subprocess) — used by ``explain``.
    """
    active_runner = runner if runner is not None else DefaultCommandRunner()
    resolved: Variables = {}
    for name, value in raw.items():
        source = parse_source(value)
        resolved[name] = resolve_source(
            source,
            secrets=secrets,
            runner=active_runner,
            target=target,
            variable=name,
            timeout=timeout,
            redact=redact,
        )
    return resolved


def describe_source(raw: YamlValue) -> str:
    """Return a redacted, non-executing description of a variable's source.

    Used by ``inspect``/``explain`` so they can show what a variable is WITHOUT
    running captures or revealing secrets, e.g. ``"<secret gb10_password>"`` or
    ``"<capture: openssl passwd -6 -stdin>"``. Literals are returned via their
    normal (name-based) redaction path instead. See README.md "CLI usage".
    """
    source = parse_source(raw)
    if isinstance(source, SecretSource):
        return f"<secret {source.name}>"
    if isinstance(source, CaptureSource):
        parts = [_redact_source_for_error(part) for part in source.command]
        return f"<capture: {' '.join(parts)}>"
    if isinstance(source, LiteralSource):
        return repr(source.value)
    return "<unknown>"
