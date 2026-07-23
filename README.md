# Autoinstall Configuration Renderer

Render complete Ubuntu Server autoinstall configurations from an inventory of
machines, an ordered sequence of reusable configuration fragments, per-machine
variables, and **explicit** fragment merge instructions.

The full design specification is in [`PLAN.md`](PLAN.md). This README is the
operator-facing summary. The renderer favors deterministic ordering, explicit
list behavior, strict validation, good diagnostics, and inspectable
provenance over "smart" merge behavior.

> Status: skeleton. Module signatures, data files, schemas, and the CLI
> surface are in place; the business logic in `src/autoinstall_renderer/` is
> stubbed with `NotImplementedError` and documented against `PLAN.md`. The test
> suite is stubbed and skipped. See "Implementation status" below.

## Installation

Requires Python 3.12+.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
```

This installs the `autoinstall-render` command.

## Repository structure

```
inventory/     Machine/group/default definitions (machines.yaml).
fragments/     Reusable, composable config fragments (base/, releases/,
               hardware/, roles/, sites/, hosts/).
schemas/       JSON schemas for inventory and fragment files.
templates/     Optional static files referenced by fragments.
rendered/      Output: rendered/<machine>/{user-data,meta-data} (git-ignored).
src/autoinstall_renderer/
               The package (see "Modules" below).
tests/         Unit, inventory, snapshot, and CLI tests + fixtures.
```

### Modules

| Module          | Responsibility |
|-----------------|----------------|
| `models.py`     | Immutable value objects and shared type aliases. |
| `errors.py`     | Exception hierarchy; each maps to an exit code. |
| `exit_codes.py` | Stable process exit codes. |
| `yamlio.py`     | Safe YAML load + deterministic serialization. |
| `pointer.py`    | JSON Pointer parse/get/set/delete (no array indexes). |
| `templating.py` | Strict, sandboxed, type-preserving Jinja substitution. |
| `inventory.py`  | Load/validate inventory; resolve a machine's fragments + vars. |
| `fragments.py`  | Resolve/load/validate fragment files. |
| `merge.py`      | The explicit merge operations. |
| `provenance.py` | Track which fragment/op last set each path; override warnings. |
| `validation.py` | Rendered-document checks + optional Subiquity validation. |
| `render.py`     | Orchestration: render, serialize, atomic write, redaction. |
| `cli.py`        | `click` command surface and error→exit-code mapping. |

## Authoring inventory

`inventory/machines.yaml` declares `defaults`, `groups`, and `machines`. A
machine picks ordered groups and fragments and supplies variables:

```yaml
machines:
  gb10-01:
    groups: [gb10, general_servers]
    fragments: [hosts/gb10-01]
    variables:
      identity_hostname: gb10-01
      primary_interface: enP7s7
```

- **Fragment order**: `defaults` → each group's fragments (in the machine's
  group order) → machine fragments. Never reordered.
- **Variable precedence** (later wins): defaults → group vars (in order) →
  machine vars → secrets overlay → `--var` CLI overrides.
- **Secrets**: pass `--secrets inventory/secrets.yaml` (git-ignored) with
  `machines.<name>.<var>` entries. Never logged.

## Authoring fragments

A fragment declares metadata, optional required variables, and an ordered list
of explicit operations:

```yaml
fragment:
  version: 1
  name: hardware/gb10        # must match the file path minus .yaml
  description: ...
requires:
  variables: [primary_interface]
operations:
  - op: set
    path: /autoinstall/kernel
    value: { package: linux-generic-hwe-24.04 }
```

Paths are JSON Pointers (`/a/b`; `~1`→`/`, `~0`→`~`; no array indexes).
Operations: `set`, `merge`, `append`, `prepend`, `remove`,
`remove-list-items`, `assert`. Lists are never merged implicitly — use
`append`/`prepend`/`set` explicitly. Values support strict `{{ var }}`
substitution (missing variables are a hard error). See `PLAN.md` for the full
operation semantics and merge examples.

## CLI usage

```bash
autoinstall-render render gb10-01            # -> rendered/gb10-01/{user-data,meta-data}
autoinstall-render render gb10-01 --stdout   # print config to stdout only
autoinstall-render render-all                # render every machine
autoinstall-render validate gb10-01          # render in memory + validate
autoinstall-render validate gb10-01 --subiquity
autoinstall-render explain gb10-01 [/path]   # where did each value come from?
autoinstall-render inspect gb10-01           # resolved groups/fragments/vars (redacted)
autoinstall-render list machines|fragments|groups
```

Common options: `--inventory`, `--fragments-dir`, `--output`, `--secrets`,
`--var KEY=VALUE`, `--dry-run`, `--quiet-overrides`, `--no-validate`.
Rendered config goes to stdout only with `--stdout`; all diagnostics go to
stderr.

### Exit codes

`0` success · `1` render failure · `2` usage · `3` inventory validation ·
`4` fragment validation · `5` merge conflict · `6` rendered-document validation.

## Development

```bash
pytest            # tests (currently skipped stubs)
mypy src          # type checking (strict)
ruff check .      # lint
```

## Implementation status

Implement the `NotImplementedError` bodies in `src/autoinstall_renderer/`, then
remove the `pytestmark` skip in each `tests/test_*.py` and fill in the test
bodies. `PLAN.md` is authoritative for all behavior; every stub docstring
points to the relevant section. Acceptance criteria are listed in
`PLAN.md` "Acceptance criteria".
