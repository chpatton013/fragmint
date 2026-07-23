"""Variable value sources: literal, secret, and subprocess capture.

A variable value may be a plain literal (the default) or a tagged *source* that
is resolved AFTER the normal defaults/group/target/CLI layering but BEFORE
templating. See PLAN.md "Variable value sources".

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
instead of running real programs (PLAN.md "Testing requirements").
"""

from __future__ import annotations

import subprocess
from typing import Protocol, Sequence

from .errors import CaptureError
from .models import (
    CaptureSource,
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
            completed = subprocess.run(  # noqa: S603 - argv list, shell=False by design
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
    raise NotImplementedError


def parse_source(raw: YamlValue) -> VariableSource:
    """Parse a raw variable value into a :class:`~models.VariableSource`.

    Untagged values become a ``LiteralSource``. A tagged mapping is validated
    for its kind (correct fields present, ``command`` non-empty for capture,
    ``name`` present for secret) and its nested ``command``/``stdin`` sources
    are parsed recursively. Raise :class:`~errors.VariableResolutionError` (or
    :class:`~errors.ConfigError` for a structurally invalid source) with the
    offending shape described.
    """
    raise NotImplementedError


def resolve_source(
    source: VariableSource,
    *,
    secrets: SecretStore,
    runner: CommandRunner,
    target: str,
    variable: str,
    timeout: float = DEFAULT_CAPTURE_TIMEOUT,
) -> YamlValue:
    """Resolve a single source to a concrete value.

    - ``LiteralSource`` -> its value.
    - ``SecretSource`` -> the named secret; raise
      :class:`~errors.SecretNotFoundError` (naming ``target``/``variable``, not
      the value) if absent.
    - ``CaptureSource`` -> recursively resolve each ``command`` element (to
      strings) and ``stdin``, run via ``runner``, then strip one trailing
      newline when ``trim``. Raise :class:`~errors.CaptureError` on failure.

    Never log resolved secret values or secret-sourced arguments.
    """
    raise NotImplementedError


def resolve_variables(
    raw: Variables,
    *,
    secrets: SecretStore,
    runner: CommandRunner | None = None,
    target: str,
    timeout: float = DEFAULT_CAPTURE_TIMEOUT,
) -> Variables:
    """Resolve every variable in a layered map to concrete values.

    Parses each value with :func:`parse_source` and resolves it with
    :func:`resolve_source`. ``runner`` defaults to :class:`DefaultCommandRunner`.
    Returns a new mapping of concrete values suitable for templating. Called by
    :mod:`render` once, after inventory resolution.
    """
    raise NotImplementedError


def describe_source(raw: YamlValue) -> str:
    """Return a redacted, non-executing description of a variable's source.

    Used by ``inspect``/``explain`` so they can show what a variable is WITHOUT
    running captures or revealing secrets, e.g. ``"<secret gb10_password>"`` or
    ``"<capture: openssl passwd -6 -stdin>"``. Literals are returned via their
    normal (name-based) redaction path instead. See PLAN.md "Show resolved
    inputs".
    """
    raise NotImplementedError
