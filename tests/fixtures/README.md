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

`tests/fixtures/expected/explain-gb10-01.txt` and
`tests/fixtures/expected/explain-ansible-inventory.txt` are the analogous
snapshots for `explain`'s provenance report — the regression guard that proves
skipping `assert` during `explain`'s composition changes nothing for a
document whose assertions pass (README.md "Provenance and `explain`"):

```bash
yaml-frag explain gb10-01 --secrets example/secrets.example.yaml \
  --inventory example/targets.yaml > tests/fixtures/expected/explain-gb10-01.txt
yaml-frag explain --only ansible-inventory --secrets example/secrets.example.yaml \
  --inventory example/targets.yaml > tests/fixtures/expected/explain-ansible-inventory.txt
```

## Malformed inputs

Put intentionally-broken documents/fragments used by negative tests under
`tests/fixtures/invalid/` (e.g. fragment name mismatch, unknown group, bad
op, wrong-version document).
