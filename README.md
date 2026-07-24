# yaml-frag — YAML Fragment Composer

Render structured YAML documents from an inventory of **targets**, an ordered
sequence of reusable **fragments**, per-target **variables**, and **explicit
path-based merge operations**.

`yaml-frag` is generic: it has no built-in knowledge of any particular document
schema. Domain-specific behavior lives entirely in *project configuration,
schemas, validators, and fragments*. The repository ships a complete example —
**Ubuntu 24.04 Server autoinstall** — built this way; nothing about autoinstall
is hard-coded in the renderer.

This README is the authoritative reference for the tool's design and usage.

## Design principles

- **Ordered composition.** Each target specifies (directly or via reusable
  groups) an ordered list of fragments, applied first to last. Later fragments
  may override or extend values produced by earlier ones. Order is the *only*
  thing that determines precedence — the renderer never reorders fragments,
  not alphabetically, not by path, not by category convention. The result is
  deterministic.
- **Explicit merge behavior.** Fragments specify *how* each field should be
  applied (replace, merge, append, prepend, remove, assert) rather than
  relying on an implicit recursive deep-merge. Default behavior is
  conservative and predictable.
- **Separation of data and rendering logic.** The renderer contains no
  hard-coded knowledge of any specific document type — no autoinstall keys,
  host names, hardware models, usernames, SSH identities, package lists,
  network interfaces, or required-field rules. Everything domain-specific
  belongs in the inventory, fragments, project configuration, and schemas. The
  renderer's only generic knowledge is: YAML parsing and deterministic
  serialization; path-based merge operations; template variable substitution;
  provenance tracking; generic structural validation (unresolved-marker
  detection, optional user-supplied schema, user-defined assertions,
  user-defined validators); output templating and file writing; and
  command-line behavior.
- **Fail closed.** Invalid or ambiguous configuration stops rendering with a
  clear error: a referenced fragment doesn't exist, a template variable is
  missing, two fragments produce incompatible types, a list is implicitly
  replaced without an explicit operation, a removal targets a missing path
  under strict mode, a fragment assertion fails, the rendered configuration
  contains unresolved template expressions, or a configured validator reports
  failure. Assertions such as "the document must contain
  `autoinstall.version: 1`" are **not** built in — they're declared by the
  project's fragments (see `example/fragments/autoinstall/checks.yaml`).
- **Human-readable source files.** Inventory, fragment, and project-config
  files are meant to be easy to review in Git. Avoid embedding large amounts
  of Python or arbitrary executable logic in YAML; use Jinja-style variable
  substitution for values, but keep control flow minimal.
- **Implementation preference.** Favor a small, unsurprising implementation
  over a highly abstract framework. The most important properties are:
  deterministic ordering; explicit list behavior; strict validation; good
  diagnostics; inspectable provenance; a renderer with no domain knowledge;
  straightforward Git review. Avoid "smart" merge behavior that guesses
  intent.

## Installation

Requires Python 3.12+.

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
```

This installs the `yaml-frag` command.

## Repository structure

`yaml-frag` the tool (`src/yaml_frag/`, `schemas/`) is separate from any
particular project that uses it. A **project** is just a directory containing
a `yaml-frag.yaml` plus whatever inventory, fragments, and templates it needs —
nothing about its internal layout is baked into the tool, and it can be
invoked from anywhere via `--config path/to/that/yaml-frag.yaml`. Every path a
project declares in its config is resolved relative to *that config file's own
directory*, not the caller's working directory (see "Project configuration"
below) — that's what makes the project directory portable and relocatable.

This repository ships one such project, the Ubuntu autoinstall example, under
`example/`:

```
example/
├── yaml-frag.yaml   Project configuration (input locations, output, validators).
├── inventory/       Target/group/default definitions (targets.yaml) + secrets.example.yaml.
├── fragments/       Reusable, composable fragments under any nested layout.
├── templates/       Output templates (e.g. the #cloud-config wrapper).
└── rendered/        Output, per the configured path pattern (git-ignored).
```

`fragments/` in particular is entirely up to the project: fragments may live
under any nested path and are referenced by their path relative to the
fragments directory. The tool itself lives alongside it:

```
schemas/         JSON schemas for inventory, fragment, project, and secrets files.
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
| `sources.py`    | Variable value sources: literal, secret, subprocess capture. |
| `provenance.py` | Track which fragment/op last set each path; override warnings. |
| `validation.py` | Generic doc checks (marker/schema) + external validators. |
| `render.py`     | Orchestration: render, serialize, output template, atomic write. |
| `cli.py`        | `click` command surface and error→exit-code mapping. |

Custom exception types (`errors.py`), each mapped to a stable exit code:

```text
ConfigError
InventoryError
FragmentError
TemplateRenderError
MergeConflictError
AssertionFailedError
ValidationError
VariableResolutionError   (base for SecretNotFoundError, CaptureError)
UnknownTargetError
UnknownFragmentError
```

Every user-facing error includes enough context to find the source file and
operation — but never the value of a secret or a secret-sourced argument.

## Project configuration

`yaml-frag.yaml` (default name, overridable with `--config`) adapts the
generic renderer to a use case. It declares default input locations, how
output is written (path pattern + optional text template + optional document
schema + default validators), and named validators:

```yaml
version: 1

# Default input locations (CLI flags override these).
inventory: inventory/targets.yaml
fragments_dir: fragments

# How each target's rendered document is written.
output:
  # Destination path pattern. "{target}" is substituted with the target name.
  # Must contain "{target}" when rendering more than one target.
  path: "rendered/{target}/user-data"
  # Optional text template. The serialized YAML replaces the literal token
  # "{{ document }}" in this file. If omitted, the serialized YAML is written
  # verbatim. This is how the autoinstall project adds the "#cloud-config"
  # header — see templates/user-data.tmpl.
  template: templates/user-data.tmpl
  # Optional JSON schema the rendered document is validated against.
  schema: null
  # Named validators (see below) run by default for this output.
  validators: []

# Named validators, selected with --validator NAME. Each runs an external
# command against the rendered output file; a nonzero exit is a failure.
validators:
  subiquity:
    command: [python3, tools/validate-autoinstall-user-data.py]
```

Everything domain-specific (the `#cloud-config` header, the Subiquity
validator, any document schema) lives here or in fragments — never in the
renderer.

**Portability.** Every path above (`inventory`, `fragments_dir`,
`output.path`, `output.template`, `output.schema`) is resolved **relative to
the directory containing this config file**, never the caller's current
working directory. An already-absolute path is left unchanged. This is what
lets a project directory — like `example/` in this repository — be self-
contained and relocatable: it works identically whether you run
`yaml-frag render gb10-01 --config example/yaml-frag.yaml` from the repo root,
`yaml-frag render gb10-01 --config yaml-frag.yaml` from inside `example/`, or
copy `example/` somewhere else entirely and invoke it from there. `--inventory`
and `--fragments-dir` CLI overrides are the exception: given directly on the
command line, they resolve relative to the CWD like any ordinary CLI argument.

## Authoring inventory

`inventory/targets.yaml` (paths below are relative to the project directory,
e.g. `example/inventory/targets.yaml`) declares `defaults`, `groups`, and
`targets`:

```yaml
version: 1

defaults:
  variables:
    identity_username: chris
    ssh_import_id: gh:chpatton013
    locale: en_US.UTF-8
    keyboard_layout: us
  fragments:
    - autoinstall/base
    - autoinstall/default-user
    - ubuntu-24.04

groups:
  gb10:
    variables:
      hardware_model: gb10
    fragments:
      - hardware/gb10
  general_servers:
    fragments:
      - roles/general-server

targets:
  gb10-01:
    groups:
      - gb10
      - general_servers
    fragments:
      - hosts/gb10-01
      - autoinstall/checks
    variables:
      identity_hostname: gb10-01
      identity_password_hash: "$6$example-salt$example-hash"
      primary_interface: enP7s7
```

A **target** is any named thing you want to render a document for (a machine,
an environment, a service — the renderer does not care). Groups are an
optional, generic reuse mechanism: a named bundle of variables and fragments a
target can pull in.

### Fragment order

The final ordered fragment list for a target is the concatenation of:

1. inventory `defaults.fragments`;
2. for each group the target lists, that group's fragments, in the target's
   group order;
3. the target's own `fragments`.

Precedence is purely positional: entries later in this resolved list override
earlier ones. The renderer never reorders. For the example above, the
resolved order is:

```text
autoinstall/base
autoinstall/default-user
ubuntu-24.04
hardware/gb10
roles/general-server
hosts/gb10-01
autoinstall/checks
```

### Variable precedence

Variable *definitions* are layered in this order (later wins):

1. inventory `defaults.variables`;
2. group variables, in the target's group order;
3. target variables;
4. CLI overrides (`--var KEY=VALUE`, always literal strings).

```bash
yaml-frag render gb10-01 --var identity_hostname=test-gb10
```

A later layer's definition fully replaces an earlier one — including
replacing a literal with a source or vice versa. Layering happens first; a
definition may be a literal or a `from:` source (see "Variable value sources"
below). Resolution of sources happens once, *after* layering and *before*
templating. Secrets are not a precedence layer; they are a named store
referenced explicitly.

## Variable value sources

A variable's value is one of three sources, distinguished by an optional
`from:` key. A mapping is a source only when its `from` value is one of
`literal`, `secret`, or `capture`; anything else (scalar, list, or mapping
without that discriminator) is a literal.

```yaml
keyboard_layout: us                       # literal (unchanged)

identity_password:                        # secret reference
  from: secret
  name: gb10-01_password

identity_password_hash:                   # subprocess capture
  from: capture
  command: [openssl, passwd, -6, -stdin]  # each element may itself be a source
  stdin: { from: secret, name: gb10-01_password }
  trim: true                              # strip one trailing newline (default)

weird_literal:                            # escape hatch: a literal mapping that
  from: literal                           #   itself contains a `from` key
  value: { from: "us-east-1" }
```

- **literal** — used verbatim. The default for untagged values; the tagged
  form `{from: literal, value: ...}` exists only as an escape hatch.
- **secret** — `{from: secret, name: NAME}` looks `NAME` up in the secret
  store (see "Secrets" below). A missing name fails closed
  (`SecretNotFoundError`).
- **capture** — `{from: capture, command: [...], stdin: <source?>, trim: bool}`
  runs a subprocess and uses its stdout. Sources **nest**: each `command`
  element and the optional `stdin` are themselves sources (literal or
  secret), so arguments and stdin can come from literals or secrets.

Variables do not reference other variables in this version (a possible future
extension).

### Security

- Captures run with an argv list and `shell=False` — never a shell string, so
  there is no shell-injection surface. `command` must be a list.
- A bounded timeout applies; a nonzero exit, timeout, or missing program
  raises `CaptureError` with the command name and stderr, and secret-sourced
  arguments redacted.
- Resolved secret values and secret-sourced arguments/stdin are never logged.
- A variable defined via `secret` or `capture` is treated as sensitive and
  redacted in `inspect`/`explain` regardless of its name.

### Determinism

Captures make output environment-dependent, and some (e.g. `openssl passwd
-6`, which uses a random salt) are non-deterministic. For reproducible
snapshot tests, inject a deterministic `CommandRunner` stub (see
`tests/conftest.py`'s `stub_runner` fixture); real renders of such variables
will differ run to run by design.

### Resolution timing

Captures execute only when a document is actually rendered (`render`,
`render-all`, and the in-memory render behind `validate`). `inspect` and
`explain` do **not** execute captures or reveal secrets — they show a
redacted description such as `<capture: openssl passwd -6 -stdin>` or
`<secret gb10-01_password>`.

## Secrets

Secrets live in an optional, untracked file passed with `--secrets`. It is a
flat named store referenced by `from: secret` sources — not a precedence
layer.

```bash
yaml-frag render gb10-01 --config example/yaml-frag.yaml --secrets example/inventory/secrets.yaml
```

```yaml
secrets:
  gb10-01_password: "correct horse battery staple"
```

`--secrets`, like other CLI-supplied paths, resolves relative to the CWD (not
the project directory). A tracked `example/inventory/secrets.example.yaml`
documents the shape; `example/inventory/secrets.yaml` is git-ignored. The
renderer never logs secret values or writes them to diagnostics; they appear
only where a fragment places a resolved value into the output document.

## Authoring fragments

A fragment declares metadata, optional required variables, and an ordered
list of explicit operations. Fragments may live under any nested path and are
referenced by that path (minus `.yaml`):

```yaml
fragment:
  version: 1
  name: autoinstall/default-user   # must match the file path minus .yaml
  description: Configure the default administrative user and SSH access.

requires:
  variables:
    - identity_hostname
    - identity_username
    - identity_password_hash
    - ssh_import_id

operations:
  - op: merge
    path: /autoinstall
    value:
      identity:
        hostname: "{{ identity_hostname }}"
        username: "{{ identity_username }}"
        password: "{{ identity_password_hash }}"
      ssh:
        install-server: true
        allow-pw: false
        import-id:
          - "{{ ssh_import_id }}"
```

The renderer resolves a fragment reference like `hardware/gb10` to
`<fragments_dir>/hardware/gb10.yaml`; the fragment file must declare a
matching `fragment.name`. A mismatch is an error.

### Paths

Operation paths are JSON Pointers:

```text
/                       the root document
/a/b/c                  nested mapping keys
```

- `/` represents the root document; `/a/b/c` addresses nested mapping keys.
- Array indexes are not supported (intentionally — it discourages brittle
  fragments).
- Escaping follows JSON Pointer rules: `~1` → `/`, `~0` → `~`.

### Merge operations

Every operation is explicit — the renderer never guesses whether a value
should be replaced, merged, or extended.

**`set`** — replace the value at a path, creating missing parent mappings.
If a value already exists, it's replaced completely (an override warning is
emitted; see "Conflict reporting"). Use this for explicit scalar, list, or
object replacement.

```yaml
- op: set
  path: /autoinstall/kernel
  value: { package: linux-generic-hwe-24.04 }
```

**`merge`** — recursively merge one mapping into another mapping. Both the
existing target and incoming value must be mappings; nested mappings merge
recursively; scalar values are replaced. Lists are **not** implicitly
merged — if a list already exists at a path and the incoming mapping
contains a list at the same path, the operation fails unless the list value
is identical. Fragments that need to modify lists must use `append`,
`prepend`, `set`, or a removal operation explicitly.

```yaml
- op: merge
  path: /autoinstall
  value: { keyboard: { layout: us }, locale: en_US.UTF-8 }
```

**`append`** / **`prepend`** — add one or more values to a list, creating the
list if it doesn't exist. Fails if the existing value is not a list. Order is
preserved; no deduplication by default. `prepend` preserves the order of the
incoming values (they end up first, in the order given).

```yaml
- op: append
  path: /autoinstall/user-data/packages
  value: [curl, ca-certificates]
```

Optional field `deduplicate: true` removes duplicates while preserving the
first occurrence; for mappings inside lists, equality is structural.

**`remove`** — remove a complete field. Removing a missing field is an error
unless `missing_ok: true`.

```yaml
- op: remove
  path: /autoinstall/oem
  missing_ok: true
```

**`remove-list-items`** — remove selected values from a list. Fails if the
target is not a list, or if none of the requested values were found (unless
`missing_ok: true`). Removes *all* structurally equal matches.

```yaml
- op: remove-list-items
  path: /autoinstall/user-data/packages
  value: [vim-tiny]
```

**`assert`** — verify a condition without modifying the document. This is
the primary mechanism for document-specific validation (see "Validation"
below); document-specific structural requirements belong here, in fragments,
not in the renderer.

```yaml
- op: assert
  path: /autoinstall/version
  equals: 1
```

Supported assertion forms: `equals: <value>`, `exists: true|false`, and
`type: mapping|list|string|integer|boolean`. Assertions fail closed with a
message naming the target, fragment, operation index, path, and the
expectation.

### Merge examples

Appending packages from two fragments:

```yaml
# earlier fragment
- op: append
  path: /autoinstall/user-data/packages
  value: [curl, ca-certificates]
# later fragment
- op: append
  path: /autoinstall/user-data/packages
  deduplicate: true
  value: [curl, qemu-guest-agent]
```

Result: `[curl, ca-certificates, qemu-guest-agent]`.

Replacing a scalar object (earlier: `set /autoinstall/kernel ->
{package: linux-generic}`; later: `set /autoinstall/kernel -> {package:
linux-generic-hwe-24.04}`) results in `{package: linux-generic-hwe-24.04}`
plus an override warning.

Two `append` operations to `/autoinstall/user-data/write_files` concatenate
the entries — the renderer never merges list entries by a key field; to
replace the whole list, use `set`.

### Template rendering

Fragment values support strict Jinja-style substitution:

```yaml
hostname: "{{ identity_hostname }}"
```

Undefined-variable behavior is strict — a missing variable produces an error
naming the target, fragment, operation index, and missing variable name (it
never silently substitutes an empty string). Templates may appear in mapping
values, list values, multiline strings, operation paths, and assertion
values; they must not dynamically create new YAML structure by returning YAML
text — rendering occurs on already-parsed scalar strings.

Variables remain typed when the entire scalar is a single template
expression. For example, with `enable_package_upgrade: false`:

```yaml
package_upgrade: "{{ enable_package_upgrade }}"
```

yields Boolean `false`, not string `"False"`.

Templates run in a sandboxed environment: no arbitrary Python execution, no
access to the filesystem, environment, subprocess, or Python object
internals. Only a small set of safe filters is exposed: `default`, `lower`,
`upper`, `replace`, `join`, `tojson`.

## Rendering algorithm

For each requested target:

1. Load the project configuration.
2. Load and validate the inventory.
3. Resolve defaults, groups, and the target definition.
4. Build the ordered variable map, then resolve variable value sources
   (secrets/captures).
5. Build the ordered fragment list.
6. Load every referenced fragment and validate it against the fragment
   schema.
7. Start with an empty document.
8. For each fragment, in order: verify required variables; render templates
   using the resolved variable map; apply operations in listed order,
   recording provenance for every changed path; evaluate `assert` operations
   as they're encountered.
9. Run generic structural validation (unresolved-marker check; optional
   project-supplied document schema).
10. Serialize deterministic YAML.
11. If the output has a template, inject the serialized YAML into it
    (replacing `{{ document }}`); otherwise use the serialized YAML directly.
12. Write the result to the configured output path (`{target}` substituted),
    using a temporary file and atomic rename.
13. Run any selected validators against the written output.

The renderer creates exactly the file(s) the project configures — it never
forces companion files like a NoCloud `meta-data`.

## Provenance and `explain`

The renderer tracks which fragment and operation last modified each path:

```bash
yaml-frag explain gb10-01
yaml-frag explain gb10-01 /autoinstall/storage
```

```text
/autoinstall/kernel/package
  value: linux-generic-hwe-24.04
  source: hardware/gb10 operation 0

/autoinstall/user-data/packages
  contributors:
    - roles/general-server operation 0
```

## Conflict reporting

When a `set` replaces an existing value, a warning is emitted by default
(suppress with `--quiet-overrides`):

```text
warning: hardware/gb10 operation 0 replaced /autoinstall/kernel
  previous source: ubuntu-24.04
  new source: hardware/gb10
```

A `merge` encountering an incompatible type fails instead:

```text
cannot merge mapping into list at /autoinstall/user-data/packages
  existing value from: roles/general-server
  incoming value from: hosts/gb10-01
```

## Validation

There are four clearly separable validation concerns. Only the first two are
built into the renderer; the last two are supplied by the project.

1. **Input validation (built in).** Inventory is validated against
   `schemas/inventory.schema.json` plus structural rules (inventory version;
   unique target/group names; referenced groups exist; fragments are lists of
   strings; variables are mappings; order preserved). Fragments are validated
   against `schemas/fragment.schema.json` plus rules (supported fragment
   version; name matches path; recognized `op`; valid path; op-appropriate
   fields present; assertions use supported forms). Project configuration is
   validated against `schemas/project.schema.json`; secrets against
   `schemas/secrets.schema.json`.
2. **Generic rendered-document validation (built in).** Only document-agnostic
   checks: reject unresolved template markers (text containing `{{` or `{%`);
   if `output.schema` is set, validate the rendered document against that
   JSON schema. The renderer contains **no** hard-coded structural
   expectations (no `autoinstall.version`, no required `identity`/`ssh`/
   `storage`/`network` rules).
3. **Document assertions (project supplied, via fragments).** Any
   document-specific structural requirement is expressed as `assert`
   operations in fragments. The example project ships an
   `autoinstall/checks` fragment asserting the autoinstall structure, and
   per-hardware fragments assert their own additions (e.g.
   `network.version == 2`). Assertions run as part of rendering and fail
   closed.
4. **Named validators (project supplied, external).** Projects declare named
   validators in the project config; select them with a repeatable
   `--validator NAME` (if none given, the output's default validators run).
   Each runs an external command against the written output file and fails
   on nonzero exit.

```bash
yaml-frag validate gb10-01 --validator subiquity
```

## YAML serialization

Output is stable and diff-friendly:

- preserve mapping insertion order (never sort keys);
- two-space indentation;
- never emit Python-specific YAML tags;
- Booleans as `true`/`false`;
- quote strings only when necessary;
- prefer block style for multiline strings;
- end files with exactly one newline.

Any header such as `#cloud-config` comes from the output template, not the
serializer.

## CLI usage

```bash
yaml-frag render gb10-01                 # -> configured output path
yaml-frag render gb10-01 --stdout        # print output to stdout only
yaml-frag render gb10-01 --validator subiquity
yaml-frag render-all                     # render every target
yaml-frag validate gb10-01               # render in memory + validate
yaml-frag validate-all
yaml-frag explain gb10-01 [/path]        # where did each value come from?
yaml-frag inspect gb10-01                # resolved groups/fragments/vars (redacted)
yaml-frag list targets|fragments|groups
```

All of the above assume `--config example/yaml-frag.yaml` (or that you've `cd`'d
into `example/` and use the default). Every command accepts `--config PATH`
(default `yaml-frag.yaml`). Common
options: `--inventory`, `--fragments-dir`, `--output` (render only),
`--secrets`, `--var KEY=VALUE`, `--validator NAME`, `--dry-run`,
`--quiet-overrides`, `--no-validate`. Rendered output goes to stdout only
with `--stdout`; all diagnostics go to stderr. `render-all`/`validate-all`
process every target in inventory order and exit nonzero if any target
fails, without leaving a partially written output file for a failed target
(atomic temp-file + rename).

`inspect` shows resolved groups, fragment order, and variables, redacting any
variable whose name contains `password`, `secret`, `token`, `private`, or
`credential`, and any variable defined via a `secret` or `capture` source
regardless of name (shown as a non-executing description, e.g. `<capture:
openssl passwd -6 -stdin>`). Pass `--show-secrets` to reveal literal values
(captures are still never executed for inspection).

### Exit codes

`0` success · `1` render failure · `2` usage · `3` inventory validation ·
`4` fragment validation · `5` merge conflict · `6` rendered-document validation
(generic check, schema, assertion, or validator) · `7` project-config error ·
`8` variable-resolution failure (secret not found / capture failed).

## Example project: Ubuntu autoinstall

The repository ships a complete example that renders Ubuntu 24.04 autoinstall
`user-data`, demonstrating that all autoinstall specifics live in data:

- `example/yaml-frag.yaml` sets `output.template:
  templates/user-data.tmpl` (which adds the `#cloud-config` header) and
  `output.path: rendered/{target}/user-data`, and declares the optional
  `subiquity` validator.
- `example/fragments/autoinstall/base.yaml`, `default-user.yaml`, and
  `checks.yaml`, `example/fragments/ubuntu-24.04.yaml`,
  `example/fragments/hardware/*`, `example/fragments/roles/*`, and
  `example/fragments/hosts/*` build and assert the document.
- Each target's `identity_password_hash` is a `capture` source that runs
  `openssl passwd -6` over the plaintext password held in the secret store
  (`example/inventory/secrets.example.yaml` shows the shape).
- No autoinstall knowledge exists in `src/yaml_frag/`.

Expected render for target `gb10-01` (the password hash varies run to run —
`openssl passwd -6` uses a random salt; the committed test snapshots pin it
via a stubbed `CommandRunner`):

```yaml
#cloud-config
autoinstall:
  version: 1
  kernel:
    package: linux-generic-hwe-24.04
  source:
    id: ubuntu-server-minimal
    search_drivers: false
  keyboard:
    layout: us
  locale: en_US.UTF-8
  identity:
    hostname: gb10-01
    username: chris
    password: "$6$...$..."
  storage:
    layout:
      name: lvm
      sizing-policy: all
  network:
    version: 2
    ethernets:
      primary:
        match:
          name: enP7s7
        dhcp4: true
        dhcp6: false
  ssh:
    install-server: true
    allow-pw: false
    import-id:
      - gh:chpatton013
  user-data:
    package_update: true
    package_upgrade: false
    packages:
      - bind9-dnsutils
      - ca-certificates
      - curl
      - iputils-ping
      - vim-tiny
    write_files:
      - path: /etc/sudoers.d/90-chris-nopasswd
        owner: root:root
        permissions: "0440"
        content: |
          chris ALL=(ALL:ALL) NOPASSWD:ALL
    runcmd:
      - >-
        visudo --check
        --file="/etc/sudoers.d/90-chris-nopasswd"
```

## Non-goals

Deliberately not implemented: a web interface; dynamic Python plugins;
arbitrary template code execution; array-index mutation; semantic merging of
list entries; key-aware list merges; multiple output files per target;
variables that reference other variables; automatic hardware discovery;
PXE/TFTP/DHCP configuration; deployment to HTTP servers; secret-manager
integration (the secret store is a plain file); reimplementing any full
document schema (e.g. Subiquity); running Ansible; installing Ubuntu. The
renderer's job is to produce correct, inspectable YAML artifacts.

Room for future extension: multiple named outputs per target; per-target
metadata files; PXE/iPXE script generation; publishing to an HTTP directory;
secret retrieval from a password manager; target enrollment states;
additional built-in validators; schema-aware validation for multiple document
families; encrypted outputs; fragment deprecation warnings; fragment
dependency declarations; optional fragment conditions.

## Development

```bash
pytest            # full test suite
mypy src          # type checking (strict)
ruff check .      # lint
```

Tests are organized by concern: `test_merge.py` (merge operations, pointer,
templating, provenance), `test_inventory.py` (precedence and ordering),
`test_sources.py` (variable value sources), `test_config.py` (project
config), `test_render.py` (end-to-end rendering + committed snapshot
fixtures under `tests/fixtures/expected/`), and `test_cli.py` (command
surface and exit codes). Capture tests use a stubbed `CommandRunner` — no
test should spawn a real subprocess.
