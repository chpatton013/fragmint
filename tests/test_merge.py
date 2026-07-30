"""Unit tests for merge operations, templating, pointers, and provenance.

See README.md "Merge operations" and "Template rendering" for the behavior
each test asserts.
"""

from __future__ import annotations

import pytest

from yaml_frag import merge, pointer, templating
from yaml_frag.errors import (
    AssertionFailedError,
    MergeConflictError,
    TemplateRenderError,
    YamlFragError,
)
from yaml_frag.models import FragmentOperation, ProvenanceEntry
from yaml_frag.provenance import ProvenanceTracker
from yaml_frag.render import _render_operation_path


def _entry(fragment: str = "frag", operation_index: int = 0, op: str = "set") -> ProvenanceEntry:
    return ProvenanceEntry(fragment=fragment, operation_index=operation_index, operation=op)


def _apply(document: dict, operation: FragmentOperation, tracker: ProvenanceTracker | None = None, **entry_kwargs):
    tracker = tracker if tracker is not None else ProvenanceTracker()
    entry = _entry(op=operation.op, **entry_kwargs)
    merge.apply_operation(document, operation, entry=entry, tracker=tracker)
    return document, tracker, entry


def test_set_root() -> None:
    """`set` at `/` replaces the whole document."""
    document = {"a": 1}
    op = FragmentOperation(op="set", path="/", value={"b": 2})
    _apply(document, op)
    assert document == {"b": 2}


def test_set_nested_path_creates_parents() -> None:
    """`set` at a deep path creates missing parent mappings."""
    document: dict = {}
    op = FragmentOperation(op="set", path="/a/b/c", value=42)
    _apply(document, op)
    assert document == {"a": {"b": {"c": 42}}}


def test_merge_nested_mappings() -> None:
    """`merge` recursively merges mappings; scalars are replaced."""
    document = {"autoinstall": {"keyboard": {"layout": "us"}, "locale": "en_US.UTF-8"}}
    op = FragmentOperation(
        op="merge",
        path="/autoinstall",
        value={"keyboard": {"variant": ""}, "locale": "fr_FR.UTF-8"},
    )
    _apply(document, op)
    assert document == {
        "autoinstall": {
            "keyboard": {"layout": "us", "variant": ""},
            "locale": "fr_FR.UTF-8",
        }
    }


def test_merge_rejects_mapping_list_conflict() -> None:
    """`merge` of a mapping onto an existing list raises MergeConflictError."""
    document = {"autoinstall": {"user-data": {"packages": ["curl"]}}}
    op = FragmentOperation(
        op="merge",
        path="/autoinstall/user-data",
        value={"packages": {"not": "a list"}},
    )
    with pytest.raises(MergeConflictError):
        _apply(document, op)


def test_merge_identical_list_is_allowed() -> None:
    """`merge` where both sides have an identical list at a path succeeds."""
    document = {"autoinstall": {"packages": ["curl", "vim-tiny"]}}
    op = FragmentOperation(
        op="merge",
        path="/autoinstall",
        value={"packages": ["curl", "vim-tiny"]},
    )
    _apply(document, op)
    assert document == {"autoinstall": {"packages": ["curl", "vim-tiny"]}}


def test_append_to_missing_list_creates_it() -> None:
    """`append` creates the target list when absent."""
    document: dict = {}
    op = FragmentOperation(op="append", path="/packages", value=["curl", "ca-certificates"])
    _apply(document, op)
    assert document == {"packages": ["curl", "ca-certificates"]}


def test_append_to_existing_list_preserves_order() -> None:
    """`append` keeps existing then incoming order; no dedupe by default."""
    document = {"packages": ["curl", "ca-certificates"]}
    op = FragmentOperation(op="append", path="/packages", value=["curl", "qemu-guest-agent"])
    _apply(document, op)
    assert document == {"packages": ["curl", "ca-certificates", "curl", "qemu-guest-agent"]}


def test_append_with_deduplicate_keeps_first() -> None:
    """`append` with deduplicate removes later structural duplicates."""
    document = {"packages": ["curl", "ca-certificates"]}
    op = FragmentOperation(
        op="append",
        path="/packages",
        value=["curl", "qemu-guest-agent"],
        deduplicate=True,
    )
    _apply(document, op)
    assert document == {"packages": ["curl", "ca-certificates", "qemu-guest-agent"]}


def test_append_non_list_target_fails() -> None:
    """`append` onto a non-list existing value raises."""
    document = {"packages": {"not": "a list"}}
    op = FragmentOperation(op="append", path="/packages", value=["curl"])
    with pytest.raises(MergeConflictError):
        _apply(document, op)


def test_prepend_preserves_incoming_order() -> None:
    """`prepend` inserts incoming values (in order) before existing ones."""
    document = {"runcmd": ["echo done"]}
    op = FragmentOperation(op="prepend", path="/runcmd", value=["echo start", "echo mid"])
    _apply(document, op)
    assert document == {"runcmd": ["echo start", "echo mid", "echo done"]}


def test_remove_existing_field() -> None:
    """`remove` deletes an existing field."""
    document = {"autoinstall": {"oem": {"enabled": True}, "keep": 1}}
    op = FragmentOperation(op="remove", path="/autoinstall/oem")
    _apply(document, op)
    assert document == {"autoinstall": {"keep": 1}}


def test_remove_missing_without_missing_ok_fails() -> None:
    """`remove` of an absent path fails unless missing_ok is set."""
    document: dict = {"autoinstall": {}}
    op = FragmentOperation(op="remove", path="/autoinstall/oem")
    with pytest.raises(YamlFragError):
        _apply(document, op)


def test_remove_missing_with_missing_ok_succeeds() -> None:
    """`remove` with missing_ok tolerates an absent path."""
    document: dict = {"autoinstall": {}}
    op = FragmentOperation(op="remove", path="/autoinstall/oem", missing_ok=True)
    _apply(document, op)
    assert document == {"autoinstall": {}}


def test_remove_list_items_structural_match() -> None:
    """`remove-list-items` removes all structurally-equal entries."""
    document = {"packages": ["curl", "vim-tiny", "curl"]}
    op = FragmentOperation(op="remove-list-items", path="/packages", value=["curl"])
    _apply(document, op)
    assert document == {"packages": ["vim-tiny"]}


def test_remove_list_items_no_match_fails() -> None:
    """`remove-list-items` fails when nothing matched unless missing_ok."""
    document = {"packages": ["vim-tiny"]}
    op = FragmentOperation(op="remove-list-items", path="/packages", value=["curl"])
    with pytest.raises(MergeConflictError):
        _apply(document, op)

    document2 = {"packages": ["vim-tiny"]}
    op_ok = FragmentOperation(
        op="remove-list-items", path="/packages", value=["curl"], missing_ok=True
    )
    _apply(document2, op_ok)
    assert document2 == {"packages": ["vim-tiny"]}


@pytest.mark.parametrize("form", ["equals", "exists_true", "exists_false", "type"])
def test_assertions(form: str) -> None:
    """Each supported assertion form passes/fails as specified."""
    document = {"autoinstall": {"version": 1, "storage": {"layout": {"name": "lvm"}}}}

    if form == "equals":
        ok = FragmentOperation(
            op="assert", path="/autoinstall/version", assertion={"equals": 1}
        )
        bad = FragmentOperation(
            op="assert", path="/autoinstall/version", assertion={"equals": 2}
        )
    elif form == "exists_true":
        ok = FragmentOperation(
            op="assert", path="/autoinstall/version", assertion={"exists": True}
        )
        bad = FragmentOperation(
            op="assert", path="/autoinstall/missing", assertion={"exists": True}
        )
    elif form == "exists_false":
        ok = FragmentOperation(
            op="assert", path="/autoinstall/missing", assertion={"exists": False}
        )
        bad = FragmentOperation(
            op="assert", path="/autoinstall/version", assertion={"exists": False}
        )
    else:  # type
        ok = FragmentOperation(
            op="assert",
            path="/autoinstall/storage/layout",
            assertion={"type": "mapping"},
        )
        bad = FragmentOperation(
            op="assert", path="/autoinstall/version", assertion={"type": "list"}
        )

    merge.apply_assert(document, ok)
    with pytest.raises(AssertionFailedError):
        merge.apply_assert(document, bad)


def test_json_pointer_escaping() -> None:
    """`~1` decodes to `/` and `~0` decodes to `~` per RFC 6901."""
    assert pointer.parse_pointer("") == ()
    assert pointer.parse_pointer("/") == ()
    assert pointer.parse_pointer("/a/b/c") == ("a", "b", "c")
    assert pointer.parse_pointer("/a~1b") == ("a/b",)
    assert pointer.parse_pointer("/a~0b") == ("a~b",)
    assert pointer.parse_pointer("/~0~1") == ("~/",)
    assert pointer.parse_pointer("/~01") == ("~1",)

    with pytest.raises(YamlFragError):
        pointer.parse_pointer("no-leading-slash")


def test_strict_missing_variable_fails() -> None:
    """A missing template variable raises TemplateRenderError with context."""
    with pytest.raises(TemplateRenderError) as excinfo:
        templating.render_value(
            "{{ identity_hostname }}",
            {},
            target="gb10-01",
            fragment="hardware/gb10",
            operation_index=2,
        )
    message = str(excinfo.value)
    assert "gb10-01" in message
    assert "hardware/gb10" in message
    assert "2" in message
    assert "identity_hostname" in message


def test_typed_template_values() -> None:
    """A whole-string `{{ bool_var }}` renders to a native bool, not a string."""
    result = templating.render_value(
        "{{ enable_package_upgrade }}",
        {"enable_package_upgrade": False},
        target="t",
        fragment="f",
        operation_index=0,
    )
    assert result is False

    embedded = templating.render_value(
        "value is {{ enable_package_upgrade }}",
        {"enable_package_upgrade": False},
        target="t",
        fragment="f",
        operation_index=0,
    )
    assert embedded == "value is False"


def test_provenance_recording() -> None:
    """Provenance records the last fragment/operation to touch each path."""
    document: dict = {}
    tracker = ProvenanceTracker()
    op1 = FragmentOperation(op="set", path="/autoinstall/kernel", value={"package": "linux-generic"})
    entry1 = _entry(fragment="ubuntu-24.04", operation_index=0, op="set")
    merge.apply_operation(document, op1, entry=entry1, tracker=tracker)

    op2 = FragmentOperation(
        op="set", path="/autoinstall/kernel", value={"package": "linux-generic-hwe-24.04"}
    )
    entry2 = _entry(fragment="hardware/gb10", operation_index=0, op="set")
    merge.apply_operation(document, op2, entry=entry2, tracker=tracker)

    last = tracker.last_source("/autoinstall/kernel")
    assert last is not None
    assert last.fragment == "hardware/gb10"
    assert last.operation_index == 0


def test_set_override_emits_warning() -> None:
    """`set` over an existing value records an override warning."""
    document: dict = {"autoinstall": {"kernel": {"package": "linux-generic"}}}
    tracker = ProvenanceTracker()
    entry1 = _entry(fragment="ubuntu-24.04", operation_index=0, op="set")
    merge.apply_operation(
        document,
        FragmentOperation(op="set", path="/autoinstall/kernel", value={"package": "linux-generic"}),
        entry=entry1,
        tracker=tracker,
    )
    assert tracker.overrides == []

    entry2 = _entry(fragment="hardware/gb10", operation_index=0, op="set")
    merge.apply_operation(
        document,
        FragmentOperation(
            op="set", path="/autoinstall/kernel", value={"package": "linux-generic-hwe-24.04"}
        ),
        entry=entry2,
        tracker=tracker,
    )
    assert len(tracker.overrides) == 1
    warning = tracker.overrides[0]
    assert "hardware/gb10" in warning
    assert "ubuntu-24.04" in warning
    assert "/autoinstall/kernel" in warning


def test_set_overwrite_ok_suppresses_warning() -> None:
    """`overwrite_ok: true` on `set` replaces the value but emits no warning."""
    document: dict = {"autoinstall": {"kernel": {"package": "linux-generic"}}}
    tracker = ProvenanceTracker()
    entry1 = _entry(fragment="ubuntu-24.04", operation_index=0, op="set")
    merge.apply_operation(
        document,
        FragmentOperation(op="set", path="/autoinstall/kernel", value={"package": "linux-generic"}),
        entry=entry1,
        tracker=tracker,
    )

    entry2 = _entry(fragment="hardware/gb10", operation_index=0, op="set")
    merge.apply_operation(
        document,
        FragmentOperation(
            op="set",
            path="/autoinstall/kernel",
            value={"package": "linux-generic-hwe-24.04"},
            overwrite_ok=True,
        ),
        entry=entry2,
        tracker=tracker,
    )
    assert document["autoinstall"]["kernel"] == {"package": "linux-generic-hwe-24.04"}
    assert tracker.overrides == []
    # The replacement is still recorded for provenance/`explain`.
    last = tracker.last_source("/autoinstall/kernel")
    assert last is not None
    assert last.fragment == "hardware/gb10"


def test_merge_overwrite_ok_suppresses_scalar_warning() -> None:
    """`overwrite_ok: true` on `merge` suppresses the scalar-replace warning."""
    document: dict = {"autoinstall": {"locale": "en_US.UTF-8"}}
    tracker = ProvenanceTracker()
    entry1 = _entry(fragment="ubuntu-24.04", operation_index=0, op="merge")
    merge.apply_operation(
        document,
        FragmentOperation(op="merge", path="/autoinstall", value={"locale": "en_US.UTF-8"}),
        entry=entry1,
        tracker=tracker,
    )

    entry2 = _entry(fragment="hardware/gb10", operation_index=0, op="merge")
    merge.apply_operation(
        document,
        FragmentOperation(
            op="merge", path="/autoinstall", value={"locale": "en_GB.UTF-8"}, overwrite_ok=True
        ),
        entry=entry2,
        tracker=tracker,
    )
    assert document["autoinstall"]["locale"] == "en_GB.UTF-8"
    assert tracker.overrides == []


# --- Additional coverage -----------------------------------------------


def test_pointer_get_missing_intermediate_is_absent() -> None:
    found, value = pointer.get({"a": 1}, "/a/b")
    assert found is False
    assert value is None


def test_merge_requires_mapping_value() -> None:
    document = {"autoinstall": {}}
    op = FragmentOperation(op="merge", path="/autoinstall", value=["not", "a", "mapping"])
    with pytest.raises(MergeConflictError):
        _apply(document, op)


def test_structurally_equal_bool_vs_int() -> None:
    assert merge.structurally_equal(True, True) is True
    assert merge.structurally_equal(True, 1) is False
    assert merge.structurally_equal([1, {"a": 1}], [1, {"a": 1}]) is True
    assert merge.structurally_equal({"a": 1, "b": 2}, {"b": 2, "a": 1}) is True


def test_templating_allowed_filters_only() -> None:
    result = templating.render_value(
        "{{ name | upper }}", {"name": "gb10"}, target="t", fragment="f", operation_index=0
    )
    assert result == "GB10"

    with pytest.raises(TemplateRenderError):
        templating.render_value(
            "{{ ''.__class__ }}", {}, target="t", fragment="f", operation_index=0
        )


# --- Templated operation paths (README.md "Template rendering", "Paths") ---


def test_render_operation_path_happy_path() -> None:
    """A template in a position (not just a value) renders to a valid pointer."""
    result = _render_operation_path(
        "/all/children/{{ ansible_group }}/hosts/{{ target }}",
        {"ansible_group": "gb10", "target": "gb10-01"},
        target="gb10-01",
        fragment="ansible/host",
        operation_index=0,
    )
    assert result == "/all/children/gb10/hosts/gb10-01"


def test_render_operation_path_literal_path_unchanged() -> None:
    """A path with no template syntax renders through unchanged."""
    result = _render_operation_path(
        "/autoinstall/kernel",
        {},
        target="t",
        fragment="f",
        operation_index=0,
    )
    assert result == "/autoinstall/kernel"


def test_render_operation_path_missing_variable_fails_closed() -> None:
    """A missing variable referenced from a path fails closed, naming the
    fragment/operation, exactly like a missing variable in a value."""
    with pytest.raises(TemplateRenderError) as excinfo:
        _render_operation_path(
            "/all/children/{{ ansible_group }}/hosts/{{ target }}",
            {"target": "gb10-01"},
            target="gb10-01",
            fragment="ansible/host",
            operation_index=3,
        )
    message = str(excinfo.value)
    assert "ansible/host" in message
    assert "3" in message
    assert "ansible_group" in message


def test_render_operation_path_non_string_result_fails_closed() -> None:
    """A template that renders to a non-string (e.g. a whole-expression
    reference to a boolean/number/mapping variable) is rejected before
    `pointer.set_` ever sees it."""
    with pytest.raises(TemplateRenderError) as excinfo:
        _render_operation_path(
            "{{ not_a_string }}",
            {"not_a_string": 42},
            target="t",
            fragment="f",
            operation_index=0,
        )
    assert "must render to a string" in str(excinfo.value)


def test_render_operation_path_malformed_pointer_result_fails_closed() -> None:
    """A template that renders to a string that isn't a valid JSON Pointer
    (doesn't start with `/`) is rejected before `pointer.set_` ever sees it."""
    with pytest.raises(TemplateRenderError) as excinfo:
        _render_operation_path(
            "{{ bad_path }}",
            {"bad_path": "no-leading-slash"},
            target="t",
            fragment="f",
            operation_index=0,
        )
    assert "not a valid JSON Pointer" in str(excinfo.value)
