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
fragmint render <target> --output tests/fixtures/expected/<target>/user-data
```

Review the diff before committing — the snapshot is the reviewed contract.
These per-target snapshots and `explain-*.txt` are YAML/text and stay
byte-identical across format work; most TOML/JSON input and output cases are
covered by synthetic trees (the `closure_from_tree` fixture) in the test
suite instead, so this corpus stays a stable regression gate. The
`ansible-inventory.json` snapshot below is the exception: it exercises the
JSON output path through the shipped example itself, alongside a TOML input
fragment (`example/modules/ansible/fragments/ansible/checks.toml`), so the
example also guards the non-YAML paths end to end.

`tests/fixtures/expected/ansible-inventory.yaml` is the analogous snapshot for
the example project's `scope: aggregate` output (README.md "Aggregate
outputs"), composed once across every target rather than per target:

```bash
fragmint render-all --only ansible-inventory --output tests/fixtures/expected/ansible-inventory.yaml
```

`tests/fixtures/expected/ansible-inventory.json` is the same composed
document, serialized as JSON by the `ansible-inventory-json` output — the
two outputs share every fragment and differ only in `format`, so this
fixture is the regression guard for the JSON serialization path exercised
through the shipped example rather than a synthetic tree:

```bash
fragmint render-all --only ansible-inventory-json --output tests/fixtures/expected/ansible-inventory.json
```

`tests/fixtures/expected/explain-gb10-01.txt` and
`tests/fixtures/expected/explain-ansible-inventory.txt` are the analogous
snapshots for `explain`'s provenance report — the regression guard that proves
skipping `assert` during `explain`'s composition changes nothing for a
document whose assertions pass (README.md "Provenance and `explain`"):

```bash
fragmint explain gb10-01 --secrets example/secrets.example.yaml \
  --inventory example/targets.yaml > tests/fixtures/expected/explain-gb10-01.txt
fragmint explain --only ansible-inventory --secrets example/secrets.example.yaml \
  --inventory example/targets.yaml > tests/fixtures/expected/explain-ansible-inventory.txt
```

## Malformed inputs

Put intentionally-broken documents/fragments used by negative tests under
`tests/fixtures/invalid/` (e.g. fragment name mismatch, unknown group, bad
op, wrong-version document).
