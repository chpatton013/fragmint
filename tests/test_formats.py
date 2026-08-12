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

import json
import math
import tomllib
from pathlib import Path

import pytest

from fragmint.errors import FragmintError, SerializationError
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


# --- YAML: non-string mapping keys ---------------------------------------------


def test_yaml_load_rejects_integer_key_naming_the_file(tmp_path: Path) -> None:
    bad = tmp_path / "f.yaml"
    with pytest.raises(FragmintError) as excinfo:
        codec_for("yaml").load("1: x\n", path=bad)
    message = str(excinfo.value)
    assert str(bad) in message
    assert "1" in message


def test_yaml_load_rejects_boolean_key(tmp_path: Path) -> None:
    with pytest.raises(FragmintError) as excinfo:
        codec_for("yaml").load("true: x\n", path=tmp_path / "f.yaml")
    assert "True" in str(excinfo.value) or "true" in str(excinfo.value)


def test_yaml_load_rejects_null_key(tmp_path: Path) -> None:
    with pytest.raises(FragmintError) as excinfo:
        codec_for("yaml").load("null: x\n", path=tmp_path / "f.yaml")
    assert "None" in str(excinfo.value) or "null" in str(excinfo.value)


def test_yaml_load_rejects_integer_key_nested_in_a_mapping(tmp_path: Path) -> None:
    with pytest.raises(FragmintError) as excinfo:
        codec_for("yaml").load("a:\n  1: x\n", path=tmp_path / "f.yaml")
    assert "/a" in str(excinfo.value)


def test_yaml_load_rejects_integer_key_nested_in_a_list(tmp_path: Path) -> None:
    with pytest.raises(FragmintError) as excinfo:
        codec_for("yaml").load("a:\n  - 1: x\n", path=tmp_path / "f.yaml")
    assert "/a/0" in str(excinfo.value)


def test_yaml_load_does_not_collapse_int_and_string_keys(tmp_path: Path) -> None:
    """Before the fix, `_normalize`'s `str(key)` coercion made an int key `1`
    and a string key `'1'` collide, with the later value silently winning —
    data loss with no diagnostic. Now the int key is rejected before that can
    happen."""
    with pytest.raises(FragmintError) as excinfo:
        codec_for("yaml").load("1: first\n'1': second\n", path=tmp_path / "f.yaml")
    assert "1" in str(excinfo.value)


def test_yaml_load_duplicate_key_diagnostic_names_int_and_bool_ambiguity(
    tmp_path: Path,
) -> None:
    """Sibling keys `1` and `true` are rejected by ruamel as *duplicate* keys
    before `_normalize` ever runs, because `1 == True` in Python. The bare
    ruamel message ('found duplicate key "True"') does not explain that the
    original key was spelled `1`; the wrapped message must add that
    explanation rather than merely repeating ruamel's."""
    with pytest.raises(FragmintError) as excinfo:
        codec_for("yaml").load("1: x\ntrue: y\n", path=tmp_path / "f.yaml")
    message = str(excinfo.value)
    assert "collide" in message


def test_yaml_load_accepts_ordinary_string_keys(tmp_path: Path) -> None:
    data = codec_for("yaml").load("a: 1\nb.c: 2\nd/e: 3\n", path=tmp_path / "f.yaml")
    assert data == {"a": 1, "b.c": 2, "d/e": 3}


def test_yaml_load_accepts_a_quoted_numeric_looking_string_key(tmp_path: Path) -> None:
    data = codec_for("yaml").load("'1': x\n", path=tmp_path / "f.yaml")
    assert data == {"1": "x"}


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


# --- JSON: writing --------------------------------------------------------------


def test_json_dump_uses_two_space_indent() -> None:
    text = dump_document({"a": 1, "b": [1, 2]}, "json")
    assert text == '{\n  "a": 1,\n  "b": [\n    1,\n    2\n  ]\n}\n'


def test_json_dump_preserves_mapping_insertion_order() -> None:
    text = dump_document({"z": 1, "a": 2}, "json")
    assert text.index('"z"') < text.index('"a"')


def test_json_dump_does_not_escape_non_ascii() -> None:
    text = dump_document({"u": "héllo→"}, "json")
    assert "héllo→" in text
    assert "\\u" not in text


def test_json_dump_ends_with_exactly_one_newline() -> None:
    text = dump_document({"a": 1}, "json")
    assert text.endswith("\n")
    assert not text.endswith("\n\n")


def test_json_dump_rejects_nan_naming_the_pointer() -> None:
    with pytest.raises(SerializationError) as excinfo:
        dump_document({"a": float("nan")}, "json")
    assert "/a" in str(excinfo.value)


def test_json_dump_rejects_infinity_naming_the_pointer() -> None:
    with pytest.raises(SerializationError) as excinfo:
        dump_document({"a": float("inf")}, "json")
    assert "/a" in str(excinfo.value)


def test_json_dump_rejects_non_finite_float_nested_in_a_list() -> None:
    with pytest.raises(SerializationError) as excinfo:
        dump_document({"a": [1, float("-inf")]}, "json")
    assert "/a/1" in str(excinfo.value)


def test_json_dump_round_trips_through_json_loads() -> None:
    document = {"a": 1, "b": [1, "two", True, None], "c": {"nested": 3.5}}
    text = dump_document(document, "json")
    assert json.loads(text) == document


# --- TOML: writing ---------------------------------------------------------------


def test_toml_dump_round_trips_through_tomllib() -> None:
    document = {"a": 1, "b": [1, 2, "three"], "c": {"nested": True}}
    text = dump_document(document, "toml")
    assert tomllib.loads(text) == document


def test_toml_dump_is_byte_identical_across_repeated_calls() -> None:
    document = {"a": 1, "b": {"x": 1, "y": 2}, "c": [1, 2, 3]}
    texts = {dump_document(document, "toml") for _ in range(50)}
    assert len(texts) == 1


def test_toml_dump_preserves_scalar_key_order_within_a_table() -> None:
    text = dump_document({"z": 1, "a": 2, "m": 3}, "toml")
    assert text.index("z") < text.index("a") < text.index("m")


def test_toml_dump_emits_subtables_after_a_tables_scalar_keys() -> None:
    document = {"sub": {"x": 1}, "scalar": "value"}
    text = dump_document(document, "toml")
    assert text.index("scalar") < text.index("[sub]")


def test_toml_dump_emits_list_of_mappings_as_array_of_tables() -> None:
    # tomli_w only switches to `[[name]]` array-of-tables syntax when an
    # inline rendering would not fit on one line; a long value forces that
    # here so the assertion exercises the syntax this test names.
    document = {"items": [{"a": 1, "b": "x" * 90}, {"a": 2, "b": "y" * 90}]}
    text = dump_document(document, "toml")
    assert "[[items]]" in text
    assert tomllib.loads(text) == document


def test_toml_dump_uses_multiline_strings_for_embedded_newlines() -> None:
    document = {"a": "line one\nline two\n"}
    text = dump_document(document, "toml")
    assert '"""' in text
    assert tomllib.loads(text) == document


def test_toml_dump_falls_back_to_single_line_strings_when_a_value_contains_cr() -> None:
    document = {"a": "line one\nline two\n", "b": "has\rcr"}
    text = dump_document(document, "toml")
    assert '"""' not in text


def test_toml_dump_carriage_return_fallback_round_trips_exactly() -> None:
    document = {"a": "line one\nline two\n", "b": "has\r\ncrlf\r\n"}
    text = dump_document(document, "toml")
    assert tomllib.loads(text) == document


def test_toml_dump_rejects_null_naming_the_pointer() -> None:
    with pytest.raises(SerializationError) as excinfo:
        dump_document({"a": None}, "toml")
    assert "/a" in str(excinfo.value)


def test_toml_dump_rejects_null_nested_in_a_mapping_naming_the_pointer() -> None:
    with pytest.raises(SerializationError) as excinfo:
        dump_document({"a": {"b": None}}, "toml")
    assert "/a/b" in str(excinfo.value)


def test_toml_dump_rejects_null_inside_a_list_naming_the_pointer() -> None:
    with pytest.raises(SerializationError) as excinfo:
        dump_document({"a": [1, None]}, "toml")
    assert "/a/1" in str(excinfo.value)


def test_toml_dump_accepts_non_finite_floats() -> None:
    document = {"a": float("nan"), "b": float("inf"), "c": float("-inf")}
    text = dump_document(document, "toml")
    loaded = tomllib.loads(text)
    assert math.isnan(loaded["a"])
    assert loaded["b"] == float("inf")
    assert loaded["c"] == float("-inf")


def test_toml_dump_escapes_keys_that_need_it() -> None:
    document = {"has space": 1, "a.b": 2, "": 3}
    text = dump_document(document, "toml")
    assert tomllib.loads(text) == document


def test_toml_dump_pointer_uses_json_pointer_escaping() -> None:
    with pytest.raises(SerializationError) as excinfo:
        dump_document({"a/b": None}, "toml")
    assert "/a~1b" in str(excinfo.value)
