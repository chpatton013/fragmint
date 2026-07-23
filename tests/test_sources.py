"""Variable value source tests. Covers PLAN.md "Variable-source tests".

Capture tests MUST use a stubbed CommandRunner — never run real subprocesses.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.skip(reason="TODO: implement — see PLAN.md 'Variable-source tests'")


def test_untagged_value_is_literal() -> None:
    """A scalar/list/plain mapping (no reserved `from:`) parses as a literal."""
    raise NotImplementedError


def test_from_literal_escape_hatch() -> None:
    """`{from: literal, value: {from: ...}}` yields the inner mapping literally."""
    raise NotImplementedError


def test_mapping_with_nonreserved_from_is_literal() -> None:
    """`{from: us-east-1}` is a literal, not a source (from value not reserved)."""
    raise NotImplementedError


def test_secret_resolves_from_store() -> None:
    """`{from: secret, name: N}` resolves to the store value for N."""
    raise NotImplementedError


def test_missing_secret_raises() -> None:
    """An absent secret name raises SecretNotFoundError naming target/variable,
    not the value."""
    raise NotImplementedError


def test_capture_uses_stubbed_runner() -> None:
    """`from: capture` calls the injected runner with the resolved argv and
    stdin, and returns its stdout."""
    raise NotImplementedError


def test_capture_args_and_stdin_from_sources() -> None:
    """command elements and stdin may themselves be literal/secret sources."""
    raise NotImplementedError


def test_capture_trim_default_and_off() -> None:
    """`trim` strips one trailing newline by default; `trim: false` keeps it."""
    raise NotImplementedError


def test_capture_failure_raises_capture_error() -> None:
    """A runner failure surfaces as CaptureError with stderr and redacted
    secret-sourced args."""
    raise NotImplementedError


def test_secret_and_capture_vars_are_sensitive() -> None:
    """Variables defined via secret/capture are redacted by describe_source,
    regardless of name, and captures are not executed for inspection."""
    raise NotImplementedError
