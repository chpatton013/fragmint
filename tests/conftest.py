"""Shared pytest fixtures.

Fixtures point at the repo's real example project (config, inventory, and
fragments under ``example/``) so tests can render the representative targets.
Snapshot expectations live under ``tests/fixtures/expected/`` (see
``tests/fixtures/README.md``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_ROOT = REPO_ROOT / "example"


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture
def example_root() -> Path:
    return EXAMPLE_ROOT


@pytest.fixture
def config_path() -> Path:
    return EXAMPLE_ROOT / "yaml-frag.yaml"


@pytest.fixture
def inventory_path() -> Path:
    return EXAMPLE_ROOT / "inventory" / "targets.yaml"


@pytest.fixture
def fragments_dir() -> Path:
    return EXAMPLE_ROOT / "fragments"


@pytest.fixture
def fixtures_dir() -> Path:
    return Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def secrets_example_path() -> Path:
    """The tracked example secret store, usable directly by tests."""
    return EXAMPLE_ROOT / "inventory" / "secrets.example.yaml"


@pytest.fixture
def stub_runner():
    """A deterministic ``sources.CommandRunner`` stub for capture tests.

    Returns a fixed fake hash regardless of input so snapshots stay byte-stable
    and no real subprocess (e.g. ``openssl``) runs. Records the calls it saw for
    assertions.
    """

    class StubRunner:
        def __init__(self) -> None:
            self.calls: list[tuple[list[str], str | None]] = []

        def run(self, command, *, stdin, timeout):  # type: ignore[no-untyped-def]
            self.calls.append((list(command), stdin))
            return "$6$stubsalt$stubhash\n"

    return StubRunner()
