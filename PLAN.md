# yaml-frag — YAML Fragment Composer

## Objective

Build a generic Python command-line tool that renders structured YAML documents
from:

1. an inventory of targets;
2. an ordered sequence of reusable configuration fragments;
3. per-target variables;
4. explicit path-based merge operations.

The tool composes arbitrary YAML documents deterministically. It contains **no
built-in knowledge of any particular document schema**. The first supported use
case is **Ubuntu Server autoinstall**, which is implemented entirely through
*project configuration, schemas, validators, and example fragments* — never
through hard-coded renderer behavior.

The design must make it easy to:

* define reusable base configurations;
* define reusable, composable fragments under any layout the user chooses;
* provide per-target variables;
* override configuration values deliberately;
* append items to selected lists;
* render one target or all targets;
* inspect which fragment supplied each value;
* validate rendered YAML before use, using project-supplied rules;
* wrap rendered YAML in an arbitrary output template (e.g. a `#cloud-config`
  header);
* avoid implicit or surprising merge behavior.

The renderer merges arbitrary YAML documents; any document-specific structure,
required fields, and validation come from the project, not the tool.

---

# Design principles

## Ordered composition

Each target specifies (directly or via reusable groups) an ordered list of
fragments.

Fragments are applied from first to last. Later fragments may override or extend
values produced by earlier fragments. **Order is the only thing that determines
precedence.** The renderer never reorders fragments — not alphabetically, not by
path, not by any category convention.

The result must be deterministic.

## Explicit merge behavior

Fragments must include enough metadata to tell the renderer how each field
should be applied. Do not rely on a recursive deep-merge algorithm alone.

A fragment must be able to specify operations such as:

* replace a scalar or object;
* merge a mapping;
* append values to a list;
* prepend values to a list;
* replace a list;
* remove a field;
* remove selected list entries;
* assert a condition.

The default behavior must be conservative and predictable.

## Separation of data and rendering logic

The renderer must not contain hard-coded knowledge about any specific document
type — no autoinstall keys, no host names, hardware models, usernames, SSH
identities, package lists, network interfaces, or required-field rules.

Everything domain-specific belongs in the inventory, fragments, project
configuration, and schemas.

The renderer may contain only generic knowledge about:

* YAML parsing and deterministic serialization;
* path-based merge operations;
* template variable substitution;
* provenance tracking;
* generic structural validation (unresolved-marker detection, optional
  user-supplied schema, user-defined assertions, user-defined validators);
* output templating and file writing;
* command-line behavior.

## Fail closed

Invalid or ambiguous configuration must stop rendering with a clear error.
Examples:

* a referenced fragment does not exist;
* a template variable is missing;
* two fragments produce incompatible types;
* a list is implicitly replaced without an explicit operation;
* a removal targets a missing path when strict;
* an assertion declared by a fragment fails;
* the rendered configuration contains unresolved template expressions;
* a configured validator reports failure.

Note: assertions such as "the document must contain `autoinstall.version: 1`"
are **not** built in. They are declared by the project's fragments (see
"Assertions" and the example project).

## Human-readable source files

Inventory, fragment, and project-config files must be easy to review in Git.
Avoid embedding large amounts of Python or arbitrary executable logic in YAML.
Use Jinja-style variable substitution for values, but keep control flow minimal.

---

# Suggested repository layout

The tool imposes no fragment directory structure. The layout below is a
*suggestion*; the `fragments/` subtree in particular is entirely up to the
project — fragments may live under any nested path and are referenced by their
path relative to the fragments directory.

```text
yaml-frag/
├── README.md
├── pyproject.toml
├── yaml-frag.yaml              # project configuration
├── inventory/
│   └── targets.yaml
├── fragments/                  # any nested layout the project likes
│   ├── autoinstall/
│   │   ├── base.yaml
│   │   ├── default-user.yaml
│   │   └── checks.yaml
│   ├── ubuntu-24.04.yaml
│   ├── hardware/
│   │   ├── gb10.yaml
│   │   └── generic-vm.yaml
│   ├── roles/
│   │   └── general-server.yaml
│   └── hosts/
│       └── gb10-01.yaml
├── schemas/
│   ├── inventory.schema.json
│   ├── fragment.schema.json
│   └── project.schema.json
├── templates/
│   └── user-data.tmpl          # output template (adds "#cloud-config")
├── rendered/
│   └── .gitkeep
├── tests/
│   ├── fixtures/
│   ├── test_merge.py
│   ├── test_render.py
│   ├── test_inventory.py
│   ├── test_config.py
│   └── test_cli.py
└── src/
    └── yaml_frag/
        ├── __init__.py
        ├── cli.py
        ├── config.py           # project configuration
        ├── inventory.py
        ├── fragments.py
        ├── merge.py
        ├── render.py
        ├── provenance.py
        ├── validation.py
        ├── yamlio.py
        ├── pointer.py
        ├── templating.py
        └── errors.py
```

Module names may vary, but responsibilities must remain separated.

---

# Project configuration

The project-configuration file (`yaml-frag.yaml` by default, overridable with
`--config`) is how a project adapts the generic renderer to a specific use case.
It declares default locations, how output is written, and named validators.

```yaml
version: 1

# Default input locations (CLI flags override these).
inventory: inventory/targets.yaml
fragments_dir: fragments

# How each target's rendered document is written.
output:
  # Destination path pattern. "{target}" is substituted with the target name.
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

Rules:

* `output.path` must contain `{target}` when rendering more than one target.
* `output.template`, `output.schema`, and `output.validators` are optional.
* Everything domain-specific (the `#cloud-config` header, the Subiquity
  validator, any document schema) lives here or in fragments — never in the
  renderer.

---

# Inventory format

The inventory defines defaults, reusable groups, and targets.

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

A **target** is any named thing you want to render a document for (a machine, an
environment, a service — the renderer does not care).

Groups are an optional generic reuse mechanism: a named bundle of variables and
fragments a target can pull in.

## Fragment ordering

The final ordered fragment list for a target is the concatenation of:

1. inventory `defaults.fragments`;
2. for each group the target lists, that group's fragments, in the target's
   group order;
3. the target's own `fragments`.

Precedence is purely positional: entries later in this resolved list override
earlier ones. The renderer never reorders. For the example above:

```text
autoinstall/base
autoinstall/default-user
ubuntu-24.04
hardware/gb10
roles/general-server
hosts/gb10-01
autoinstall/checks
```

## Variable precedence

Variables are layered in this order (later wins):

1. inventory `defaults.variables`;
2. group variables, in the target's group order;
3. target variables;
4. secret overlay (see below);
5. CLI overrides (`--var KEY=VALUE`).

CLI overrides:

```bash
yaml-frag render gb10-01 --var identity_hostname=test-gb10
```

## Optional external secret variables

Support an optional untracked secrets file, merged after target variables but
before CLI overrides:

```bash
yaml-frag render gb10-01 --secrets inventory/secrets.yaml
```

```yaml
targets:
  gb10-01:
    identity_password_hash: "$6$..."
```

The renderer must never log secret values.

---

# Fragment format

Each fragment is a YAML document containing:

* a fragment format version;
* a name that matches its path;
* a human-readable description;
* an ordered set of operations;
* optional required variables.

```yaml
fragment:
  version: 1
  name: autoinstall/default-user
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

## Paths

Use JSON Pointer-style paths:

```text
/                       the root document
/a/b/c                  nested mapping keys
```

Rules:

* `/` represents the root document.
* `/a/b/c` addresses nested mapping keys.
* Array indexes are not supported initially.
* Escaping follows JSON Pointer rules: `~1` → `/`, `~0` → `~`.

Array-index mutation is intentionally omitted; it encourages brittle fragments.

## Fragment names

Fragment references omit the `.yaml` suffix and may use any nested path:

```yaml
fragments:
  - hardware/gb10
  - vendors/dell/poweredge-r640
```

The renderer resolves `hardware/gb10` to `<fragments_dir>/hardware/gb10.yaml`.
The fragment file must declare a matching name:

```yaml
fragment:
  name: hardware/gb10
```

A mismatch is an error.

---

# Supported merge operations

## `set`

Replace the value at a path, creating missing parent mappings.

```yaml
- op: set
  path: /autoinstall/kernel
  value:
    package: linux-generic-hwe-24.04
```

If a value already exists, it is replaced completely. Use this for explicit
scalar, list, or object replacement.

## `merge`

Recursively merge one mapping into another mapping.

```yaml
- op: merge
  path: /autoinstall
  value:
    keyboard:
      layout: us
    locale: en_US.UTF-8
```

Rules:

* both the existing target and incoming value must be mappings;
* nested mappings are recursively merged;
* scalar values are replaced;
* lists are not implicitly merged;
* if a list already exists and the incoming mapping contains a list at the same
  path, rendering fails unless the list value is identical.

Fragments that need to modify lists must use `append`, `prepend`, `set`, or a
removal operation explicitly.

## `append`

Append one or more values to a list.

```yaml
- op: append
  path: /autoinstall/user-data/packages
  value:
    - curl
    - ca-certificates
```

Rules:

* create the list when it does not exist;
* fail if the existing value is not a list;
* preserve order;
* do not deduplicate by default.

Optional field `deduplicate: true` removes duplicates while preserving the first
occurrence. For mappings inside lists, equality is structural.

## `prepend`

Prepend one or more values to a list, preserving the order of incoming values.

```yaml
- op: prepend
  path: /autoinstall/user-data/runcmd
  value:
    - echo "Starting bootstrap"
```

## `remove`

Remove a complete field. Removing a missing field is an error unless
`missing_ok: true`.

```yaml
- op: remove
  path: /autoinstall/oem
  missing_ok: true
```

## `remove-list-items`

Remove selected values from a list.

```yaml
- op: remove-list-items
  path: /autoinstall/user-data/packages
  value:
    - vim-tiny
```

Rules:

* fail if the target is not a list;
* remove all structurally equal matches;
* fail when no requested values were found unless `missing_ok: true`.

## `assert`

Verify a condition without modifying the document. This is the primary mechanism
for document-specific validation (see "Validation").

```yaml
- op: assert
  path: /autoinstall/version
  equals: 1

- op: assert
  path: /autoinstall/storage/layout/name
  equals: lvm
```

Supported assertion forms initially:

```yaml
equals: value
exists: true
exists: false
type: mapping
type: list
type: string
type: integer
type: boolean
```

Assertions fail closed with a message naming the target, fragment, operation
index, path, and the expectation.

---

# Template rendering

Fragment values support strict Jinja-style substitution.

```yaml
hostname: "{{ identity_hostname }}"
```

Use strict undefined-variable behavior. A missing variable must produce an error
containing:

* target name;
* fragment name;
* operation index;
* missing variable name.

Do not silently substitute an empty string.

## Template scope

Templates may appear in mapping values, list values, multiline strings,
operation paths, and assertion values. Templates must not dynamically create new
YAML structure by returning YAML text; rendering occurs on already-parsed scalar
strings.

Variables remain typed when the entire scalar is a single template expression.
For example, with `enable_package_upgrade: false`:

```yaml
package_upgrade: "{{ enable_package_upgrade }}"
```

yields Boolean `false`, not string `"False"`. Use a native Jinja environment or
equivalent.

## Security

Do not allow arbitrary Python execution from templates. Do not expose
filesystem, environment, subprocess, or Python object internals. Provide only a
small set of safe filters: `default`, `lower`, `upper`, `replace`, `join`,
`tojson`. Custom filters are optional for the initial release.

---

# Rendering algorithm

For each requested target:

1. Load the project configuration.
2. Load and validate the inventory.
3. Resolve defaults, groups, and the target definition.
4. Build the ordered variable map.
5. Build the ordered fragment list.
6. Load every referenced fragment and validate it against the fragment schema.
7. Start with an empty document.
8. For each fragment, in order:
   1. verify required variables;
   2. render templates using the target variable map;
   3. apply operations in listed order;
   4. record provenance for every changed path;
   5. evaluate `assert` operations as they are encountered.
9. Run generic structural validation (unresolved-marker check; optional
   project-supplied document schema).
10. Serialize deterministic YAML.
11. If the output has a template, inject the serialized YAML into it (replacing
    `{{ document }}`); otherwise use the serialized YAML directly.
12. Write the result to the configured output path (`{target}` substituted),
    using a temporary file and atomic rename.
13. Run any selected validators against the written output.

The renderer creates exactly the file(s) the project configures. It does not
force any NoCloud `meta-data` or other companion files.

---

# Provenance tracking

The renderer tracks which fragment and operation last modified each path.

```text
/autoinstall/kernel/package
  value: linux-generic-hwe-24.04
  source: hardware/gb10 operation 0

/autoinstall/user-data/packages
  contributors:
    - roles/general-server operation 0
```

Expose through:

```bash
yaml-frag explain gb10-01
yaml-frag explain gb10-01 /autoinstall/storage
```

---

# Conflict reporting

When a `set` replaces an existing value, emit a warning by default:

```text
warning: hardware/gb10 operation 0 replaced /autoinstall/kernel
  previous source: ubuntu-24.04
  new source: hardware/gb10
```

Suppress with `--quiet-overrides`. A `merge` encountering an incompatible type
must fail:

```text
cannot merge mapping into list at /autoinstall/user-data/packages
  existing value from: roles/general-server
  incoming value from: hosts/gb10-01
```

---

# Validation

There are four clearly separable validation concerns. Only the first two are
built into the renderer; the last two are supplied by the project.

## Input validation (built in)

* **Inventory** (against `schemas/inventory.schema.json` plus structural rules):
  inventory version; unique target names; unique group names; referenced groups
  exist; fragments are lists of strings; variables are mappings; order
  preserved; group cycles rejected if nested groups are implemented.
* **Fragments** (against `schemas/fragment.schema.json` plus rules): supported
  fragment version; name matches path; recognized `op`; valid path;
  op-appropriate fields present; `append`/`prepend` values are lists; `merge`
  values are mappings; assertions use supported forms.
* **Project configuration** (against `schemas/project.schema.json`).

## Generic rendered-document validation (built in)

The renderer performs only document-agnostic checks:

* reject unresolved template markers (text containing `{{` or `{%`);
* if `output.schema` is set, validate the rendered document against that JSON
  schema.

The renderer does **not** contain any hard-coded structural expectations (no
`autoinstall.version`, no required `identity`/`ssh`/`storage`/`network` rules).

## Document assertions (project supplied, via fragments)

Any document-specific structural requirement is expressed as `assert`
operations in fragments. The example project ships an `autoinstall/checks`
fragment asserting the autoinstall structure (version, identity fields, ssh
booleans, storage presence), and per-hardware fragments assert their own
additions (e.g. `network.version == 2`). Assertions run as part of rendering and
fail closed.

## Named validators (project supplied, external)

Projects may declare named validators in the project config. Select them with a
repeatable `--validator NAME`; if none is given, the output's default validators
run. Each validator runs an external command against the written output file and
fails on nonzero exit. This replaces the old autoinstall-specific `--subiquity`
flag with a generic mechanism.

```bash
yaml-frag validate gb10-01 --validator subiquity
```

The CLI must clearly distinguish internal validation, YAML parsing, and external
validator failures.

---

# YAML serialization

Output must be stable and diff-friendly:

* preserve mapping insertion order (do not sort keys);
* two-space indentation;
* never emit Python-specific YAML tags;
* emit Booleans as `true`/`false`;
* quote strings only when necessary;
* prefer block style for multiline strings;
* end files with exactly one newline.

Any header such as `#cloud-config` comes from the output template, not the
serializer. Use `ruamel.yaml` (recommended) or PyYAML if determinism is met.

---

# Command-line interface

The executable is named `yaml-frag`.

Global option available to all commands:

```text
--config PATH        project configuration (default: yaml-frag.yaml)
```

## Render one target

```bash
yaml-frag render gb10-01
```

Writes the configured output path. Options:

```text
--inventory PATH
--fragments-dir PATH
--output PATH            override the destination path for this render
--secrets PATH
--var KEY=VALUE
--validator NAME         run a named validator (repeatable)
--stdout                 print rendered output to stdout instead of writing
--dry-run
--quiet-overrides
--no-validate            skip generic validation and validators
```

`--stdout` prints only the rendered output; all diagnostics go to stderr.

## Render all targets

```bash
yaml-frag render-all
```

Render every target in inventory order. Return nonzero if any fails. Never leave
a partially written final output file for a failed target (temp file + atomic
rename).

## Validate

```bash
yaml-frag validate gb10-01 [--validator NAME]
yaml-frag validate-all [--validator NAME]
```

Validation renders in memory without writing output unless explicitly requested.
External validators that require a file operate on a temporary render.

## Explain

```bash
yaml-frag explain gb10-01
yaml-frag explain gb10-01 /autoinstall/storage
```

## List

```bash
yaml-frag list targets
yaml-frag list fragments
yaml-frag list groups
```

## Show resolved inputs

```bash
yaml-frag inspect gb10-01
```

```yaml
target: gb10-01
groups:
  - gb10
  - general_servers
fragments:
  - autoinstall/base
  - autoinstall/default-user
  - ubuntu-24.04
  - hardware/gb10
  - roles/general-server
  - hosts/gb10-01
  - autoinstall/checks
variables:
  identity_hostname: gb10-01
  identity_username: chris
  identity_password_hash: "<redacted>"
  ssh_import_id: gh:chpatton013
  primary_interface: enP7s7
```

Automatically redact variables whose names contain any of: `password`,
`secret`, `token`, `private`, `credential`. Provide `--show-secrets` to reveal
full values.

---

# Python requirements

Target Python 3.12+. Use type annotations throughout. Recommended dependencies:

```text
click
pydantic
jinja2
ruamel.yaml
jsonschema
```

Suggested value objects:

```python
@dataclass(frozen=True)
class TargetDefinition:
    name: str
    groups: tuple[str, ...]
    fragments: tuple[str, ...]
    variables: dict[str, object]

@dataclass(frozen=True)
class FragmentOperation:
    op: str
    path: str
    value: object | None = None
    deduplicate: bool = False
    missing_ok: bool = False

@dataclass(frozen=True)
class ProvenanceEntry:
    fragment: str
    operation_index: int
    operation: str
```

Custom exception types:

```text
ConfigError
InventoryError
FragmentError
TemplateRenderError
MergeConflictError
AssertionFailedError
ValidationError
UnknownTargetError
UnknownFragmentError
```

Every user-facing error must include enough context to find the source file and
operation.

---

# Merge examples

## Appending packages

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

## Replacing a scalar object

```yaml
# earlier: set /autoinstall/kernel -> {package: linux-generic}
# later:   set /autoinstall/kernel -> {package: linux-generic-hwe-24.04}
```

Result: `{package: linux-generic-hwe-24.04}`; renderer emits an override
warning.

## Extending a list of mappings

Two `append` operations to `/autoinstall/user-data/write_files` concatenate the
entries. The renderer never merges list entries by a key field; to replace the
whole list, use `set`.

---

# Example project: Ubuntu autoinstall

The repository ships a complete example that renders Ubuntu 24.04 autoinstall
`user-data`, demonstrating that all autoinstall specifics live in data:

* `yaml-frag.yaml` sets `output.template: templates/user-data.tmpl` (which adds
  the `#cloud-config` header) and `output.path: rendered/{target}/user-data`,
  and declares the optional `subiquity` validator.
* `fragments/autoinstall/base.yaml`, `default-user.yaml`, and `checks.yaml`,
  `fragments/ubuntu-24.04.yaml`, `fragments/hardware/*`, `fragments/roles/*`,
  and `fragments/hosts/*` build and assert the document.
* No autoinstall knowledge exists in `src/yaml_frag/`.

## Complete expected render (target `gb10-01`)

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
    password: "$6$example-salt$example-hash"
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

Blank-line placement need not match exactly, but the YAML data and the
`#cloud-config` header must.

---

# Testing requirements

## Unit tests (merge/templating/pointer/provenance)

set root; set nested path; merge nested mappings; reject mapping/list conflicts;
append to missing/existing list; append with dedup; prepend; remove; remove with
and without `missing_ok`; remove-list-items; assertions; JSON Pointer escaping;
strict missing-variable failures; typed template values; fragment-name mismatch;
provenance recording; override warning.

## Inventory tests

default/group/target/CLI variable precedence; group order; fragment order;
missing groups; missing fragments; duplicate targets; secret overlay.

## Config tests

load defaults; output path substitution; output template injection of
`{{ document }}`; validator selection resolution.

## Snapshot tests

Render representative targets and compare with committed expected files:
`generic-vm-01`, `gb10-01`, `gb10-02`. The two GB10 hosts differ only in
host-specific variables unless their inventory selects different fragments.

## CLI tests

successful render; render to stdout; render-all; validation failure exit codes;
missing target; explain output; redaction; atomic output; diagnostics on stderr;
`--validator` selection.

## Error-message tests

Errors include actionable context, e.g.:

```text
gb10-01: fragment hardware/gb10, operation 2: missing required variable "primary_interface"
```

---

# Exit codes

```text
0  success
1  general rendering failure
2  invalid command-line usage
3  inventory validation failure
4  fragment validation failure
5  merge conflict
6  rendered-document validation failure (generic validation, schema, assertion, or validator)
7  project-configuration error
```

Values may change but must be documented and tested.

---

# Non-goals for the initial version

Do not implement: a web interface; dynamic Python plugins; arbitrary template
code execution; array-index mutation; semantic merging of list entries;
key-aware list merges; multiple output files per target; automatic hardware
discovery; PXE/TFTP/DHCP configuration; deployment to HTTP servers; secret
manager integration; reimplementing any full document schema (e.g. Subiquity);
running Ansible; installing Ubuntu.

The renderer's job is to produce correct, inspectable YAML artifacts.

---

# Future extensions

Room for: multiple named outputs per target; per-target metadata files;
PXE/iPXE script generation; publishing to an HTTP directory; secret retrieval
from a password manager; target enrollment states; additional built-in
validators; schema-aware validation for multiple document families; encrypted
outputs; fragment deprecation warnings; fragment dependency declarations;
optional fragment conditions.

Do not implement these at the expense of a clear first version.

---

# Acceptance criteria

1. A target can be defined in inventory with ordered groups and fragments.
2. Fragments can explicitly set, merge, append, prepend, remove, and assert.
3. Missing template variables fail with useful diagnostics.
4. The renderer produces deterministic YAML with no built-in schema knowledge.
5. Output can be wrapped in a project-supplied template (e.g. `#cloud-config`).
6. The output path is configurable; no companion files are forced.
7. Lists from multiple fragments append without accidental replacement.
8. Fragments can replace kernel, storage, and network configuration.
9. Target variables can supply hostname, username, password hash, SSH identity,
   and interface name.
10. The renderer explains where final values came from.
11. Secret-looking values are redacted from inspection output.
12. Invalid config, inventory, fragments, or rendered documents return nonzero.
13. Document-specific requirements are enforced via fragment assertions and
    project-configured validators, not built-in renderer logic.
14. Named validators are selectable with `--validator`.
15. Tests cover merge, precedence, config, rendering, provenance, and CLI.
16. The example autoinstall project renders the expected `gb10-01` output above.
17. The README explains installation, structure, project config, fragment and
    inventory authoring, and CLI usage.

---

# Implementation preference

Favor a small, unsurprising implementation over a highly abstract framework. The
most important properties are: deterministic ordering; explicit list behavior;
strict validation; good diagnostics; inspectable provenance; a renderer with no
domain knowledge; straightforward Git review. Avoid "smart" merge behavior that
guesses intent.
