# Autoinstall Configuration Renderer

## Objective

Build a Python command-line tool that renders complete Ubuntu Server autoinstall configurations from:

1. an inventory of machines;
2. an ordered sequence of reusable configuration fragments;
3. per-machine variables;
4. explicit fragment merge instructions.

The system should support repeatable, unattended installation of Ubuntu Server on heterogeneous homelab machines.

The design must make it easy to:

* define reusable base configurations;
* define hardware-model-specific configuration;
* define role-specific configuration;
* define environment-specific configuration;
* override configuration values deliberately;
* append items to selected lists;
* render one machine or all machines;
* inspect which fragment supplied each value;
* validate rendered YAML before deployment;
* avoid implicit or surprising merge behavior.

The initial target is Ubuntu 24.04 Server autoinstall with Subiquity and cloud-init, but the renderer should remain generic enough to merge arbitrary YAML documents.

---

# Design principles

## Ordered composition

Each machine specifies an ordered list of fragments.

Fragments are applied from first to last.

Later fragments may override or extend values produced by earlier fragments.

Example:

```text
base
ubuntu-24.04
hardware-gb10
role-proxmox
site-home
host-gb10-01
```

The result should be deterministic.

## Explicit merge behavior

Fragments must include enough metadata to tell the renderer how each field should be applied.

Do not rely on a recursive deep-merge algorithm alone.

A fragment must be able to specify operations such as:

* replace a scalar or object;
* merge a mapping;
* append values to a list;
* prepend values to a list;
* replace a list;
* remove a field;
* remove selected list entries.

The default behavior should be conservative and predictable.

## Separation of data and rendering logic

The renderer must not contain hard-coded knowledge about specific hosts, hardware models, usernames, SSH identities, package lists, or network interfaces.

Those belong in inventory and fragments.

The renderer may contain generic knowledge about:

* YAML parsing;
* merge operations;
* template rendering;
* validation;
* provenance tracking;
* command-line behavior.

## Fail closed

Invalid or ambiguous configuration should stop rendering with a clear error.

Examples:

* referenced fragment does not exist;
* template variable is missing;
* two fragments produce incompatible types;
* a list is implicitly replaced without an explicit operation;
* a removal targets a missing path when strict mode is enabled;
* rendered YAML does not contain `autoinstall.version: 1`;
* rendered configuration contains unresolved template expressions.

## Human-readable source files

Inventory and fragment files should be easy to review in Git.

Avoid embedding large amounts of Python or arbitrary executable logic in YAML.

Use Jinja-style variable substitution for values, but keep control flow minimal.

---

# Suggested repository layout

```text
autoinstall-config/
├── README.md
├── pyproject.toml
├── inventory/
│   ├── machines.yaml
│   └── groups.yaml
├── fragments/
│   ├── base/
│   │   ├── autoinstall-base.yaml
│   │   └── default-user.yaml
│   ├── releases/
│   │   └── ubuntu-24.04.yaml
│   ├── hardware/
│   │   ├── gb10.yaml
│   │   └── generic-vm.yaml
│   ├── roles/
│   │   ├── proxmox-host.yaml
│   │   ├── docker-host.yaml
│   │   └── general-server.yaml
│   ├── sites/
│   │   └── home.yaml
│   └── hosts/
│       └── gb10-01.yaml
├── schemas/
│   ├── inventory.schema.json
│   └── fragment.schema.json
├── templates/
│   └── optional-static-files/
├── rendered/
│   └── .gitkeep
├── tests/
│   ├── fixtures/
│   ├── test_merge.py
│   ├── test_render.py
│   ├── test_inventory.py
│   └── test_cli.py
└── src/
    └── autoinstall_renderer/
        ├── __init__.py
        ├── cli.py
        ├── inventory.py
        ├── fragments.py
        ├── merge.py
        ├── render.py
        ├── provenance.py
        ├── validation.py
        └── errors.py
```

The exact module names may vary, but responsibilities should remain separated.

---

# Inventory format

The main inventory file should define defaults, reusable groups, and machines.

Example:

```yaml
version: 1

defaults:
  variables:
    identity_username: chris
    ssh_import_id: gh:chpatton013
    locale: en_US.UTF-8
    keyboard_layout: us

  fragments:
    - base/autoinstall-base
    - base/default-user
    - releases/ubuntu-24.04
    - sites/home

groups:
  gb10:
    variables:
      hardware_model: gb10

    fragments:
      - hardware/gb10

  proxmox_hosts:
    fragments:
      - roles/proxmox-host

machines:
  gb10-01:
    groups:
      - gb10
      - proxmox_hosts

    fragments:
      - hosts/gb10-01

    variables:
      identity_hostname: gb10-01
      identity_password_hash: "$6$example-salt$example-hash"
      primary_interface: enP7s7
```

## Fragment ordering

The renderer must produce the final fragment order as:

1. inventory defaults;
2. groups in the order listed on the machine;
3. machine fragments.

For the preceding example:

```text
base/autoinstall-base
base/default-user
releases/ubuntu-24.04
sites/home
hardware/gb10
roles/proxmox-host
hosts/gb10-01
```

Group order must be significant.

The renderer must not alphabetically reorder groups or fragments.

## Variable precedence

Variables should be merged in the following order:

1. inventory defaults;
2. group variables in machine group order;
3. machine variables;
4. CLI overrides.

Later values override earlier values.

CLI overrides should use syntax such as:

```bash
autoinstall-render render gb10-01 \
  --var identity_hostname=test-gb10
```

## Optional external secret variables

The renderer should support loading an optional untracked secrets file:

```bash
autoinstall-render render gb10-01 \
  --secrets inventory/secrets.yaml
```

Example:

```yaml
machines:
  gb10-01:
    identity_password_hash: "$6$..."
```

Secret values should be merged after normal machine variables but before CLI overrides.

The renderer must not log secret values in normal output.

---

# Fragment format

Each fragment is a YAML document containing:

* a fragment format version;
* a human-readable description;
* a set of merge operations;
* optional required variables;
* optional assertions.

Example:

```yaml
fragment:
  version: 1
  name: base/default-user
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

  - op: append
    path: /autoinstall/user-data/write_files
    value:
      - path: "/etc/sudoers.d/90-{{ identity_username }}-nopasswd"
        owner: root:root
        permissions: "0440"
        content: |
          {{ identity_username }} ALL=(ALL:ALL) NOPASSWD:ALL

  - op: append
    path: /autoinstall/user-data/runcmd
    value:
      - >-
        visudo --check
        --file="/etc/sudoers.d/90-{{ identity_username }}-nopasswd"
```

## Paths

Use JSON Pointer-style paths:

```text
/autoinstall
/autoinstall/storage/layout
/autoinstall/user-data/packages
```

Rules:

* `/` represents the root document.
* `/a/b/c` addresses nested mapping keys.
* Array indexes should not be supported initially.
* Escaping should follow JSON Pointer rules:

  * `~1` represents `/`;
  * `~0` represents `~`.

Supporting array indexes is unnecessary for the first version and would encourage brittle fragments.

## Fragment names

Fragment references should omit the `.yaml` suffix:

```yaml
fragments:
  - hardware/gb10
```

The renderer should resolve this to:

```text
fragments/hardware/gb10.yaml
```

Fragment files must declare a matching name:

```yaml
fragment:
  name: hardware/gb10
```

A mismatch must be treated as an error.

---

# Supported merge operations

## `set`

Replace the value at a path.

Create missing parent mappings when possible.

Example:

```yaml
- op: set
  path: /autoinstall/kernel
  value:
    package: linux-generic-hwe-24.04
```

If a value already exists, it is replaced completely.

Use this for explicit scalar, list, or object replacement.

## `merge`

Recursively merge one mapping into another mapping.

Example:

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
* if a list already exists and the incoming mapping contains a list at the same path, rendering must fail unless the list value is identical.

This rule prevents accidental package-list replacement.

Fragments that need to modify lists must use `append`, `prepend`, `set`, or removal operations explicitly.

## `append`

Append one or more values to a list.

Example:

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

Optional fragment field:

```yaml
deduplicate: true
```

When enabled, duplicate values should be removed while preserving the first occurrence.

For mappings inside lists, equality should be structural.

## `prepend`

Prepend one or more values to a list.

Example:

```yaml
- op: prepend
  path: /autoinstall/user-data/runcmd
  value:
    - echo "Starting host bootstrap"
```

Preserve the order of the incoming values.

## `remove`

Remove a complete field.

Example:

```yaml
- op: remove
  path: /autoinstall/oem
```

By default, removing a missing field should be an error.

Allow:

```yaml
missing_ok: true
```

for intentionally optional removals.

## `remove-list-items`

Remove selected values from a list.

Example:

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

Verify a condition without modifying the document.

Examples:

```yaml
- op: assert
  path: /autoinstall/version
  equals: 1
```

```yaml
- op: assert
  path: /autoinstall/storage/layout/name
  equals: lvm
```

Support these assertion forms initially:

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

Assertions are useful for hardware- or role-specific fragments that require a known base configuration.

---

# Template rendering

Fragment values should support strict Jinja-style substitution.

Example:

```yaml
hostname: "{{ identity_hostname }}"
```

Use strict undefined-variable behavior.

A missing variable must produce an error containing:

* machine name;
* fragment name;
* operation index;
* missing variable name.

Do not silently substitute an empty string.

## Template scope

Templates may appear in:

* mapping values;
* list values;
* multiline strings;
* operation paths, if needed;
* fragment assertions.

Templates should not be allowed to dynamically create new YAML structure by returning YAML text.

Rendering occurs on already-parsed scalar strings.

For example, this is supported:

```yaml
path: "/etc/sudoers.d/90-{{ identity_username }}-nopasswd"
```

This should not be supported:

```yaml
value: "{{ arbitrary_yaml_document }}"
```

when the variable is expected to be reparsed as YAML.

Variables should remain typed when the entire scalar is a single template expression.

Example inventory:

```yaml
enable_package_upgrade: false
```

Fragment:

```yaml
package_upgrade: "{{ enable_package_upgrade }}"
```

The resulting value should be Boolean `false`, not string `"False"`.

Use a native Jinja environment or equivalent behavior.

## Security

Do not allow arbitrary Python execution from templates.

Do not expose filesystem, environment, subprocess, or Python object internals to templates.

Provide only a small set of safe filters, such as:

```text
default
lower
upper
replace
join
tojson
```

Custom filters are optional for the initial release.

---

# Initial fragment set

The implementation should include representative fragments based on the current desired Ubuntu installation.

## `base/autoinstall-base.yaml`

```yaml
fragment:
  version: 1
  name: base/autoinstall-base
  description: Establish the required Ubuntu autoinstall structure.

operations:
  - op: set
    path: /
    value:
      autoinstall:
        version: 1

  - op: merge
    path: /autoinstall
    value:
      keyboard:
        layout: "{{ keyboard_layout }}"
      locale: "{{ locale }}"

  - op: merge
    path: /autoinstall/user-data
    value:
      package_update: true
      package_upgrade: false
```

The renderer should emit `#cloud-config` before the rendered YAML, but that marker should not be represented as YAML data.

## `base/default-user.yaml`

```yaml
fragment:
  version: 1
  name: base/default-user
  description: Configure the primary admin user, SSH, and passwordless sudo.

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

  - op: append
    path: /autoinstall/user-data/write_files
    value:
      - path: "/etc/sudoers.d/90-{{ identity_username }}-nopasswd"
        owner: root:root
        permissions: "0440"
        content: |
          {{ identity_username }} ALL=(ALL:ALL) NOPASSWD:ALL

  - op: append
    path: /autoinstall/user-data/runcmd
    value:
      - >-
        visudo --check
        --file="/etc/sudoers.d/90-{{ identity_username }}-nopasswd"
```

## `releases/ubuntu-24.04.yaml`

```yaml
fragment:
  version: 1
  name: releases/ubuntu-24.04
  description: Ubuntu 24.04 Server installation source.

operations:
  - op: set
    path: /autoinstall/source
    value:
      id: ubuntu-server-minimal
      search_drivers: false
```

Do not place the HWE kernel in the generic release fragment because it may be hardware-specific.

## `hardware/gb10.yaml`

```yaml
fragment:
  version: 1
  name: hardware/gb10
  description: Configuration for GB10 physical hosts.

requires:
  variables:
    - primary_interface

operations:
  - op: set
    path: /autoinstall/kernel
    value:
      package: linux-generic-hwe-24.04

  - op: set
    path: /autoinstall/storage
    value:
      layout:
        name: lvm
        sizing-policy: all

  - op: set
    path: /autoinstall/network
    value:
      version: 2
      ethernets:
        primary:
          match:
            name: "{{ primary_interface }}"
          dhcp4: true
          dhcp6: false
```

## `roles/general-server.yaml`

```yaml
fragment:
  version: 1
  name: roles/general-server
  description: Basic tools for a general-purpose Ubuntu server.

operations:
  - op: append
    path: /autoinstall/user-data/packages
    deduplicate: true
    value:
      - bind9-dnsutils
      - ca-certificates
      - curl
      - iputils-ping
      - vim-tiny
```

## `roles/proxmox-host.yaml`

This fragment may initially be a placeholder until the exact Proxmox installation flow is decided.

It should still demonstrate explicit composition:

```yaml
fragment:
  version: 1
  name: roles/proxmox-host
  description: Bootstrap prerequisites for a future Proxmox host.

operations:
  - op: append
    path: /autoinstall/user-data/packages
    deduplicate: true
    value:
      - curl
      - gnupg
      - ca-certificates
```

---

# Rendering algorithm

For each requested machine:

1. Load and validate the inventory.
2. Resolve defaults, groups, and machine definition.
3. Build the ordered variable map.
4. Build the ordered fragment list.
5. Load every referenced fragment.
6. Validate each fragment against the fragment schema.
7. Start with an empty document.
8. For each fragment:

   1. verify required variables;
   2. render templates using the machine variable map;
   3. apply operations in listed order;
   4. record provenance for every changed path;
   5. evaluate assertions.
9. Run structural validation.
10. Run optional external Subiquity validation.
11. Serialize deterministic YAML.
12. Prefix the output with:

```text
#cloud-config
```

13. Write the rendered file to:

```text
rendered/<machine>/user-data
```

14. Optionally create an empty NoCloud metadata file:

```text
rendered/<machine>/meta-data
```

An optional metadata template may be added later.

---

# Provenance tracking

The renderer should track which fragment and operation last modified each path.

Example internal record:

```yaml
/autoinstall/kernel:
  fragment: hardware/gb10
  operation: 0
  op: set

/autoinstall/user-data/packages/0:
  fragment: roles/general-server
  operation: 0
  op: append
```

Expose this through:

```bash
autoinstall-render explain gb10-01
```

Example output:

```text
/autoinstall/kernel/package
  value: linux-generic-hwe-24.04
  source: hardware/gb10 operation 1

/autoinstall/network/ethernets/primary/match/name
  value: enP7s7
  source: hardware/gb10 operation 3

/autoinstall/user-data/packages
  contributors:
    - roles/general-server operation 1
    - roles/proxmox-host operation 1
```

Also support querying one path:

```bash
autoinstall-render explain gb10-01 \
  /autoinstall/network
```

Provenance is important because fragments are intentionally layered and later overrides must be inspectable.

---

# Conflict reporting

When a fragment replaces an existing value, the renderer should support warning output.

Example:

```text
warning: hardware/gb10 operation 1 replaced
/autoinstall/kernel

previous source: releases/ubuntu-24.04
new source: hardware/gb10
```

Normal `set` operations may replace existing values without failing, because replacement is explicit.

However, warnings should be enabled by default.

Allow:

```bash
--quiet-overrides
```

to suppress these warnings.

A `merge` operation encountering an incompatible type must fail.

Example:

```text
cannot merge mapping into list at
/autoinstall/user-data/packages

existing value from: roles/general-server
incoming value from: hosts/gb10-01
```

---

# Validation

## Inventory validation

Validate:

* inventory version;
* unique machine names;
* unique group names;
* groups referenced by machines exist;
* fragments are lists of strings;
* variables are mappings;
* machine fragment order is preserved;
* group inheritance cycles are impossible or rejected.

Nested groups are optional for the first version.

If nested groups are implemented, cycles must be detected.

## Fragment validation

Validate:

* fragment version is supported;
* fragment name matches its file path;
* operation is recognized;
* path is valid;
* required fields exist for the selected operation;
* `append` and `prepend` values are lists;
* `merge` values are mappings;
* assertions use supported forms.

## Rendered-document validation

At minimum, require:

```yaml
autoinstall:
  version: 1
```

Reject unresolved template markers matching patterns such as:

```text
{{ ...
{% ...
```

Validate that:

* `identity.hostname` is a nonempty string;
* `identity.username` is a nonempty string;
* `identity.password` is a nonempty string;
* `ssh.install-server` is Boolean;
* `ssh.allow-pw` is Boolean;
* `storage` exists;
* `network.version` is `2`, when network configuration exists;
* `user-data.packages` is a list, when present;
* `user-data.write_files` is a list, when present;
* `user-data.runcmd` is a list, when present.

Do not attempt to duplicate the complete Subiquity schema manually.

## Optional Subiquity validation

Support an optional external validation command.

Example configuration:

```yaml
validation:
  command:
    - python3
    - tools/validate-autoinstall-user-data.py
```

CLI:

```bash
autoinstall-render validate gb10-01 --subiquity
```

The renderer should clearly distinguish:

* internal renderer validation;
* YAML parsing;
* optional Subiquity validation.

---

# YAML serialization

Output should be stable and diff-friendly.

Requirements:

* preserve mapping insertion order;
* use two-space indentation;
* never emit Python-specific YAML tags;
* emit Booleans as `true` and `false`;
* quote strings only when necessary;
* preserve multiline strings using block style where practical;
* end files with one newline;
* prefix with `#cloud-config`;
* do not sort keys alphabetically.

Exact comment preservation is not required.

The rendered file should prioritize correctness and stable diffs over reproducing fragment formatting exactly.

Use a mature YAML library such as `ruamel.yaml` when useful for formatting, although PyYAML is acceptable if deterministic output requirements are met.

---

# Command-line interface

The executable should be named:

```text
autoinstall-render
```

## Render one machine

```bash
autoinstall-render render gb10-01
```

Default output:

```text
rendered/gb10-01/user-data
rendered/gb10-01/meta-data
```

Options:

```text
--inventory PATH
--fragments-dir PATH
--output PATH
--secrets PATH
--var KEY=VALUE
--stdout
--dry-run
--quiet-overrides
--no-validate
```

`--stdout` should print only the rendered configuration to standard output.

Diagnostics must go to standard error.

## Render all machines

```bash
autoinstall-render render-all
```

Render every machine in inventory order.

Return nonzero if any machine fails.

Do not leave a partially written final output file for a failed machine.

Use temporary files and atomic rename.

## Validate

```bash
autoinstall-render validate gb10-01
autoinstall-render validate-all
```

Validation should render in memory without writing output unless explicitly requested.

## Explain

```bash
autoinstall-render explain gb10-01
autoinstall-render explain gb10-01 /autoinstall/storage
```

## List

```bash
autoinstall-render list machines
autoinstall-render list fragments
autoinstall-render list groups
```

## Show resolved inputs

```bash
autoinstall-render inspect gb10-01
```

Output:

```yaml
machine: gb10-01

groups:
  - gb10
  - proxmox_hosts

fragments:
  - base/autoinstall-base
  - base/default-user
  - releases/ubuntu-24.04
  - sites/home
  - hardware/gb10
  - roles/proxmox-host
  - hosts/gb10-01

variables:
  identity_hostname: gb10-01
  identity_username: chris
  identity_password_hash: "<redacted>"
  ssh_import_id: gh:chpatton013
  primary_interface: enP7s7
```

Automatically redact variables whose names contain:

```text
password
secret
token
private
credential
```

Provide an explicit unsafe option if full values are ever needed:

```bash
--show-secrets
```

---

# Python requirements

Target Python 3.12 or later.

Use type annotations throughout.

Recommended dependencies:

```text
click or typer
pydantic
jinja2
ruamel.yaml or pyyaml
jsonschema
```

Possible architecture:

```python
@dataclass(frozen=True)
class MachineDefinition:
    name: str
    groups: list[str]
    fragments: list[str]
    variables: dict[str, object]
```

```python
@dataclass(frozen=True)
class FragmentOperation:
    op: str
    path: str
    value: object | None
    deduplicate: bool = False
    missing_ok: bool = False
```

```python
@dataclass(frozen=True)
class ProvenanceEntry:
    fragment: str
    operation_index: int
    operation: str
```

Use custom exception types:

```text
InventoryError
FragmentError
TemplateRenderError
MergeConflictError
ValidationError
UnknownMachineError
UnknownFragmentError
```

Every user-facing error should include enough context to find the source file and operation.

---

# Merge examples

## Appending packages

Base fragment:

```yaml
operations:
  - op: append
    path: /autoinstall/user-data/packages
    value:
      - curl
      - ca-certificates
```

Role fragment:

```yaml
operations:
  - op: append
    path: /autoinstall/user-data/packages
    deduplicate: true
    value:
      - curl
      - qemu-guest-agent
```

Result:

```yaml
packages:
  - curl
  - ca-certificates
  - qemu-guest-agent
```

## Replacing the kernel

Earlier fragment:

```yaml
operations:
  - op: set
    path: /autoinstall/kernel
    value:
      package: linux-generic
```

Later fragment:

```yaml
operations:
  - op: set
    path: /autoinstall/kernel
    value:
      package: linux-generic-hwe-24.04
```

Result:

```yaml
kernel:
  package: linux-generic-hwe-24.04
```

The renderer should emit an override warning.

## Extending `write_files`

Earlier fragment:

```yaml
operations:
  - op: append
    path: /autoinstall/user-data/write_files
    value:
      - path: /etc/example-a
        content: a
```

Later fragment:

```yaml
operations:
  - op: append
    path: /autoinstall/user-data/write_files
    value:
      - path: /etc/example-b
        content: b
```

Result:

```yaml
write_files:
  - path: /etc/example-a
    content: a
  - path: /etc/example-b
    content: b
```

Do not attempt to merge list entries based on their `path` fields automatically.

If a later fragment wants to replace the full list, it must use `set`.

## Removing a default

Earlier fragment:

```yaml
operations:
  - op: set
    path: /autoinstall/user-data/package_upgrade
    value: false
```

Later fragment:

```yaml
operations:
  - op: remove
    path: /autoinstall/user-data/package_upgrade
```

The final document omits the field.

---

# Complete expected render

For a GB10 host, an expected output should resemble:

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

Blank-line placement does not need to match this example exactly, but the YAML data must.

---

# Testing requirements

## Unit tests

Test each operation independently:

* set root;
* set nested path;
* merge nested mappings;
* reject mapping/list conflicts;
* append to missing list;
* append to existing list;
* append with deduplication;
* prepend;
* remove;
* remove missing with and without `missing_ok`;
* remove list items;
* assertions;
* JSON Pointer escaping;
* strict missing-variable failures;
* typed template values;
* fragment-name mismatch;
* provenance recording.

## Inventory tests

Test:

* default variables;
* group variable precedence;
* machine variable precedence;
* CLI variable precedence;
* group order;
* fragment order;
* missing groups;
* missing fragments;
* duplicate machine definitions;
* secret overlay behavior.

## Snapshot tests

Render representative machines and compare with committed expected YAML files.

At minimum:

```text
generic-vm-01
gb10-01
gb10-02
```

The two GB10 hosts should differ only in host-specific variables unless their inventory explicitly selects different fragments.

## CLI tests

Test:

* successful render;
* render to stdout;
* render-all;
* validation failure exit codes;
* missing machine;
* explain output;
* redaction;
* atomic output behavior;
* diagnostics on stderr.

## Error-message tests

Errors should include actionable context.

Good:

```text
gb10-01: fragment hardware/gb10, operation 2:
missing required variable "primary_interface"
```

Bad:

```text
KeyError: primary_interface
```

---

# Exit codes

Use stable exit codes:

```text
0  success
1  general rendering failure
2  invalid command-line usage
3  inventory validation failure
4  fragment validation failure
5  merge conflict
6  rendered configuration validation failure
```

Exact values may change, but they must be documented and tested.

---

# Non-goals for the initial version

Do not implement these initially:

* a web interface;
* dynamic Python plugins;
* arbitrary template code execution;
* automatic hardware discovery;
* automatic PXE server configuration;
* deployment to HTTP servers;
* DHCP configuration;
* TFTP configuration;
* secret-manager integration;
* array-index mutation;
* semantic merging of `write_files` entries;
* semantic merging of Netplan interfaces;
* complete Subiquity schema reimplementation;
* running Ansible;
* installing Ubuntu directly.

The renderer’s job is to produce correct, inspectable configuration artifacts.

---

# Future extensions

Design should leave room for:

* NoCloud `meta-data` rendering;
* per-machine `vendor-data`;
* PXE or iPXE script generation;
* automatic publishing to an HTTP directory;
* secret retrieval from a password manager;
* machine enrollment states such as `provisioning_enabled`;
* output formats for cloud-init outside Subiquity;
* AWS EC2 user-data rendering;
* Proxmox cloud-init snippets;
* FreeBSD-specific profiles;
* schema-aware validation for multiple Ubuntu releases;
* encrypted autoinstall configurations;
* fragment deprecation warnings;
* fragment dependency declarations;
* optional fragment conditions.

Do not implement these at the expense of a clear first version.

---

# Acceptance criteria

The implementation is complete when:

1. A machine can be defined in inventory with ordered groups and fragments.
2. Fragments can explicitly set, merge, append, prepend, remove, and assert values.
3. Missing template variables fail with useful diagnostics.
4. The renderer produces deterministic Ubuntu autoinstall YAML.
5. The rendered file begins with `#cloud-config`.
6. The renderer creates a NoCloud-compatible `user-data` file.
7. Package lists from multiple fragments can be appended without accidental replacement.
8. Hardware-specific fragments can replace kernel, storage, and network configuration.
9. Host-specific variables can supply hostname, username, password hash, SSH import identity, and interface name.
10. The renderer can explain where final values came from.
11. Secret-looking values are redacted from inspection output.
12. Invalid inventory, fragments, or rendered documents return nonzero.
13. Tests cover merge behavior, precedence, rendering, provenance, and CLI behavior.
14. A GB10 inventory entry renders the expected configuration shown above.
15. The project includes a README explaining installation, repository structure, fragment authoring, inventory authoring, and CLI usage.

---

# Implementation preference

Favor a small, unsurprising implementation over a highly abstract framework.

The most important properties are:

* deterministic ordering;
* explicit list behavior;
* strict validation;
* good diagnostics;
* inspectable provenance;
* straightforward Git review.

Avoid implementing “smart” merge behavior that guesses what the user intended.

