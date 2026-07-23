"""Shared pytest fixtures.

Fixtures point at the repo's real ``inventory/`` and ``fragments/`` so tests
can render the representative machines. Snapshot expectations live under
``tests/fixtures/`` (see PLAN.md "Snapshot tests").
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture
def inventory_path() -> Path:
    return REPO_ROOT / "inventory" / "machines.yaml"


@pytest.fixture
def fragments_dir() -> Path:
    return REPO_ROOT / "fragments"


@pytest.fixture
def fixtures_dir() -> Path:
    return Path(__file__).resolve().parent / "fixtures"
