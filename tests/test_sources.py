"""Variable value source tests. See README.md "Variable value sources".

Capture tests MUST use a stubbed CommandRunner — never run real subprocesses.
"""

from __future__ import annotations

import pytest

from fragmint.errors import CaptureError, ModuleError, SecretNotFoundError
from fragmint.models import (
    CaptureSource,
    DataValue,
    LiteralSource,
    SecretSource,
    SecretStore,
    VariableReferenceSource,
)
from fragmint.sources import (
    describe_source,
    is_source,
    parse_source,
    resolve_source,
)


def test_untagged_value_is_literal() -> None:
    """A scalar/list/plain mapping (no reserved `from:`) parses as a literal."""
    assert is_source("us") is False
    assert is_source([1, 2, 3]) is False
    assert is_source({"a": 1}) is False
    assert parse_source("us") == LiteralSource(value="us")
    assert parse_source({"a": 1}) == LiteralSource(value={"a": 1})


def test_from_literal_escape_hatch() -> None:
    """`{from: literal, value: {from: ...}}` yields the inner mapping literally."""
    raw = {"from": "literal", "value": {"from": "us-east-1"}}
    assert is_source(raw) is True
    source = parse_source(raw)
    assert source == LiteralSource(value={"from": "us-east-1"})


def test_mapping_with_nonreserved_from_is_literal() -> None:
    """`{from: us-east-1}` is a literal, not a source (from value not reserved)."""
    raw: DataValue = {"from": "us-east-1"}
    assert is_source(raw) is False
    assert parse_source(raw) == LiteralSource(value=raw)


def test_variable_reference_source_is_a_redacted_alias_description() -> None:
    """`from: variable` names another variable without exposing its value."""
    raw: DataValue = {"from": "variable", "name": "password_hash"}
    assert is_source(raw) is True
    assert parse_source(raw) == VariableReferenceSource(name="password_hash")
    assert describe_source(raw) == "<variable password_hash>"


def test_capture_inputs_reject_variable_references() -> None:
    """Capture argv/stdin reject variable aliases."""
    with pytest.raises(ModuleError, match="not allowed here"):
        parse_source(
            {
                "from": "capture",
                "command": [{"from": "variable", "name": "password_hash"}],
            }
        )


def test_secret_resolves_from_store() -> None:
    """`{from: secret, name: N}` resolves to the store value for N."""
    store = SecretStore(secrets={"gb10-01_password": "hunter2"})
    source = parse_source({"from": "secret", "name": "gb10-01_password"})
    assert source == SecretSource(name="gb10-01_password")
    value = resolve_source(
        source,
        secrets=store,
        runner=None,  # type: ignore[arg-type]
        scope="target 'gb10-01'",
        variable="identity_password",
    )
    assert value == "hunter2"


def test_missing_secret_raises() -> None:
    """An absent secret name raises SecretNotFoundError naming the scope
    (here, a real target) and the variable, not the value."""
    store = SecretStore(secrets={})
    source = SecretSource(name="nope")
    with pytest.raises(SecretNotFoundError) as excinfo:
        resolve_source(
            source,
            secrets=store,
            runner=None,  # type: ignore[arg-type]
            scope="target 'gb10-01'",
            variable="identity_password",
        )
    message = str(excinfo.value)
    assert message == (
        "target 'gb10-01': variable 'identity_password': "
        "secret 'nope' not found in secret store"
    )


def test_missing_secret_in_aggregate_scope_does_not_claim_a_target() -> None:
    """The aggregate scope has no target, so its failure message must not say
    `target` — `resolve_source` renders whatever scope label the caller
    passes verbatim (README.md "The aggregate scope"; the caller,
    `RenderSession._aggregate_variable`, passes `scope="aggregate scope"`)."""
    store = SecretStore(secrets={})
    source = SecretSource(name="nope")
    with pytest.raises(SecretNotFoundError) as excinfo:
        resolve_source(
            source,
            secrets=store,
            runner=None,  # type: ignore[arg-type]
            scope="aggregate scope",
            variable="aggsecret",
        )
    message = str(excinfo.value)
    assert message == (
        "aggregate scope: variable 'aggsecret': secret 'nope' not found in secret store"
    )
    assert "target" not in message


def test_capture_uses_stubbed_runner(stub_runner) -> None:  # type: ignore[no-untyped-def]
    """`from: capture` calls the injected runner with the resolved argv and
    stdin, and returns its stdout."""
    store = SecretStore(secrets={})
    source = parse_source(
        {"from": "capture", "command": ["openssl", "passwd", "-6", "-stdin"]}
    )
    assert isinstance(source, CaptureSource)
    value = resolve_source(
        source,
        secrets=store,
        runner=stub_runner,
        scope="target 'gb10-01'",
        variable="identity_password_hash",
    )
    assert value == "$6$stubsalt$stubhash"
    assert stub_runner.calls == [(["openssl", "passwd", "-6", "-stdin"], None)]


def test_capture_args_and_stdin_from_sources(stub_runner) -> None:  # type: ignore[no-untyped-def]
    """command elements and stdin may themselves be literal/secret sources."""
    store = SecretStore(secrets={"gb10-01_password": "hunter2"})
    source = parse_source(
        {
            "from": "capture",
            "command": ["openssl", "passwd", "-6", "-stdin"],
            "stdin": {"from": "secret", "name": "gb10-01_password"},
        }
    )
    resolve_source(
        source,
        secrets=store,
        runner=stub_runner,
        scope="target 'gb10-01'",
        variable="identity_password_hash",
    )
    assert stub_runner.calls == [(["openssl", "passwd", "-6", "-stdin"], "hunter2")]


def test_capture_trim_default_and_off(stub_runner) -> None:  # type: ignore[no-untyped-def]
    """`trim` strips one trailing newline by default; `trim: false` keeps it."""
    store = SecretStore(secrets={})

    default_source = parse_source({"from": "capture", "command": ["echo"]})
    assert isinstance(default_source, CaptureSource)
    assert default_source.trim is True
    value = resolve_source(
        default_source,
        secrets=store,
        runner=stub_runner,
        scope="target 't'",
        variable="v",
    )
    assert value == "$6$stubsalt$stubhash"

    untrimmed_source = parse_source({"from": "capture", "command": ["echo"], "trim": False})
    assert isinstance(untrimmed_source, CaptureSource)
    assert untrimmed_source.trim is False
    value = resolve_source(
        untrimmed_source,
        secrets=store,
        runner=stub_runner,
        scope="target 't'",
        variable="v",
    )
    assert value == "$6$stubsalt$stubhash\n"


def test_capture_failure_raises_capture_error() -> None:
    """A runner failure surfaces as CaptureError with stderr and redacted
    secret-sourced args."""

    class FailingRunner:
        def run(self, command, *, stdin, timeout):  # type: ignore[no-untyped-def]
            raise CaptureError(
                f"capture command {command[0]!r} failed with exit 1: boom stderr"
            )

    store = SecretStore(secrets={"pw": "hunter2"})
    source = parse_source(
        {
            "from": "capture",
            "command": ["openssl", "passwd", "-6", "-stdin"],
            "stdin": {"from": "secret", "name": "pw"},
        }
    )
    with pytest.raises(CaptureError) as excinfo:
        resolve_source(
            source,
            secrets=store,
            runner=FailingRunner(),
            scope="target 'gb10-01'",
            variable="identity_password_hash",
        )
    message = str(excinfo.value)
    assert "openssl" in message
    assert "boom stderr" in message
    assert "hunter2" not in message


def test_secret_and_capture_vars_are_sensitive() -> None:
    """Variables defined via secret/capture are redacted by describe_source,
    regardless of name, and captures are not executed for inspection."""
    secret_desc = describe_source({"from": "secret", "name": "gb10-01_password"})
    assert secret_desc == "<secret gb10-01_password>"
    assert "gb10-01_password" not in secret_desc.replace("<secret gb10-01_password>", "")

    capture_desc = describe_source(
        {"from": "capture", "command": ["openssl", "passwd", "-6", "-stdin"]}
    )
    assert capture_desc == "<capture: openssl passwd -6 -stdin>"
