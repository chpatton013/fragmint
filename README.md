# yaml-frag — YAML Fragment Composer

Render structured YAML documents from an inventory of **targets**, an ordered
sequence of reusable **fragments**, per-target **variables**, and **explicit
path-based merge operations**.

`yaml-frag` is generic: it has no built-in knowledge of any particular document
schema. Domain-specific behavior lives entirely in *project configuration,
schemas, validators, and fragments*. The repository ships a complete example —
**Ubuntu 24.04 Server autoinstall** — built this way; nothing about autoinstall
is hard-coded in the renderer.

The full design specification is in [`PLAN.md`](PLAN.md). This README is the
operator-facing summary.

> Status: skeleton. Module signatures, data files, schemas, and the CLI surface
> are in place; the logic in `src/yaml_frag/` is stubbed with
> `NotImplementedError` and documented against `PLAN.md`. The test suite is
> stubbed and skipped. See "Implementation status" below.

## Installation

Requires Python 3.12+.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
```

This installs the `yaml-frag` command.

## Repository structure

```
yaml-frag.yaml   Project configuration (input locations, output, validators).
inventory/       Target/group/default definitions (targets.yaml).
fragments/       Reusable, composable fragments under any nested layout.
schemas/         JSON schemas for inventory, fragment, and project files.
templates/       Output templates (e.g. the #cloud-config wrapper).
rendered/        Output, per the configured path pattern (git-ignored).
src/yaml_frag/   The package (see "Modules" below).
tests/           Unit, inventory, config, snapshot, and CLI tests + fixtures.
```

### Modules

| Module          | Responsibility |
|-----------------|----------------|
| `models.py`     | Immutable value objects and shared type aliases. |
| `errors.py`     | Exception hierarchy; each maps to an exit code. |
| `exit_codes.py` | Stable process exit codes. |
| `config.py`     | Load the project config; resolve output paths and validators. |
| `yamlio.py`     | Safe YAML load + deterministic serialization. |
| `pointer.py`    | JSON Pointer parse/get/set/delete (no array indexes). |
| `templating.py` | Strict, sandboxed, type-preserving Jinja substitution. |
| `inventory.py`  | Load/validate inventory; resolve a target's fragments + vars. |
| `fragments.py`  | Resolve/load/validate fragment files. |
| `merge.py`      | The explicit merge operations (incl. `assert`). |
| `provenance.py` | Track which fragment/op last set each path; override warnings. |
| `validation.py` | Generic doc checks (marker/schema) + external validators. |
| `render.py`     | Orchestration: render, serialize, output template, atomic write. |
| `cli.py`        | `click` command surface and error→exit-code mapping. |

## Project configuration

`yaml-frag.yaml` adapts the generic renderer to a use case. It declares default
input locations, how output is written (path pattern + optional text template +
optional document schema + default validators), and named validators:

```yaml
version: 1
inventory: inventory/targets.yaml
fragments_dir: fragments
output:
  path: "rendered/{target}/user-data"
  template: templates/user-data.tmpl   # injects the serialized YAML, adds #cloud-config
validators:
  subiquity:
    command: [python3, tools/validate-autoinstall-user-data.py]
```

The output template contains the literal token `{{ document }}`, which is
replaced by the serialized YAML — that is how the `#cloud-config` header is
prepended without the renderer knowing anything about cloud-init.

## Authoring inventory

`inventory/targets.yaml` declares `defaults`, `groups`, and `targets`. A target
picks ordered groups and fragments and supplies variables:

```yaml
targets:
  gb10-01:
    groups: [gb10, general_servers]
    fragments: [hosts/gb10-01, autoinstall/checks]
    variables:
      identity_hostname: gb10-01
      primary_interface: enP7s7
```

- **Fragment order**: `defaults` → each group's fragments (in the target's group
  order) → target fragments. Precedence is purely positional; the renderer never
  reorders.
- **Variable precedence** (later wins): defaults → group vars (in order) → target
  vars → `--var` CLI overrides.

### Variable value sources

A variable's value is a **literal** (default), a **secret** reference, or the
captured **stdout of a subprocess**. Sources nest — a capture's args and stdin
can be literals or secrets:

```yaml
identity_password_hash:                     # run openssl over a secret password
  from: capture
  command: [openssl, passwd, -6, -stdin]
  stdin: { from: secret, name: gb10-01_password }
```

Secrets come from a flat named store passed with `--secrets` (git-ignored; see
`inventory/secrets.example.yaml`):

```yaml
secrets:
  gb10-01_password: "..."
```

Captures run only when rendering (never during `inspect`/`explain`), use an argv
list (no shell), and are treated as sensitive — secret/capture variables are
redacted regardless of name. Captures can be non-deterministic (e.g. `openssl
passwd -6` uses a random salt).

## Authoring fragments

A fragment declares metadata, optional required variables, and an ordered list
of explicit operations. Fragments may live under any nested path and are
referenced by that path (minus `.yaml`):

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
Operations: `set`, `merge`, `append`, `prepend`, `remove`, `remove-list-items`,
`assert`. Lists are never merged implicitly — use `append`/`prepend`/`set`.
Values support strict `{{ var }}` substitution (missing variables are a hard
error).

**Document-specific validation is expressed as `assert` operations in
fragments**, not in the renderer. See `fragments/autoinstall/checks.yaml` for the
autoinstall structural checks, and the `assert` ops inside `hardware/*` and
`roles/*`.

## CLI usage

```bash
yaml-frag render gb10-01                 # -> configured output path
yaml-frag render gb10-01 --stdout        # print output to stdout only
yaml-frag render gb10-01 --validator subiquity
yaml-frag render-all                     # render every target
yaml-frag validate gb10-01               # render in memory + validate
yaml-frag explain gb10-01 [/path]        # where did each value come from?
yaml-frag inspect gb10-01                # resolved groups/fragments/vars (redacted)
yaml-frag list targets|fragments|groups
```

Common options: `--config`, `--inventory`, `--fragments-dir`, `--output`,
`--secrets`, `--var KEY=VALUE`, `--validator NAME`, `--dry-run`,
`--quiet-overrides`, `--no-validate`. Rendered output goes to stdout only with
`--stdout`; all diagnostics go to stderr.

### Exit codes

`0` success · `1` render failure · `2` usage · `3` inventory validation ·
`4` fragment validation · `5` merge conflict · `6` rendered-document validation
(generic check, schema, assertion, or validator) · `7` project-config error ·
`8` variable-resolution failure (secret not found / capture failed).

## Development

```bash
pytest            # tests (currently skipped stubs)
mypy src          # type checking (strict)
ruff check .      # lint
```

## Implementation status

Implement the `NotImplementedError` bodies in `src/yaml_frag/`, then remove the
`pytestmark` skip in each `tests/test_*.py` and fill in the test bodies.
`PLAN.md` is authoritative for all behavior; every stub docstring points to the
relevant section. Acceptance criteria are in `PLAN.md` "Acceptance criteria".
Snapshot fixtures under `tests/fixtures/expected/` are generated from the
working renderer and reviewed before committing (see that directory's README).
