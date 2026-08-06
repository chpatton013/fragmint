"""Tests for :mod:`fragmint.yamlio` serialization, focused on the YAML 1.1
quoting rule documented in the module docstring and README.md "YAML
serialization": a bare scalar that a YAML 1.1 parser (e.g. PyYAML, which
cloud-init uses) would resolve to something other than the identical string
must be quoted, even though ruamel's default YAML 1.2 emitter would leave it
bare.
"""

from __future__ import annotations

import pytest

from fragmint.yamlio import dump_str

# Values a YAML 1.1 resolver reinterprets as a non-string type when left
# bare: the 1.1-only booleans (`yes|no|on|off`, dropped from the 1.2 core
# schema) and sexagesimal (h:mm[:ss]) integers (also dropped in 1.2).
CORRUPTING_VALUES = ["yes", "no", "on", "off", "12:30", "1:2:3"]

# Boolean aliases per the YAML 1.1 *spec*. PyYAML's concrete resolver happens
# not to implement this alias (its bool regex lists only
# yes/Yes/YES/no/No/NO/true/.../on/.../off/...), so PyYAML alone reads these
# back as strings either way. They are quoted regardless: the spec is the
# authority, so a stricter reader cannot retype them.
SPEC_ONLY_BOOLEAN_ALIASES = ["y", "n", "Y", "N"]


@pytest.mark.parametrize("value", CORRUPTING_VALUES)
def test_yaml_1_1_corrupting_values_are_quoted(value: str) -> None:
    text = dump_str({"key": value})
    assert text == f"key: '{value}'\n"


@pytest.mark.parametrize("value", SPEC_ONLY_BOOLEAN_ALIASES)
def test_spec_only_boolean_aliases_are_quoted(value: str) -> None:
    """Quoted on the spec resolver's authority alone, without consulting any
    particular reader's leniency — a bare `y` is a boolean to a spec-faithful
    YAML 1.1 parser."""
    text = dump_str({"key": value})
    assert text == f"key: '{value}'\n"


def test_real_booleans_still_emit_bare() -> None:
    text = dump_str({"a": True, "b": False})
    assert text == "a: true\nb: false\n"


@pytest.mark.parametrize("value", ["true", "0755", "null", ".inf", "1_000"])
def test_already_1_2_ambiguous_values_are_quoted_exactly_once(value: str) -> None:
    text = dump_str({"key": value})
    assert text == f"key: '{value}'\n"
    # No nested/doubled quoting: exactly two single-quote characters.
    assert text.count("'") == 2


def test_multiline_strings_still_use_block_literal_style() -> None:
    text = dump_str({"key": "line one\nline two\n"})
    assert text == "key: |\n  line one\n  line two\n"


@pytest.mark.parametrize("value", CORRUPTING_VALUES)
def test_dangerous_values_are_quoted_when_nested(value: str) -> None:
    document = {
        "top": [
            {"nested": value},
            value,
        ]
    }
    text = dump_str(document)
    assert f"nested: '{value}'" in text
    assert f"- '{value}'" in text
    assert f"- {value}\n" not in text
