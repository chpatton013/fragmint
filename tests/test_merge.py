"""Unit tests for merge operations, templating, pointers, and provenance.

Covers PLAN.md "Unit tests". Each test is a stub to implement; the docstring
states the exact behavior to assert. Remove the module-level skip as tests are
filled in.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.skip(reason="TODO: implement — see PLAN.md 'Unit tests'")


def test_set_root() -> None:
    """`set` at `/` replaces the whole document."""
    raise NotImplementedError


def test_set_nested_path_creates_parents() -> None:
    """`set` at a deep path creates missing parent mappings."""
    raise NotImplementedError


def test_merge_nested_mappings() -> None:
    """`merge` recursively merges mappings; scalars are replaced."""
    raise NotImplementedError


def test_merge_rejects_mapping_list_conflict() -> None:
    """`merge` of a mapping onto an existing list raises MergeConflictError."""
    raise NotImplementedError


def test_merge_identical_list_is_allowed() -> None:
    """`merge` where both sides have an identical list at a path succeeds."""
    raise NotImplementedError


def test_append_to_missing_list_creates_it() -> None:
    """`append` creates the target list when absent."""
    raise NotImplementedError


def test_append_to_existing_list_preserves_order() -> None:
    """`append` keeps existing then incoming order; no dedupe by default."""
    raise NotImplementedError


def test_append_with_deduplicate_keeps_first() -> None:
    """`append` with deduplicate removes later structural duplicates."""
    raise NotImplementedError


def test_append_non_list_target_fails() -> None:
    """`append` onto a non-list existing value raises."""
    raise NotImplementedError


def test_prepend_preserves_incoming_order() -> None:
    """`prepend` inserts incoming values (in order) before existing ones."""
    raise NotImplementedError


def test_remove_existing_field() -> None:
    """`remove` deletes an existing field."""
    raise NotImplementedError


def test_remove_missing_without_missing_ok_fails() -> None:
    """`remove` of an absent path fails unless missing_ok is set."""
    raise NotImplementedError


def test_remove_missing_with_missing_ok_succeeds() -> None:
    """`remove` with missing_ok tolerates an absent path."""
    raise NotImplementedError


def test_remove_list_items_structural_match() -> None:
    """`remove-list-items` removes all structurally-equal entries."""
    raise NotImplementedError


def test_remove_list_items_no_match_fails() -> None:
    """`remove-list-items` fails when nothing matched unless missing_ok."""
    raise NotImplementedError


@pytest.mark.parametrize("form", ["equals", "exists_true", "exists_false", "type"])
def test_assertions(form: str) -> None:
    """Each supported assertion form passes/fails as specified."""
    raise NotImplementedError


def test_json_pointer_escaping() -> None:
    """`~1` decodes to `/` and `~0` decodes to `~` per RFC 6901."""
    raise NotImplementedError


def test_strict_missing_variable_fails() -> None:
    """A missing template variable raises TemplateRenderError with context."""
    raise NotImplementedError


def test_typed_template_values() -> None:
    """A whole-string `{{ bool_var }}` renders to a native bool, not a string."""
    raise NotImplementedError


def test_fragment_name_mismatch_fails() -> None:
    """A fragment whose name != its path raises FragmentError."""
    raise NotImplementedError


def test_provenance_recording() -> None:
    """Provenance records the last fragment/operation to touch each path."""
    raise NotImplementedError


def test_set_override_emits_warning() -> None:
    """`set` over an existing value records an override warning."""
    raise NotImplementedError
