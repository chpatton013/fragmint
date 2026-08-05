"""Shared pytest fixtures.

Fixtures point at the repo's real example project (the inventory at
``example/targets.yaml``, its imported modules under ``example/modules/``,
and their fragments) so tests can render the representative targets.
Snapshot expectations live under ``tests/fixtures/expected/`` (see
``tests/fixtures/README.md``).

:func:`write_tree` and the :func:`closure_from_tree` fixture build a
synthetic module/inventory tree under a temp directory and load it into a
:class:`~yaml_frag.modules.Closure` — the one place the "build a closure from
a temp tree" churn the redesign introduces lands, per the module model (see
README.md "The module model"), instead of being repeated across every test.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from yaml_frag import modules
from yaml_frag.modules import Closure

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLE_ROOT = REPO_ROOT / "example"


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture
def example_root() -> Path:
    return EXAMPLE_ROOT


@pytest.fixture
def inventory_path() -> Path:
    return EXAMPLE_ROOT / "targets.yaml"


@pytest.fixture
def fixtures_dir() -> Path:
    return Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def secrets_example_path() -> Path:
    """The tracked example secret store, usable directly by tests."""
    return EXAMPLE_ROOT / "secrets.example.yaml"


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


def write_tree(root: Path, files: dict[str, str]) -> None:
    """Write ``files`` (relative path -> text content) under ``root``,
    creating parent directories as needed. Used to build synthetic
    module/inventory trees in tests without a pile of ad hoc ``mkdir``/
    ``write_text`` calls at every call site."""
    for rel_path, content in files.items():
        path = root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


@pytest.fixture
def closure_from_tree(tmp_path: Path) -> Callable[[dict[str, str]], Closure]:
    """Build a synthetic module/inventory tree under ``tmp_path`` and load
    it into a :class:`~yaml_frag.modules.Closure`.

    ``files`` is relative path -> text content; the root document is expected
    at ``tmp_path / "targets.yaml"`` unless a different ``root`` relative path
    is given. See the module docstring.
    """

    def _build(files: dict[str, str], *, root: str = "targets.yaml") -> Closure:
        write_tree(tmp_path, files)
        return modules.load_closure(tmp_path / root)

    return _build


@pytest.fixture
def example_closure() -> Closure:
    """The real example project's import closure (inventory + its
    ``autoinstall``/``ansible`` modules)."""
    return modules.load_closure(EXAMPLE_ROOT / "targets.yaml")
