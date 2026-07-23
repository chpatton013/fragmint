"""Rendering and snapshot tests. Covers PLAN.md "Snapshot tests".

Snapshot fixtures live in tests/fixtures/expected/<machine>/user-data. To
(re)generate them once the renderer works, render each machine and write the
output there, then review the diff before committing.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.skip(reason="TODO: implement — see PLAN.md 'Snapshot tests'")

SNAPSHOT_MACHINES = ["generic-vm-01", "gb10-01", "gb10-02"]


@pytest.mark.parametrize("machine", SNAPSHOT_MACHINES)
def test_snapshot_matches_expected(machine: str) -> None:
    """Rendered user-data equals the committed expected fixture, byte for byte."""
    raise NotImplementedError


def test_gb10_matches_plan_expected_render() -> None:
    """gb10-01 renders the exact YAML in PLAN.md 'Complete expected render'
    (acceptance criterion 14)."""
    raise NotImplementedError


def test_output_starts_with_cloud_config_marker() -> None:
    """Rendered output begins with the `#cloud-config` line."""
    raise NotImplementedError


def test_gb10_hosts_differ_only_in_host_variables() -> None:
    """gb10-01 and gb10-02 differ only where host variables differ."""
    raise NotImplementedError


def test_rendered_validation_requires_autoinstall_version() -> None:
    """Validation fails when `autoinstall.version: 1` is absent."""
    raise NotImplementedError


def test_rendered_validation_rejects_unresolved_markers() -> None:
    """Validation fails on leftover `{{`/`{%` markers."""
    raise NotImplementedError


def test_deterministic_output() -> None:
    """Rendering the same machine twice yields identical bytes."""
    raise NotImplementedError
