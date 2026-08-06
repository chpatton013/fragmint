"""Tests for :mod:`fragmint.formats` — the format boundary.

The YAML tests below are carried over from the module's earlier name
(``yamlio``) and focus on the YAML 1.1 quoting rule documented in
:mod:`fragmint.formats` and README.md "Serialization": a bare scalar that a
YAML 1.1 parser (e.g. PyYAML, which cloud-init uses) would resolve to
something other than the identical string must be quoted, even though
ruamel's default YAML 1.2 emitter would leave it bare. This is the one
regression the committed snapshot fixtures cannot catch (no fixture happens
to contain an affected value), so these tests are its only guard.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fragmint.errors import FragmintError
from fragmint.formats import (
    codec_for,
    dump_document,
    format_for_path,
    load_data_file,
)

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


def dump_yaml(document: object) -> str:
    return dump_document(document, "yaml")  # type: ignore[arg-type]


@pytest.mark.parametrize("value", CORRUPTING_VALUES)
def test_yaml_1_1_corrupting_values_are_quoted(value: str) -> None:
    text = dump_yaml({"key": value})
    assert text == f"key: '{value}'\n"


@pytest.mark.parametrize("value", SPEC_ONLY_BOOLEAN_ALIASES)
def test_spec_only_boolean_aliases_are_quoted(value: str) -> None:
    """Quoted on the spec resolver's authority alone, without consulting any
    particular reader's leniency — a bare `y` is a boolean to a spec-faithful
    YAML 1.1 parser."""
    text = dump_yaml({"key": value})
    assert text == f"key: '{value}'\n"


def test_real_booleans_still_emit_bare() -> None:
    text = dump_yaml({"a": True, "b": False})
    assert text == "a: true\nb: false\n"


@pytest.mark.parametrize("value", ["true", "0755", "null", ".inf", "1_000"])
def test_already_1_2_ambiguous_values_are_quoted_exactly_once(value: str) -> None:
    text = dump_yaml({"key": value})
    assert text == f"key: '{value}'\n"
    # No nested/doubled quoting: exactly two single-quote characters.
    assert text.count("'") == 2


def test_multiline_strings_still_use_block_literal_style() -> None:
    text = dump_yaml({"key": "line one\nline two\n"})
    assert text == "key: |\n  line one\n  line two\n"


@pytest.mark.parametrize("value", CORRUPTING_VALUES)
def test_dangerous_values_are_quoted_when_nested(value: str) -> None:
    document = {
        "top": [
            {"nested": value},
            value,
        ]
    }
    text = dump_yaml(document)
    assert f"nested: '{value}'" in text
    assert f"- '{value}'" in text
    assert f"- {value}\n" not in text


# --- Dispatch and inference ---------------------------------------------------


def test_format_for_path_infers_from_known_suffixes() -> None:
    assert format_for_path("a.yaml") == "yaml"
    assert format_for_path("a.toml") == "toml"
    assert format_for_path("a.json") == "json"


def test_yml_suffix_infers_yaml_for_outputs() -> None:
    assert format_for_path("a.yml") == "yaml"


def test_format_for_path_returns_none_for_unknown_suffix() -> None:
    assert format_for_path("a.conf") is None


def test_format_for_path_returns_none_without_suffix() -> None:
    assert format_for_path("a") is None


def test_load_data_file_dispatches_on_suffix(tmp_path: Path) -> None:
    (tmp_path / "a.toml").write_text('a = "x"\n')
    (tmp_path / "a.json").write_text('{"a": "x"}')
    (tmp_path / "a.yaml").write_text("a: x\n")
    assert load_data_file(tmp_path / "a.toml") == {"a": "x"}
    assert load_data_file(tmp_path / "a.json") == {"a": "x"}
    assert load_data_file(tmp_path / "a.yaml") == {"a": "x"}


def test_load_data_file_defaults_to_yaml_for_unknown_suffix(tmp_path: Path) -> None:
    (tmp_path / "a.conf").write_text("a: x\n")
    assert load_data_file(tmp_path / "a.conf") == {"a": "x"}


def test_load_data_file_error_names_the_path_and_format(tmp_path: Path) -> None:
    bad = tmp_path / "a.toml"
    bad.write_text("not valid toml === \n")
    with pytest.raises(FragmintError) as excinfo:
        load_data_file(bad)
    message = str(excinfo.value)
    assert str(bad) in message
    assert "toml" in message


# --- Model shape agreement -----------------------------------------------------


def test_all_codecs_load_equivalent_documents_to_equal_data(tmp_path: Path) -> None:
    expected = {"a": 1, "b": [1, 2, "three"], "c": {"nested": True}}
    yaml_text = "a: 1\nb: [1, 2, three]\nc:\n  nested: true\n"
    toml_text = 'a = 1\nb = [1, 2, "three"]\n[c]\nnested = true\n'
    json_text = '{"a": 1, "b": [1, 2, "three"], "c": {"nested": true}}'
    assert codec_for("yaml").load(yaml_text, path=tmp_path) == expected
    assert codec_for("toml").load(toml_text, path=tmp_path) == expected
    assert codec_for("json").load(json_text, path=tmp_path) == expected


def test_loaded_mapping_keys_are_strings_in_every_format(tmp_path: Path) -> None:
    for fmt, text in (
        ("yaml", "a: 1\n"),
        ("toml", "a = 1\n"),
        ("json", '{"a": 1}'),
    ):
        data = codec_for(fmt).load(text, path=tmp_path)  # type: ignore[arg-type]
        assert isinstance(data, dict)
        assert all(isinstance(key, str) for key in data)


# --- TOML: date/time input rejection -------------------------------------------


def test_toml_load_rejects_date_value_naming_the_pointer(tmp_path: Path) -> None:
    with pytest.raises(FragmintError) as excinfo:
        codec_for("toml").load("a = 1979-05-27\n", path=tmp_path / "f.toml")
    assert "/a" in str(excinfo.value)


def test_toml_load_rejects_datetime_value_naming_the_pointer(tmp_path: Path) -> None:
    with pytest.raises(FragmintError) as excinfo:
        codec_for("toml").load("a = 1979-05-27T07:32:00Z\n", path=tmp_path / "f.toml")
    assert "/a" in str(excinfo.value)


def test_toml_load_rejects_time_value_naming_the_pointer(tmp_path: Path) -> None:
    with pytest.raises(FragmintError) as excinfo:
        codec_for("toml").load("a = 07:32:00\n", path=tmp_path / "f.toml")
    assert "/a" in str(excinfo.value)


def test_toml_load_rejects_date_nested_in_a_table_naming_the_pointer(tmp_path: Path) -> None:
    with pytest.raises(FragmintError) as excinfo:
        codec_for("toml").load("[a]\nb = 1979-05-27\n", path=tmp_path / "f.toml")
    assert "/a/b" in str(excinfo.value)


def test_toml_load_rejects_date_nested_in_a_list_naming_the_pointer(tmp_path: Path) -> None:
    with pytest.raises(FragmintError) as excinfo:
        codec_for("toml").load("a = [1979-05-27]\n", path=tmp_path / "f.toml")
    assert "/a/0" in str(excinfo.value)
