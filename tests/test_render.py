"""Rendering and snapshot tests. Covers PLAN.md "Snapshot tests".

Snapshot fixtures live in tests/fixtures/expected/<target>/user-data. To
(re)generate them once the renderer works, render each target and write the
output there, then review the diff before committing.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.skip(reason="TODO: implement — see PLAN.md 'Snapshot tests'")

SNAPSHOT_TARGETS = ["generic-vm-01", "gb10-01", "gb10-02"]


@pytest.mark.parametrize("target", SNAPSHOT_TARGETS)
def test_snapshot_matches_expected(target: str) -> None:
    """Rendered output equals the committed expected fixture, byte for byte."""
    raise NotImplementedError


def test_gb10_matches_plan_expected_render() -> None:
    """gb10-01 renders the expected YAML in PLAN.md 'Complete expected render'
    (acceptance criterion 16)."""
    raise NotImplementedError


def test_output_template_prepends_cloud_config_header() -> None:
    """The project output template wraps the YAML with a `#cloud-config` header."""
    raise NotImplementedError


def test_gb10_hosts_differ_only_in_host_variables() -> None:
    """gb10-01 and gb10-02 differ only where host variables differ."""
    raise NotImplementedError


def test_fragment_assertion_failure_is_reported() -> None:
    """A failed fragment `assert` (e.g. autoinstall/checks) fails the render with
    context. Structural checks are data, not built-in renderer logic."""
    raise NotImplementedError


def test_generic_validation_rejects_unresolved_markers() -> None:
    """Generic validation fails on leftover `{{`/`{%` markers."""
    raise NotImplementedError


def test_deterministic_output() -> None:
    """Rendering the same target twice yields identical bytes."""
    raise NotImplementedError
