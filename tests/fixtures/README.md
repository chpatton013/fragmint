# Test fixtures

Committed inputs and expected outputs for the test suite.

## Snapshot expectations

Expected rendered files live under:

```
tests/fixtures/expected/<target>/user-data
```

for the targets in `tests/test_render.py::SNAPSHOT_TARGETS`
(`generic-vm-01`, `gb10-01`, `gb10-02`). See README.md "Development".

To (re)generate after the renderer works:

```bash
yaml-frag render <target> --output tests/fixtures/expected/<target>/user-data
```

Review the diff before committing — the snapshot is the reviewed contract.

`tests/fixtures/expected/ansible-inventory.yaml` is the analogous snapshot for
the example project's `scope: aggregate` output (README.md "Aggregate
outputs"), composed once across every target rather than per target:

```bash
yaml-frag render-all --only ansible-inventory --output tests/fixtures/expected/ansible-inventory.yaml
```

## Malformed inputs

Put intentionally-broken configs/inventories/fragments used by negative tests
under `tests/fixtures/invalid/` (e.g. fragment name mismatch, unknown group,
bad op, wrong-version project config).
