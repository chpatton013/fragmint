# Test fixtures

Committed inputs and expected outputs for the test suite.

## Snapshot expectations

Expected rendered files live under:

```
tests/fixtures/expected/<machine>/user-data
```

for the machines in `tests/test_render.py::SNAPSHOT_MACHINES`
(`generic-vm-01`, `gb10-01`, `gb10-02`). See PLAN.md "Snapshot tests".

To (re)generate after the renderer works:

```bash
autoinstall-render render <machine> --output tests/fixtures/expected
```

Review the diff before committing — the snapshot is the reviewed contract.

## Malformed inputs

Put intentionally-broken inventories/fragments used by negative tests under
`tests/fixtures/invalid/` (e.g. name mismatch, unknown group, bad op).
