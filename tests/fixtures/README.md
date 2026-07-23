# Test fixtures

Committed inputs and expected outputs for the test suite.

## Snapshot expectations

Expected rendered files live under:

```
tests/fixtures/expected/<target>/user-data
```

for the targets in `tests/test_render.py::SNAPSHOT_TARGETS`
(`generic-vm-01`, `gb10-01`, `gb10-02`). See PLAN.md "Snapshot tests".

To (re)generate after the renderer works:

```bash
yaml-frag render <target> --output tests/fixtures/expected/<target>/user-data
```

Review the diff before committing — the snapshot is the reviewed contract.

## Malformed inputs

Put intentionally-broken configs/inventories/fragments used by negative tests
under `tests/fixtures/invalid/` (e.g. fragment name mismatch, unknown group,
bad op, wrong-version project config).
