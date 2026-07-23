"""Project-configuration tests. Covers PLAN.md "Config tests"."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.skip(reason="TODO: implement — see PLAN.md 'Config tests'")


def test_load_defaults() -> None:
    """Omitted optional fields fall back to documented defaults."""
    raise NotImplementedError


def test_output_path_substitution() -> None:
    """`{target}` in output.path is substituted with the target name."""
    raise NotImplementedError


def test_output_template_injects_document_token() -> None:
    """The serialized YAML replaces the literal `{{ document }}` token."""
    raise NotImplementedError


def test_no_template_writes_yaml_verbatim() -> None:
    """With no output.template, the serialized YAML is written unchanged."""
    raise NotImplementedError


def test_validator_selection_resolution() -> None:
    """--validator names override output defaults; unknown names raise ConfigError."""
    raise NotImplementedError


def test_invalid_config_raises_config_error() -> None:
    """A malformed or wrong-version project config raises ConfigError (exit 7)."""
    raise NotImplementedError
