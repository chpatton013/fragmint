# fragmint — Structured Document Fragment Composer

Render structured documents — YAML, TOML, or JSON — from an inventory of
**targets**, an ordered sequence of reusable **fragments**, per-target
**variables**, and **explicit path-based merge operations**.

`fragmint` is generic: it has no built-in knowledge of any particular document
schema. Domain-specific behavior lives entirely in *modules, schemas,
validators, and fragments*. The repository ships a complete example —
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
  belongs in the inventory, the modules it imports, fragments, and schemas. The
  renderer's only generic knowledge is: parsing and deterministic
  serialization of the supported formats; path-based merge operations;
  template variable substitution;
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
  project's fragments (see
  `example/modules/autoinstall/fragments/autoinstall/checks.yaml`).
- **Human-readable source files.** Inventory, module, and fragment files are
  meant to be easy to review in Git. Avoid embedding large amounts of Python or
  arbitrary executable logic in YAML; use Jinja-style variable substitution for
  values, but keep control flow minimal.
- **Implementation preference.** Favor a small, unsurprising implementation
  over a highly abstract framework. The most important properties are:
  deterministic ordering; explicit list behavior; strict validation; good
  diagnostics; inspectable provenance; a renderer with no domain knowledge;
  straightforward Git review. Avoid "smart" merge behavior that guesses
  intent.

## Supported formats

Fragments, and the documents outputs render to, may each independently be
**YAML**, **TOML**, or **JSON**. A single composition may draw fragments from
all three interchangeably, and any output may be rendered as any of the
three, independent of what its inputs were.

"Supported" means two separate things:

- **Supported as input** (fragments): the tool parses a file of that format
  into its internal data model — plain mappings, lists, strings, numbers,
  booleans, and null — and everything downstream (templating, merge, pointer,
  provenance, validation) is unaffected by which format a fragment came from.
  A construct a format admits that the model cannot hold is a fail-closed
  error naming the file and the JSON Pointer path, never a silent coercion.
- **Supported as output**: the tool serializes the merged document to that
  format deterministically. A value the format cannot express is a
  fail-closed error naming the output and the JSON Pointer path, never a
  coercion.

The tool's own documents — the inventory (`targets.yaml`), module documents
(`fragmint.yaml`), and the secrets overlay (`secrets.yaml`) — are YAML only.
These are authored once per project and read only by fragmint itself, not
data the tool renders, so multi-format support does not extend to them.

**Mapping keys are always strings.** JSON and TOML require this syntactically
already. YAML alone admits a bare key that resolves to a non-string type —
`1`, `true`, `null`, and the like — and loading one is a fail-closed error
naming the file and the offending key, telling the author to quote it (`'1'`
rather than `1`). A YAML parser resolves a key's type before comparing keys
for uniqueness, so `1` and `true` (or `0` and `false`) are the same mapping
key even though they are spelled differently; coercing every key to a string
after the fact would let `1: a` and `'1': b` collapse into one key, with one
value silently discarding the other.

**Values are strings, numbers, booleans, null, lists, and mappings** — that
list is exhaustive. YAML alone can name a type outside it, and only through an
explicit tag: `!!binary` (bytes), `!!set`, and `!!pairs` are each a fail-closed
error naming the file, the JSON Pointer, and how to write the value instead.
(`!!omap` is fine; it loads as an ordinary mapping.) A plain scalar never
resolves to any of these, so nothing an author writes by accident trips this.
Coercing instead of rejecting would smuggle a Python `repr` into the output —
`b'hi'` for `!!binary` — and for `!!set` that text is not even stable between
runs, which would break the byte-identical output every writer promises.

**The independence rule.** The input format constrains what a fragment can
say; the output format constrains what a document can mean. They meet only
through the values in between — a `.toml` fragment and a `.yaml` fragment
expressing the same data are indistinguishable once loaded. Three format-
specific restrictions fall out of this:

1. A fragment that introduces a null (a YAML mapping key with no value, or an
   explicit `value: null`) makes any `format: toml` output that fragment
   feeds impossible to serialize — TOML has no null. TOML fragments cannot
   introduce this problem themselves, since TOML has no null to write either.
2. A fragment that introduces a non-finite float (`.nan`/`.inf` in YAML,
   `nan`/`inf` in TOML) makes any `format: json` output that fragment feeds
   impossible to serialize — JSON has no `NaN`/`Infinity`. JSON fragments
   cannot introduce this problem.
3. A TOML fragment cannot express a date, time, or datetime value — TOML's
   native date/time types have no home in fragmint's data model, and loading
   one fails closed naming the file and the pointer, telling the author to
   quote the value as a string. This makes a TOML fragment strictly less
   expressive than an equivalent YAML fragment, not merely differently
   written: a YAML fragment may write an unquoted timestamp, which loads as
   the string of exactly the characters the file contains
   (`2020-01-02T03:04:05Z` stays `2020-01-02T03:04:05Z`, not some other
   spelling of the same instant) and is quoted on output so no reader retypes
   it; the same value in TOML is a hard error instead. The asymmetry is one
   of parsers, not of policy: both formats treat a timestamp as text, but a
   TOML parser resolves the value and discards the source spelling before
   fragmint sees it, leaving nothing to preserve — so TOML asks the author to
   quote it rather than inventing a spelling on their behalf.

Type-fidelity matrix — every row reflects an actual, checked behavior of the
concrete writer for that format:

| Value in the merged document | YAML | JSON | TOML |
|---|---|---|---|
| null | `a:` (empty scalar) | `null` | **error**, names the pointer |
| `nan` / `inf` / `-inf` | `.nan` / `.inf` / `-.inf` | **error**, names the pointer | `nan` / `inf` / `-inf` |
| string containing `\r` | verbatim | escaped `\r` | single-line string (see below) |
| string containing `\n` | block literal (`\|`) | escaped `\n` | multi-line string (`"""`) |
| empty mapping `{}` | `a: {}` | `"a": {}` | `[a]` (empty table) |
| empty list `[]` | `a: []` | `"a": []` | `a = []` |
| list of mappings | block sequence | array of objects | array of tables `[[a]]` |
| whole document empty | `{}` | `{}` | (empty file) |
| mapping insertion order | preserved | preserved | preserved **only among a table's own scalar keys** — see below |

TOML has two format-specific wrinkles, both deterministic and both documented
here rather than worked around:

- **Table ordering.** TOML's syntax requires that once a `[sub]` header opens
  a table, every following `key = value` belongs to `sub` — so a table's own
  scalar keys must all precede its sub-tables and arrays-of-tables in the
  emitted text, regardless of the order they were inserted in relative to
  each other. Within one table, keys holding scalars, arrays of scalars, and
  inline values keep their insertion order; that table's sub-tables and
  arrays-of-tables follow, themselves in insertion order relative to each
  other. This is a requirement of TOML's grammar, not a choice — fragmint
  never sorts keys, in any format.
- **Multi-line strings and `\r`.** TOML's `"""`-delimited multi-line strings
  normalize `\r\n` on parse, which would silently change a string containing
  a bare `\r`. To stay lossless, a TOML output uses `"""` multi-line strings
  when no string value in the document contains `\r`; if any does, every
  multi-line string in that document falls back to a single-line, escaped
  form instead. The choice is a pure function of the data, so identical data
  always produces identical bytes.

## Installation

Requires Python 3.12+. Either tool works; pick whichever you already use. Both
give you the same `fragmint` command. `fragmint` is not on PyPI yet, so the
commands below install from a Git checkout or the repository URL.

**With [uv](https://docs.astral.sh/uv/).** Install it as a standalone tool,
which puts `fragmint` on your `PATH` in its own isolated environment:

```bash
uv tool install git+https://github.com/chpatton013/fragmint
fragmint --help
```

Or skip installing altogether and run it straight from the repository —
convenient in CI, or for a one-off render:

```bash
uvx --from git+https://github.com/chpatton013/fragmint fragmint --help
```

Both accept a local path in place of the URL (`uv tool install .`,
`uvx --from . fragmint ...`), which is what you want while editing fragments
in a checkout.

**With pip.** Nothing about `fragmint` requires uv; a plain virtualenv is
fine:

```bash
python -m venv .venv && source .venv/bin/activate
pip install git+https://github.com/chpatton013/fragmint
```

Or `pip install -e .` from a checkout, to run against your working tree. For
the full contributor setup — the same thing plus the test, lint, and
type-check tooling — see "Development" at the end of this file.

## Repository structure

`fragmint` the tool (`src/fragmint/`, including `src/fragmint/schemas/`) is
separate from any particular project that uses it. A project is an **import
closure**: the inventory (the render root) plus every module it imports,
transitively (see "The module model" below). Nothing about a project's
internal layout is baked into the tool; it can be invoked from anywhere via
`--inventory path/to/targets.yaml`. Every path a document declares is resolved
relative to *that document's own directory*, not the caller's working
directory — that's what makes a module portable and relocatable
independently of whatever inventory imports it.

This repository ships one such project, the Ubuntu autoinstall example, under
`example/`. Its inventory imports two modules — `autoinstall` (which owns the
`user-data`/`meta-data` outputs) and `ansible` (which owns the aggregate
`ansible-inventory` and `ansible-inventory-json` outputs) — to demonstrate
multi-file output, both output scopes, and the module model itself (see "The
module model", "Fragment order", and "Aggregate outputs" below). It also
exercises multi-format support end to end: `ansible-inventory-json` is a
JSON twin of `ansible-inventory` (same fragments, same schema, a different
`format`), and one of the ansible module's own fragments is written in TOML
(see "Supported formats"):

```
example/
├── targets.yaml           The inventory: the render root. Imports, defaults, groups, targets.
├── secrets.example.yaml   Documents the shape of secrets.yaml, which sits here and is git-ignored.
├── fragments/             The inventory's own fragments (site-specific: per-host data).
└── modules/
    ├── autoinstall/       Reusable module: user-data/meta-data outputs, their fragments/template.
    │   ├── fragmint.yaml
    │   ├── fragments/
    │   ├── templates/
    │   └── rendered/      Output (git-ignored).
    └── ansible/            Reusable module: the aggregate ansible-inventory/ansible-inventory-json outputs.
        ├── fragmint.yaml
        ├── fragments/      One fragment (ansible/checks.toml) is TOML, not YAML.
        ├── schemas/
        └── rendered/      Output (git-ignored).
```

Fragments, templates, and schemas may live under any nested path within the
directory that provides them; a module may put them wherever it likes. The
tool itself lives alongside the example:

```
src/fragmint/           The package (see "Modules" below).
src/fragmint/schemas/   JSON schemas for documents, fragments, and secrets files.
tests/                   Unit, module/closure, inventory, snapshot, and CLI tests + fixtures.
```

### Modules

| Module          | Responsibility |
|-----------------|----------------|
| `models.py`     | Immutable value objects and shared type aliases. |
| `errors.py`     | Exception hierarchy; each maps to an exit code. |
| `exit_codes.py` | Stable process exit codes. |
| `modules.py`    | Document loading, the import closure, reference resolution, flattening. |
| `formats.py`    | Format boundary: safe load and deterministic serialization for YAML, TOML, and JSON. |
| `pointer.py`    | JSON Pointer parse/get/set/delete (no array indexes). |
| `templating.py` | Strict, sandboxed, type-preserving Jinja substitution. |
| `inventory.py`  | Resolve a target's fragments + vars from the flattened project. |
| `fragments.py`  | Load/validate a fragment file at an already-resolved path. |
| `merge.py`      | The explicit merge operations (incl. `assert`). |
| `sources.py`    | Variable value sources: literal, secret, subprocess capture. |
| `provenance.py` | Track which fragment/op last set each path; override warnings. |
| `validation.py` | Generic doc checks (marker/schema) + external validators. |
| `render.py`     | Orchestration: render, serialize, output template, atomic write. |
| `cli.py`        | `click` command surface and error→exit-code mapping. |

Custom exception types (`errors.py`), each mapped to a stable exit code:

```text
ModuleError
InventoryError
FragmentError
AmbiguousFragmentError
TemplateRenderError
MergeConflictError
AssertionFailedError
ValidationError
SerializationError
VariableResolutionError   (base for SecretNotFoundError, CaptureError)
UnknownTargetError
UnknownFragmentError
```

Every user-facing error includes enough context to find the source file and
operation — but never the value of a secret or a secret-sourced argument.

## The module model

There are two kinds of document, sharing one schema
(`schemas/document.schema.json`) and almost all of their shape:

| Capability | Module (`fragmint.yaml`) | Inventory (the root) |
|---|---|---|
| Declare input dirs (`fragments_dir`, `templates_dir`, `schemas_dir`) | yes | yes |
| `imports` other modules under local names | yes | yes |
| Define `outputs` (output files) | yes | yes |
| Attach fragments to any output in the closure (`defaults`) | yes | yes |
| Define `groups` (variables + per-output fragments) | yes | yes |
| Define `validators` | yes | yes |
| Declare an aggregate prologue/epilogue and aggregate variables (`aggregate`) | yes | yes |
| Define `targets` | **no** | **yes — only here** |

A module is "everything except targets." The **inventory** is the render
root: the same shape, plus `targets`, and it is the only document a module
may not itself reference. `fragmint` is invoked on the inventory (`--inventory
PATH`, defaulting to `.`); it pulls in modules, not the other way around.
Resolution: a value naming a **directory** looks for `<dir>/targets.yaml`; a
**file** is used as given. A directory with no `targets.yaml`, or a missing
file, is a fail-closed error naming the path it tried.

```yaml
# modules/ansible/fragmint.yaml — a reusable module
version: 1
outputs:
  ansible-inventory:
    scope: aggregate
    path: "rendered/inventory.yaml"
    schema: ansible-inventory.schema.json   # module-relative (schemas_dir inferred)
defaults:
  outputs:
    ansible-inventory:
      fragments: [host]        # this module's own fragments dir (inferred)
groups:
  gb10:
    variables: { ansible_group: gb10 }
```

```yaml
# targets.yaml — the inventory: the render root
version: 1
imports:
  ansible: modules/ansible
  autoinstall: modules/autoinstall
groups:
  gb10:                        # merges with the ansible module's `gb10` group
    variables: { hardware_model: gb10 }
targets:
  gb10-01:
    groups: [gb10]
    variables: { hostname: gb10-01 }
```

### Imports, names, and the closure

`imports` maps a local name to a module directory, resolved relative to the
importing document. Importing a module **transitively imports its own
imports**, and every module anywhere in the closure is addressable by the
name it was first bound to — one flat namespace populated by the whole
closure. Diamonds (the same module reachable through more than one import
path) are fine: a module is loaded and contributes exactly once, at first
encounter.

Fail-closed rules, each naming both offenders (or the cycle path):

- **Name collision.** A name bound to two *different* module directories
  anywhere in the closure is an error.
- **Aliasing.** One module directory bound to two *different* names is an
  error — it would give one fragment file two provenance identities
  (`a:host` and `b:host` for the same file), making `explain` untrustworthy.
  Importers must agree on one name.
- **Cycles.** The import graph must be a DAG.

### Directory inference

When `fragments_dir`/`templates_dir`/`schemas_dir` are unset, `./fragments/`,
`./templates/`, and `./schemas/` are inferred relative to the document.
**Inference is conditional on existence**: an inferred directory that doesn't
exist simply means the document provides no directory of that kind, and a
reference needing it fails closed naming the document and the kind. An
*explicitly* declared directory that doesn't exist is an error — declaring it
is a claim. That split keeps convention-over-configuration for the common
layout without silently swallowing a typo in an explicit path.

### Outputs and validators across the closure

A project declares one or more **named outputs**, each with its own path
pattern + optional text template + optional document schema + default
validators + a **scope** + a **serialization format** — but any document in
the closure may declare or attach to them:

- An output name must be **defined exactly once** in the whole closure — a
  duplicate definition is an error naming both defining documents.
- Any document may **attach fragments** to any output in the closure by name
  (its `defaults.outputs.<name>.fragments`), whether it defined that output or
  not. Attaching to an undefined output name is an error.
- `default: true` — at most one across the whole closure.
- **Validators** may be declared by any document; they union across the
  closure by name, and a duplicate name is an error.
- Any document may **attach an aggregate prologue/epilogue** (its
  `aggregate.outputs.<name>.prologue`/`epilogue`) to any `scope: aggregate`
  output in the closure, whether it defined that output or not — see
  "Aggregate outputs" below. Naming an output undefined anywhere in the
  closure, or one with `scope: target`, is an error naming both the document
  and the output.

`outputs` across the closure must be non-empty. When there's exactly one,
it's the implicit default even without `default: true`; with several, at
most one may set `default: true` (more than one is an error), and with
several but none marked, there's no implicit default — commands that need
exactly one output (like `--stdout` with no `--only`) then require `--only
NAME`.

**`format`.** An output's serialization format is resolved once, at document
load time, in this order: an explicit `format: yaml|toml|json` wins;
otherwise it's inferred from the suffix of `path` — `.yaml`/`.yml` -> yaml,
`.toml` -> toml, `.json` -> json; otherwise it defaults to `yaml`. An unknown
`format:` value is a config error naming the output and the accepted values.
Inference reads `path` **as written, before `{target}` substitution** — so a
target legitimately named `web.json` doesn't make an extension-less
`path: "rendered/{target}"` output secretly become JSON for that one target;
format is a fixed property of the output, exactly like `scope` and `schema`,
checked once regardless of which target renders it. `--output PATH`
redirects where the bytes land; it does not reinterpret what they are — it
never changes an output's format.

```yaml
outputs:
  user-data:
    path: "rendered/{target}/user-data"   # no suffix -> yaml
  api-manifest:
    path: "rendered/{target}/manifest.json"   # .json -> json
  agent-config:
    path: "rendered/{target}/agent.conf"      # unknown suffix -> yaml
    format: toml                              # explicit, wins
```

Everything domain-specific (the `#cloud-config` header, the Subiquity
validator, any document schema) lives in a module, in the inventory, or in
fragments — never in the renderer.

**Portability.** Every path a document declares (`fragments_dir`/
`templates_dir`/`schemas_dir`, and each output's `path`) is resolved
**relative to that document's own directory**, never the caller's current
working directory. An already-absolute path is left unchanged. This is what
lets a module — like `example/modules/autoinstall/` in this repository — be
self-contained and relocatable: it works identically regardless of which
inventory imports it or from where the tool is invoked. `--inventory` and
`--fragments-dir` CLI overrides are the exception: given directly on the
command line, they resolve relative to the CWD like any ordinary CLI argument.

### Reference resolution

One rule, three ref sites: **a value containing `:` is module-qualified; a
value without `:` is relative to the declaring document's own module.**

| Where | Bare value | Module-qualified |
|---|---|---|
| `fragments: [...]` | ref under the declaring document's `fragments_dir` | `ns:path` under module `ns`'s `fragments_dir` |
| output `template:` | under the declaring document's `templates_dir` | `ns:name.tmpl` |
| output `schema:` | under the declaring document's `schemas_dir` | `ns:name.json` |

Note "the *declaring* document" — a fragment ref written in module `ansible`
resolves against `ansible`'s own dirs, not the inventory's. That's what makes
a module relocatable and independently ownable. After resolution, a resolved
path must remain **inside the module directory it resolved through** — an
escaping reference (e.g. `../../etc/passwd`) fails closed rather than
silently reaching outside the module's own tree.

**A fragment reference is extension-less and format-independent.** It
resolves to exactly one of `<ref>.yaml`, `<ref>.toml`, or `<ref>.json` under
the resolved `fragments_dir` — matching none is a fail-closed error naming
every path tried; matching more than one is a fail-closed error naming the
reference and every file it matches. There is no preference order between
formats — an ambiguity is never resolved by silently picking one, so a
project cannot come to depend on that. `.yml` is deliberately **not** a
fragment candidate suffix (it *is* accepted for output-path format
inference, where no such ambiguity is possible), so a project with both
`base.yaml` and `base.yml` under a `fragments_dir` keeps resolving to
`base.yaml` exactly as it always has. A stray `.json` or `.toml` file that
happens to sit under a `fragments_dir` for unrelated reasons becomes a
visible fragment (`list fragments` will show it) and can create a new
ambiguity with a same-stemmed `.yaml` fragment — worth knowing before adding
non-fragment files there.

**Display.** A resolved reference displays *bare* only when it resolves to
the root document (the inventory); it displays *qualified* (`ns:path`)
otherwise — including a reference *written* bare inside a module, since its
display should still say which module it came from. `explain`, `inspect`,
`list fragments`, override warnings, and error messages all show this same
qualified form.

## Authoring inventory

`targets.yaml`, the inventory (the render root; e.g. `example/targets.yaml`),
declares `imports`, `defaults`, `groups`, and `targets` — the same shape as a
module, plus `targets`. **Which fragments feed which output is declared per
output**, at any layer in any document of the closure — `outputs` at each
layer maps an output name to that layer's ordered fragment contribution
(bare or module-qualified references; see "Reference resolution"). Variables
are *not* per-output — they're a single shared layer across all of a target's
outputs.

```yaml
version: 1

imports:
  autoinstall: modules/autoinstall
  ansible: modules/ansible

defaults:
  variables:
    identity_username: chris
    ssh_import_id: gh:chpatton013
    locale: en_US.UTF-8
    keyboard_layout: us

groups:
  # Merges with the same-named group the autoinstall/ansible modules ship
  # (README.md "Composition and ordering"): this inventory adds the
  # site-specific hardware_model, the modules add fragments/variables of
  # their own.
  gb10:
    variables:
      hardware_model: gb10

targets:
  gb10-01:
    groups:
      - gb10
      - general_servers
    outputs:
      user-data:
        fragments:
          - hosts/gb10-01
          - autoinstall:autoinstall/checks
    variables:
      identity_hostname: gb10-01
      identity_password_hash: "$6$example-salt$example-hash"
      primary_interface: enP7s7
```

An `aggregate:` block, legal in any document in the closure (a module's
`fragmint.yaml` or the inventory), attaches fragments that run once for a
`scope: aggregate` output — before any target contributes (`prologue`) or
after every contributing target has (`epilogue`) — plus variables scoped to
that composition, independent of any target (see "Aggregate outputs" below):

```yaml
# modules/ansible/fragmint.yaml
aggregate:
  variables:
    ansible_ssh_user: chris          # closure-wide, target-independent
  outputs:
    ansible-inventory:
      prologue: [ansible/skeleton]   # before any target contributes
      epilogue: [ansible/checks]     # after every target has contributed
```

A **target** is any named thing you want to render a document for (a machine,
an environment, a service — the renderer does not care). Groups are an
optional, generic reuse mechanism: a named bundle of variables and per-output
fragments a target can pull in. A target name must not contain `/` or `~` —
rejected at inventory load — since it would corrupt a JSON Pointer once
substituted into a templated operation path (see "Paths" and "Aggregate
outputs").

### Fragment order

For each output name, the final ordered fragment list for a target is the
concatenation of:

1. every module's `defaults.outputs.<name>.fragments`, in closure order
   (dependencies before their importers; see "Composition and ordering");
2. the inventory's own `defaults.outputs.<name>.fragments`;
3. for each group the target lists, in the target's group order — and within
   a single group name, each contributing document's fragments in closure
   order, inventory last (a group name defined by several documents merges;
   see "Composition and ordering");
4. the target's own `outputs.<name>.fragments`.

Precedence is purely positional: entries later in this resolved list override
earlier ones, within that output. The renderer never reorders. An output name
that no layer contributes fragments to is simply **not produced** for that
target — there's no empty file. For the example project, `gb10-01`'s resolved
`user-data` fragment order is:

```text
autoinstall:autoinstall/base
autoinstall:autoinstall/default-user
autoinstall:ubuntu-24.04
autoinstall:hardware/gb10
autoinstall:roles/general-server
hosts/gb10-01
autoinstall:autoinstall/checks
```

and its `meta-data` order is just `autoinstall:meta/instance-id` (contributed
once, by the `autoinstall` module's own `defaults`).

### Composition and ordering

Order is the *only* thing that determines precedence, and the renderer never
reorders — the tool's strictest guarantee, extended here across the whole
closure rather than one file.

**Closure order.** Traverse `imports` **post-order, depth-first, in
declaration order**, visiting each module once at first encounter.
Dependencies therefore contribute *before* their importers, so an importer's
contributions can override what it imports — matching "later in the list
wins" everywhere else in the tool. The inventory, as root, contributes last.

**Variable layering** follows the same shape as fragment order (later wins):
module `defaults` variables in closure order → inventory `defaults` → group
variables (group order, closure order within a group) → target variables →
`--var`. `target` and `output` stay reserved and injected last (see "Variable
precedence" below).

**Group merging.** A group name defined in several documents merges rather
than conflicting: variables layer, fragments concatenate, both in closure
order. This is the mechanism that lets a module ship a reusable `gb10` group
and the inventory add site-specific variables to it. A group referenced by a
target but defined nowhere in the closure remains an error.

### Variable precedence

Variable *definitions* are layered in this order (later wins):

1. every module's `defaults.variables`, in closure order;
2. the inventory's own `defaults.variables`;
3. group variables, in the target's group order;
4. target variables;
5. CLI overrides (`--var KEY=VALUE`, always literal strings).

```bash
fragmint render gb10-01 --var identity_hostname=test-gb10
```

A later layer's definition fully replaces an earlier one — including
replacing a literal with a source or vice versa. Layering happens first; a
definition may be a literal or a `from:` source (see "Variable value sources"
below). Resolution of sources happens after layering, at first use during
templating — see "Resolution timing". Secrets are not a precedence layer;
they are a named store referenced explicitly.

**Reserved: `target`, `output`.** After layering, `target` is always set to
the current target's own name, so any fragment can reference it via `{{
target }}` — shared across every output the target produces, same as any
other variable. `output` is reserved the same way, but is set to the current
*output's* own name (e.g. `user-data`, `meta-data`) rather than the target's —
since a target's fragment list, and therefore the value of `{{ output }}`, is
independent per output, this is set once per output during rendering rather
than once per target like `target` is. Because both are set unconditionally
after every other layer (including CLI overrides), defining a variable named
`target` or `output` anywhere (`defaults`, a group, the target itself, or
`--var target=...`/`--var output=...`) is a fail-closed `InventoryError`
rather than being silently discarded.

### The aggregate scope

A prologue/epilogue fragment (see "Aggregate outputs" below) does not run for
any one target, so it gets its own, smaller variable map — two layers, later
wins:

1. every module's and the inventory's `defaults.variables`, in closure order;
2. every `aggregate.variables`, in closure order.

Group variables, target variables, and `--var` are **not** in scope here —
all three are target-scoped by definition, and this composition is not.
`output` is set, the same as everywhere else, to the current output's own
name. `target` is deliberately **not** set: this scope runs once, outside the
per-target loop, so there is no single target to name. A prologue/epilogue
fragment referencing `{{ target }}`, or declaring `requires: variables:
[target]`, therefore fails closed with the usual strict-undefined error.
Defining `target` or `output` in either layer is the same fail-closed
`InventoryError` as in the per-target scope. This scope's variables resolve
on demand, the same as the per-target scope (see "Resolution timing"): an
aggregate output with no prologue or epilogue never touches this scope at
all, and one that has either never resolves a secret or capture its
prologue/epilogue fragments don't reference. A secret/capture failure here
reads `aggregate scope: variable NAME: ...` rather than naming a target,
since there is none.


## Aggregate outputs

Every output described so far has `scope: target` (the default): one document
per target. An output declared with `scope: aggregate` instead produces a
**single document for the whole run**, composed from every target that
contributes to it — useful for something like an Ansible inventory, which
needs one file describing every host, not one file per host.

```yaml
# modules/ansible/fragmint.yaml — owns the output AND opts every target in
outputs:
  ansible-inventory:
    scope: aggregate
    path: "rendered/inventory.yaml"   # a single fixed path — never {target}
defaults:
  outputs:
    ansible-inventory:
      fragments: [host]               # this module's own fragments dir
```

```yaml
# modules/ansible/fragments/ansible/host.yaml — the target name lives in the PATH, not a value
operations:
  - op: merge
    path: "/all/children/{{ ansible_group }}/hosts/{{ target }}"
    value:
      ansible_host: "{{ ansible_host }}"
```

This is the example project's real `ansible-inventory` output (see "Example
project" below); rendering it produces one file describing every configured
host:

```yaml
all:
  children:
    gb10:
      hosts:
        gb10-01: { ansible_host: 10.0.0.11 }
        gb10-02: { ansible_host: 10.0.0.12 }
    generic_vm:
      hosts:
        generic-vm-01: { ansible_host: 10.0.0.21 }
```

Aggregate scope reuses every other mechanism in this tool:

- **Routing and opt-in work exactly like any other output.** A target
  contributes to an aggregate output through the same
  `outputs.<name>.fragments` at the `defaults`/group/target layers. A target
  contributing no fragments for the aggregate output contributes nothing to
  the document — that's its opt-out. If *no* target contributes, the output is
  not produced at all (same rule as "Fragment order": an output nothing feeds
  is never written) — and in that case a prologue/epilogue is skipped
  entirely too; declaring one does not, by itself, make an output produced.
  Putting a fragment in `defaults` opts every target in at once; putting it
  in a group opts in that group's members.
- **Ordering is purely positional**, applied across a prologue and an
  epilogue as well as across targets, and across targets as well as within
  one:
  1. This output's **prologue** — every document's
     `aggregate.outputs.<name>.prologue`, in closure order (dependencies
     before their importers, the inventory last, same as everywhere else in
     this tool) — applied once, before any target contributes.
  2. **Targets**, in inventory declaration order — never sorted, exactly like
     every other targets-in-order rule in this tool.
  3. **Fragments within a target**, in that target's resolved order for the
     output (defaults -> groups -> target), exactly as for a per-target
     output.
  4. This output's **epilogue** — every document's
     `aggregate.outputs.<name>.epilogue`, in closure order — applied once,
     after every contributing target.

  Precedence remains purely positional across all four: a target's write
  overrides a prologue write at the same path, and an epilogue's write
  overrides any target's; the renderer never reorders — including never
  sorting hosts in the output. If you want hosts alphabetized, order the
  inventory. Closure order also means a dependency's prologue precedes its
  importer's, and the inventory's — as root, last in closure order — is last
  in both the prologue and the epilogue.
- **Templated operation paths put the target where it belongs: in a
  position, not a value.** `{{ target }}` (and any other variable) may appear
  in an operation's `path`, not just its `value` — see "Paths" below. This is
  what lets one fragment, applied once per contributing target, write each
  target under a distinct key of the same shared document.
- **Serialization, templating, and writing work identically.**
  `compose_output`, `write_output`, and validators all operate on a
  `RenderedOutput` plus an `OutputSpec` exactly as they do for a per-target
  output — a `template:` on an aggregate output works exactly as it does
  per-target (e.g. a header comment).
- **Provenance and override warnings identify the contributing target.**
  Because the same fragment applies once per contributing target,
  "`ansible:host` operation 0" alone does not identify a single write. Every
  provenance entry produced while applying a target's own fragments during
  aggregate rendering carries that target's name (`None` for per-target
  rendering, where it would be redundant); an epilogue entry also carries no
  target — it runs once, outside the per-target loop, so no single target
  identifies it either. `explain` shows the target as a parenthetical when
  there is one, so a `source:`/`contributors:` line *without* a `(target
  NAME)` parenthetical in an aggregate output's section is precisely an
  epilogue write. An override warning names both the previous and the new
  source, with a target parenthetical on whichever side has one — which, for
  two targets colliding, is exactly the "two targets claimed the same host
  key" bug you want reported:

  ```text
  [ansible-inventory] warning: ansible:host operation 0 replaced
  /all/children/gb10/hosts/gb10-01/ansible_host
    previous source: ansible:host (target gb10-01)
    new source: ansible:host (target gb10-01-dup)
  ```

**The partial-document assertion caveat.** An `assert` operation inside a
fragment contributing to an aggregate output *through a target* runs during
*that target's* turn in the loop, so it only ever sees the **partial**
document built so far (through the current target) — never the finished,
whole-run document. This is a real footgun: a trailing fragment whose whole
job is to assert the fully composed document is correct (the idiom the
`autoinstall` module's own `checks` fragment uses for `user-data`) does
**not** work when placed among a target's own fragments for an aggregate
output, because there is no single target whose "last fragment" runs after
every other target.

**Prologue and epilogue: whole-document fragments.** An `aggregate:` block —
legal in any document in the closure — attaches fragments that run once,
outside the per-target loop, on the same shared document:

```yaml
aggregate:
  variables:
    ansible_ssh_user: chris          # closure-wide, target-independent
  outputs:
    ansible-inventory:
      epilogue: [checks]             # after every target has contributed
```

An **epilogue** fragment is the remedy for the caveat above: it runs once,
after every contributing target, so its `assert` operations see the finished
document — this is what lets a project ship the same trailing-assertion
idiom for an aggregate output that a target-scoped output already enjoys.
Epilogue assertions run during composition, not validation, so they are not
suppressed by `--no-validate` for `render`/`render-all`/`validate`/
`validate-all` — only `explain` skips them, and it does so unconditionally,
not via `--no-validate` (see "Provenance and `explain`"). An aggregate
output's `schema` and any named
validators remain the right tool for whole-document *shape* checks; an
epilogue assertion is the right tool for a whole-document *fact* a schema
cannot state, such as a specific group actually being present in the
composed document.

An aggregate prologue/epilogue fragment does not identify a contributing
target — it isn't one — so `{{ target }}` is undefined there (see "The
aggregate scope" above); use `{{ output }}` for the output's own name.
Prologue/epilogue alone, with no target contributing, does not produce the
output — see the not-produced rule below.

`inspect --aggregate` (see "CLI usage") shows this effective order —
concatenated across the closure, with each entry's declaring document — and
the aggregate variable layer, without rendering anything, so it stays usable
even when no target contributes or an epilogue `assert` would fail.

**Load-time rules** (README.md "The module model"): an aggregate output's
`path` must not contain `{target}` (it's a single fixed path, not a
wildcard); an aggregate output can never be `default: true` (`default` exists
solely to disambiguate per-target commands like `--stdout` on `render
TARGET`, which an aggregate output is never the answer to). Both fail closed
when the document declaring the output is loaded.

**CLI surface** (see "CLI usage" for the full command reference): `render
TARGET` silently skips aggregate outputs (they're not a function of one
target); `render TARGET --only <aggregate>` is a config error pointing at
`render-all --only NAME` instead; `render-all`/`validate-all` render/validate
every per-target output for every target and then every aggregate output
once, *unless any target failed*, in which case aggregate outputs are skipped
entirely — a partial aggregate document (missing whatever failed) is worse
than none — and the skip is reported on stderr; `render-all --only NAME`
scopes the whole run to one output, of either scope, which is the fast
iteration loop for authoring an aggregate output; `explain` accepts an
optional TARGET, which may be omitted only when `--only` names an aggregate
output; `list outputs` prints each output's name, scope, and defining
document; `inspect --aggregate` shows the target-less part — effective
prologue/epilogue order with each entry's declaring document, and the
aggregate variable layer — that neither `inspect TARGET` nor `list outputs`
can (see "CLI usage"). `--only NAME` scopes variable resolution too — no other output's
secrets/captures/schema are needed — which is what makes it a usable
authoring loop even when the closure has secrets or captures `NAME` doesn't
consume (see "Resolution timing").

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

A `from: secret` is looked up and a `from: capture` is executed only if some
fragment applied for an output the command actually renders references the
variable in a template — in a `value`, a `path`, or an `assert` clause.
Whether that reference sits inside a branch that is taken at render time is
irrelevant: a name appearing anywhere in a fragment's templates counts. A
declared variable no rendered fragment references is never resolved, so an
unresolvable secret, a broken capture, or an unavailable capture program on
it never fails a render that doesn't need it — this is what makes `--only
NAME` (see "Aggregate outputs" and "CLI usage") a usable fast-iteration loop
even when other outputs need secrets or captures `NAME` doesn't. Each
variable resolves at most once per target for the life of a single command.
`requires: variables:` (see "Authoring fragments") asserts that a variable is
*defined*; it does not force it to resolve.

`inspect` and `explain` do **not** execute captures or reveal secrets — they
show a redacted description such as `<capture: openssl passwd -6 -stdin>` or
`<secret gb10-01_password>`, regardless of whether the variable would
otherwise be demanded. `explain` renders every fragment its selected outputs
apply, so laziness alone would not keep it from executing/revealing what it
templates — redaction is what does that.

## Secrets

Secrets live in an optional, untracked file. It is a flat named store
referenced by `from: secret` sources — not a precedence layer.

```yaml
secrets:
  gb10-01_password: "correct horse battery staple"
```

With no `--secrets`, the tool looks for a `secrets.yaml` beside the inventory
and uses it if it is there — the same convention by which an inventory
*directory* resolves to its `targets.yaml`. So an inventory laid out as

```
inventory/
├── targets.yaml      picked up by --inventory inventory
└── secrets.yaml      picked up with it
```

needs neither flag spelled out:

```bash
fragmint render gb10-01 --inventory inventory
```

That inference is conditional on the file existing: an inventory whose
*rendered* outputs reference no secret renders fine without one — a declared
secret no rendered fragment references is never looked up (see "Resolution
timing"). `--secrets` overrides the
inference and is *not* conditional — naming a file that does not exist is an
error, so a typo fails loudly instead of silently rendering with an empty
store. Like other CLI-supplied paths it resolves relative to the CWD, not to
any document's own directory:

```bash
fragmint render gb10-01 --inventory example/targets.yaml --secrets ~/private/pxe-secrets.yaml
```

A tracked `example/secrets.example.yaml` documents the shape;
`example/secrets.yaml` is git-ignored. The renderer never logs secret values
or writes them to diagnostics; they appear only where a fragment places a
resolved value into the output document.

## Authoring fragments

A fragment declares metadata, optional required variables, and an ordered
list of explicit operations. Fragments may live under any nested path and are
referenced by that path (minus its extension):

```yaml
fragment:
  version: 1
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

`requires: variables:` asserts that each named variable is *defined* for the
target — it does not force that variable's value to resolve. A required
variable this fragment never templates (unlike each of the four above, which
both requires and templates) is still satisfied by being defined; it is
simply never looked up (see "Resolution timing").

A bare fragment reference like `hardware/gb10` resolves to exactly one of
`<fragments_dir>/hardware/gb10.yaml`, `.toml`, or `.json` under the
*declaring* document's own `fragments_dir`; a module-qualified reference
like `autoinstall:hardware/gb10` resolves the same way against module
`autoinstall`'s `fragments_dir` instead (see "The module model" — "Reference
resolution", and "Supported formats" for what happens when more than one
candidate exists).

The fragment above, expressed identically in TOML and JSON — a fragment's
format never affects how it composes (see "Supported formats"):

```toml
[fragment]
version = 1
description = "Configure the default administrative user and SSH access."

[requires]
variables = [
  "identity_hostname",
  "identity_username",
  "identity_password_hash",
  "ssh_import_id",
]

[[operations]]
op = "merge"
path = "/autoinstall"

[operations.value.identity]
hostname = "{{ identity_hostname }}"
username = "{{ identity_username }}"
password = "{{ identity_password_hash }}"

[operations.value.ssh]
install-server = true
allow-pw = false
import-id = ["{{ ssh_import_id }}"]
```

```json
{
  "fragment": {
    "version": 1,
    "description": "Configure the default administrative user and SSH access."
  },
  "requires": {
    "variables": ["identity_hostname", "identity_username", "identity_password_hash", "ssh_import_id"]
  },
  "operations": [
    {
      "op": "merge",
      "path": "/autoinstall",
      "value": {
        "identity": {
          "hostname": "{{ identity_hostname }}",
          "username": "{{ identity_username }}",
          "password": "{{ identity_password_hash }}"
        },
        "ssh": {
          "install-server": true,
          "allow-pw": false,
          "import-id": ["{{ ssh_import_id }}"]
        }
      }
    }
  ]
}
```

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
- A path may itself contain template expressions (see "Template rendering"
  below), e.g. `/all/children/{{ ansible_group }}/hosts/{{ target }}` — this
  is what "Aggregate outputs" needs to put a target's name in a *position*
  rather than a value. The rendered result is validated as a JSON Pointer
  before use: a template that produces a malformed pointer or a non-string
  value fails closed, naming the fragment and operation index, before the
  merge operation ever runs. Templated mapping *keys* are not supported —
  path templating covers the same need, and keeping keys literal keeps
  `explain` paths predictable.

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

Add `overwrite_ok: true` to declare the replacement intentional and suppress
just that operation's warning (the replacement is still recorded and visible
via `explain`):

```yaml
- op: set
  path: /autoinstall/kernel
  value: { package: linux-generic-hwe-24.04 }
  overwrite_ok: true
```

**`merge`** — recursively merge one mapping into another mapping. Both the
existing target and incoming value must be mappings; nested mappings merge
recursively; scalar values are replaced (also emitting an override warning,
suppressible the same way with `overwrite_ok: true` on the `merge`
operation). Lists are **not** implicitly merged — if a list already exists
at a path and the incoming mapping contains a list at the same path, the
operation fails unless the list value is identical. Fragments that need to
modify lists must use `append`, `prepend`, `set`, or a removal operation
explicitly.

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
message naming the render scope (the target, or an aggregate prologue/
epilogue label), fragment, operation index, path, and the expectation.

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
plus an override warning — unless the later operation sets
`overwrite_ok: true`, in which case the same replacement happens silently.

Two `append` operations to `/autoinstall/user-data/write_files` concatenate
the entries — the renderer never merges list entries by a key field; to
replace the whole list, use `set`.

### Template rendering

Fragment values support strict Jinja-style substitution:

```yaml
hostname: "{{ identity_hostname }}"
```

Undefined-variable behavior is strict — a missing variable produces an error
naming the render scope (the target, or an aggregate prologue/epilogue
label), fragment, operation index, and missing variable name (it never
silently substitutes an empty string). Templates may appear in mapping
values, list values, multiline strings, operation paths, and assertion
values; they must not dynamically create new YAML structure by returning YAML
text — rendering occurs on already-parsed scalar strings. A templated
operation path is additionally validated as a JSON Pointer after rendering
(see "Paths"), since a template could produce a malformed pointer or a
non-string result.

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

A `RenderSession` (one per run — a single CLI invocation, or an aggregate
output, which by construction visits every target) loads the import closure
and flattens it once (README.md "The module model"), then memoizes, for the
life of the session: each target's layered variable definitions; each
variable's resolved value, on demand and per `(target, variable)` — so a
capture subprocess runs at most once per target regardless of how many
outputs (per-target or aggregate) demand the variable it produces, and a
variable no rendered output's fragments reference is never resolved at all
(see "Resolution timing"); and each loaded fragment (keyed by its resolved
reference, so two modules may both contain a same-named fragment without
colliding), so a fragment shared by many targets is read and schema-validated
once, not once per target.

**Per-target output** (`scope: target`, the default). For each requested
target:

1. Load the inventory's whole import closure and flatten it into its
   `outputs`/`validators`/`defaults`/`groups`/`targets` (README.md "The
   module model").
2. Resolve defaults, groups, and the target definition into a per-output
   fragment list (README.md "Fragment order") and one shared, ordered variable
   map. Every output name the resolved routing references must exist in the
   closure's `outputs` (a fail-closed error otherwise).
3. For each `scope: target` output the target produces, independently
   (`scope: aggregate` outputs are skipped here — see "Aggregate outputs"):
   1. Load every referenced fragment and validate it against the fragment
      schema.
   2. Resolve exactly the variables those fragments reference (plus the
      reserved `output` name) — see "Resolution timing".
   3. Start with an empty document.
   4. For each fragment, in order: verify required variables (against the
      target's *defined* variable names, not the resolved map — a required
      variable need not be templated); render templates (including the
      operation's `path` — see "Paths") using the resolved variable map;
      apply operations in listed order, recording provenance for every
      changed path; evaluate `assert` operations as they're encountered.
   5. Run generic structural validation (unresolved-marker check; optional
      output-specific document schema).
   6. Serialize deterministically in the output's format.
   7. If the output has a template, inject the serialized document into it
      (replacing `{{ document }}`); otherwise use the serialized document
      directly.
   8. Write the result to that output's configured path (`{target}`
      substituted), using a temporary file and atomic rename.
   9. Run any selected validators against the written output.

An output with no contributing fragments for a target is never produced for
that target — no empty file is written. The renderer creates exactly the
output file(s) the closure declares for a given target — it never forces
companion files beyond what the closure routes fragments to.

**Aggregate output** (`scope: aggregate`) — a second, small composition loop
over the *whole run* instead of one target: each contributing target's
resolved fragment list, in inventory declaration order, applied to one shared
document; then this output's `aggregate.outputs.<name>.epilogue`, applied
once, after every contributing target has. See "Aggregate outputs" above for
the full ordering rules and the partial-document assertion caveat. Epilogue
`assert` operations run as part of this composition step, not the generic
validation step below, so they are unaffected by `--no-validate`. Generic
validation, serialization, output templating, writing, and validators are
otherwise identical to the per-target case above — only how the document
itself gets built differs.

## Provenance and `explain`

The renderer tracks which fragment and operation last modified each path,
independently per output. `explain` prints one `== <output name> ==` section
per output the target produces (or just the one named by `--only`). `TARGET`
is optional: it may be omitted only when `--only` names an `scope: aggregate`
output, since an aggregate output isn't a function of one target; naming
`TARGET` together with an aggregate `--only` is the same config error as
`render TARGET --only <aggregate>` (see "Aggregate outputs"). If no target
contributes to that aggregate output it is not produced at all, so there is no
provenance to print: `explain` says so on stderr, leaves stdout empty, and
exits zero.

`explain` composes the document without evaluating `assert` operations, so it
can report provenance for a document that fails its own checks — exactly the
case where you most need to see where a value came from. Since `assert` never
modifies the document or contributes provenance (it only verifies), skipping
it changes nothing for a closure whose assertions pass; the only observable
effect is that a failing assertion no longer prevents the report. This differs
from `--no-validate`, which skips only generic structural validation and
never affects assertions in any command — see "Aggregate outputs" and
"Validation" below.

```bash
fragmint explain gb10-01
fragmint explain gb10-01 --path /autoinstall/storage
fragmint explain gb10-01 --only meta-data
fragmint explain --only ansible-inventory
```

```text
== user-data ==

/autoinstall/kernel/package
  value: linux-generic-hwe-24.04
  source: autoinstall:hardware/gb10 operation 0

/autoinstall/user-data/packages
  contributors:
    - autoinstall:roles/general-server operation 0

== meta-data ==

/instance-id
  value: gb10-01
  source: autoinstall:meta/instance-id operation 0
```

Fragment names in provenance are always the qualified reference (see "The
module model" — "Reference resolution"): bare only when the fragment lives in
the inventory itself, module-qualified otherwise — even for a fragment
referenced bare from within its own module.

For an aggregate output, every `source:`/`contributors:` line carries a
`(target NAME)` parenthetical, since the same fragment applies once per
contributing target and a fragment/operation-index pair alone does not
identify a single write:

```text
== ansible-inventory ==

/all/children/gb10/hosts/gb10-01/ansible_host
  value: 10.0.0.11
  source: ansible:host operation 0 (target gb10-01)
```

Per-target rendering never shows this parenthetical — there's only one
target in play, so it would be redundant.

## Conflict reporting

When a `set` (or a `merge`'s scalar replacement) replaces an existing value,
a warning is emitted by default, prefixed with the output name it occurred in:

```text
[user-data] warning: autoinstall:hardware/gb10 operation 0 replaced /autoinstall/kernel
  previous source: autoinstall:ubuntu-24.04
  new source: autoinstall:hardware/gb10
```

Two ways to suppress it, depending on scope: `--quiet-overrides` silences
every override warning for the run, useful when iterating; adding
`overwrite_ok: true` to a specific operation instead declares *that*
replacement intentional and silences only its warning, leaving others
visible — prefer this when you know a fragment is meant to replace a
specific earlier value, so an *unexpected* override elsewhere still
surfaces. Either way the replacement itself still happens and is still
visible via `explain`; only the warning is suppressed.

For an aggregate output, the same warning also names the contributing
targets, turning it into a duplicate-target detector — exactly the "two
targets claimed the same host key" bug you want reported (see "Aggregate
outputs"):

```text
[ansible-inventory] warning: ansible:host operation 0 replaced
/all/children/gb10/hosts/gb10-01/ansible_host
  previous source: ansible:host (target gb10-01)
  new source: ansible:host (target gb10-01-dup)
```

A prologue or epilogue write involved in the same warning names only the side
that has a target — a target overriding a prologue write, or an epilogue
overriding a target's write, shows the parenthetical on the target side only:

```text
[ansible-inventory] warning: ansible:host operation 0 replaced /all/hosts/gb10-01
  previous source: ansible:skeleton
  new source: ansible:host (target gb10-01)
```

A `merge` encountering an incompatible type fails instead:

```text
cannot merge mapping into list at /autoinstall/user-data/packages
  existing value from: autoinstall:roles/general-server
  incoming value from: hosts/gb10-01
```

## Validation

There are five clearly separable validation concerns. Only the first three
are built into the renderer; the last two are supplied by the project.

1. **Input validation (built in).** Every module/inventory document is
   validated against `schemas/document.schema.json` plus structural rules
   (supported version; unique target/group names; referenced groups exist
   somewhere in the closure; fragments are lists of strings; variables are
   mappings; order preserved; only the root document may declare `targets`;
   an output name is defined exactly once across the closure; a duplicate
   validator name is rejected). Fragments are validated against
   `schemas/fragment.schema.json` plus rules (supported fragment version;
   recognized `op`; valid path; op-appropriate fields present; assertions use
   supported forms). Secrets are validated against `schemas/secrets.schema.json`.
2. **Generic rendered-document validation (built in).** Only document-agnostic
   checks, run independently per output: reject unresolved template markers
   (text containing `{{` or `{%`); if that output's `schema` is set, validate
   its rendered document against that JSON schema. The renderer contains
   **no** hard-coded structural expectations (no `autoinstall.version`, no
   required `identity`/`ssh`/`storage`/`network` rules).
3. **Serialization (built in).** Runs on the rendered document, after generic
   validation and before the file is written: a value the output's `format`
   cannot express (a null for `toml`, a non-finite float for `json`) fails
   closed naming the output and the JSON Pointer path (see "Supported
   formats"). If the output has a `template`, and its format is `toml` or
   `json`, the composed text is re-parsed and required to still equal the
   serialized document — the same fail-closed check, applied to what a
   template might have changed (see "Serialization" below). This step has
   its own exit code (`9`, see "Exit codes"), distinct from generic
   validation failing: the document can be structurally correct and simply
   inexpressible in one particular target format.
4. **Document assertions (project supplied, via fragments).** Any
   document-specific structural requirement is expressed as `assert`
   operations in fragments. The example project's `autoinstall` module ships
   a `checks` fragment asserting the autoinstall structure, and per-hardware
   fragments assert their own additions (e.g. `network.version == 2`).
   Assertions run as part of rendering and fail closed for
   `render`/`render-all`/`validate`/`validate-all`, including with
   `--no-validate` — that flag skips only concern 2 above, never assertions
   (see "Aggregate outputs"). `explain` is the one exception: it composes
   without evaluating assertions, so a failing one can't defeat the
   provenance report (see "Provenance and `explain`"). **For `scope:
   aggregate` outputs**, an `assert` placed among a target's own fragments
   still only ever sees the partial document built so far (through the
   current target), never the finished whole-run document; a whole-document
   assertion instead goes in that output's `aggregate.outputs.<name>.epilogue`
   (see "Aggregate outputs"), which runs once after every contributing
   target. `schema` and named validators remain available for whole-document
   *shape* checks either way.
5. **Named validators (project supplied, external).** Any document in the
   closure may declare named validators; select them with a repeatable
   `--validator NAME` (if none given, the output's default validators run).
   Each runs an external command against the written output file and fails
   on nonzero exit.

```bash
fragmint validate gb10-01 --validator subiquity
```

## Serialization

Every format's output is deterministic and diff-friendly: mapping insertion
order is preserved (never sort keys, subject to each format's own syntactic
constraints — see TOML below), and a file ends with exactly one trailing
newline. Any header such as `#cloud-config` comes from the output template,
not the serializer.

**YAML.**

- two-space indentation;
- never emit Python-specific YAML tags;
- booleans as `true`/`false`;
- quote a string whenever leaving it bare would change its type for a YAML
  1.1 reader — the emitter targets YAML 1.2, but common consumers, including
  cloud-init's PyYAML-based parser, parse YAML 1.1, where values such as
  `no`, `off`, and `12:30` are booleans or sexagesimal integers rather than
  strings. The YAML 1.1 spec decides this, not any one parser's leniency, so a
  value some readers would tolerate bare is still quoted; otherwise leave
  strings unquoted;
- prefer block style for multiline strings.

**JSON.**

- insertion order preserved, never sorted;
- two-space indentation;
- raw UTF-8 (non-ASCII characters are not escaped);
- a non-finite float (`nan`/`inf`/`-inf`) has no JSON representation and
  fails closed naming the JSON Pointer path (see "Supported formats" and
  "Validation").

**TOML.**

- insertion order preserved **within one table**, for its own scalar keys,
  arrays of scalars, and inline values; that table's sub-tables and
  arrays-of-tables follow, themselves in insertion order relative to each
  other. This is a requirement of TOML's grammar — a `[sub]` header commits
  every following `key = value` to `sub`, so a table's scalar keys must
  precede its sub-tables in the emitted text. fragmint never sorts keys in
  any format; this is the one format where insertion order alone cannot
  fully determine the emitted layout.
- two-space array indentation, matching YAML/JSON;
- raw UTF-8;
- a multi-line string uses TOML's `"""` form when the document contains no
  bare `\r` anywhere; if it does, *every* multi-line string in that document
  falls back to a single-line, escaped form instead, since TOML's writer
  controls this choice per document, not per string, and `"""` strings
  normalize `\r\n` to `\n` on parse — the fallback keeps the value byte-exact
  instead of silently changing it. The choice is a pure function of the
  data, so identical data always produces identical bytes.
- null has no TOML representation and fails closed naming the JSON Pointer
  path and the remedy (`op: remove` instead), rather than being dropped or
  coerced (see "Supported formats" and "Validation"). A `remove` operation
  never itself introduces a null — it deletes the key outright — so this can
  only arise from a fragment that explicitly writes one.
- non-finite floats (`nan`/`inf`/`-inf`) ARE representable in TOML 1.0 and
  are written as such.
- a whole-document-empty output is an empty file (`tomli_w` emits an empty
  string for `{}`, and the shared "exactly one trailing newline" rule turns
  that into a single newline byte) — this cannot arise in practice, since an
  output with no contributing fragments is never produced at all (see
  "Fragment order").

**Output templates and non-YAML formats.** A template is format-neutral
text (see "The module model" — "Outputs and validators across the closure"),
not something the renderer parses — which is exactly right for a TOML
comment banner (`#`-prefixed lines are valid TOML) but risky for JSON, which
admits neither comments nor trailing content. For `format: toml` and
`format: json` outputs only, the composed text (after template substitution)
is re-parsed and required to equal the document that was serialized; a
template that breaks parsing or changes the data fails closed naming the
output and the template file. YAML has no such check — a YAML template
inserting non-comment prose already produces a file the tool never re-reads,
and this asymmetry preserves that existing behavior rather than making it a
new error.

## CLI usage

```bash
fragmint render gb10-01                 # -> every per-target output the target produces
fragmint render gb10-01 --only meta-data --stdout   # print one output only
fragmint render gb10-01 --validator subiquity
fragmint render-all                     # every target's outputs, then every aggregate output once
fragmint render-all --only ansible-inventory --stdout   # just the one aggregate output
fragmint validate gb10-01               # render in memory + validate every output
fragmint validate-all
fragmint explain gb10-01 [--path /some/path]   # where did each value come from? (all outputs, or --only)
fragmint explain --only ansible-inventory   # TARGET omitted: only valid for an aggregate --only
fragmint inspect gb10-01                # resolved groups/per-output fragments/vars (redacted)
fragmint inspect --aggregate            # effective prologue/epilogue order + declaring doc, aggregate vars
fragmint inspect --aggregate --only ansible-inventory   # narrow to one aggregate output
fragmint list targets|fragments|groups|outputs|modules
```

All of the above assume `--inventory example/targets.yaml` (or that you've
`cd`'d into `example/` and use the default `.`). Every command accepts
`--inventory PATH`, naming the render root: a directory resolves to
`<dir>/targets.yaml`, a file is used as given, and it defaults to `.` when
omitted (see "The module model"). A `secrets.yaml` beside that root is picked
up automatically (see "Secrets"). Common options: `--fragments-dir`,
`--output` (render/render-all only), `--secrets`, `--var KEY=VALUE`,
`--validator NAME`, `--only NAME`, `--dry-run`, `--quiet-overrides`,
`--no-validate`. `--fragments-dir` takes two forms, never mixed: a bare `DIR`
overrides the root document's fragments directory; repeatable `NS=DIR`
overrides module `NS`'s (an unknown `NS` is an error — it overrides an
already-imported module, it does not declare one). `render-all`/`validate-all`
process every target in inventory order and exit nonzero if any target
fails, without leaving a partially written output file for a failed target
(atomic temp-file + rename).

`list` is a command group: `list targets`, `list fragments` (qualified refs,
by document in closure order, in every supported input format — a stem
matching more than one format is reported as an error rather than silently
listing one), `list groups`, `list outputs` (name, scope, defining
document), and `list modules` (name, path, and the document that first
imported it).

**Multi-output selection (`render`, `validate`, `explain`).** With no
`--only`, `render`/`validate`/`explain` act on every `scope: target` output
the target produces — `scope: aggregate` outputs are silently skipped (no
note on stderr; it would fire on every single-host render). `--only NAME`
scopes any of them to a single named output (an unknown name, or one the
target doesn't produce, is a config error); naming an aggregate output with
`--only` on `render`/`validate` (with a `TARGET`) is a config error pointing
at `render-all`/`validate-all --only NAME` instead — see "Aggregate outputs".
`--only NAME` also scopes which output is actually rendered and validated at
all: a *different* output's secrets, captures, schema, or `assert` never run
and can never fail the command (see "Resolution timing"). Two flags can only
ever apply to one output at a time:

- `--stdout` prints one output's text, in that output's own configured
  format (see "Supported formats"). With `--only`, that's the one printed.
  Without it: a target producing exactly one output prints that one; a
  target producing several falls back to the closure's `default_output`
  (README.md "The module model" — "Outputs and validators across the
  closure"); with several and no default, it's a config error listing the
  available output names.
- `--output PATH` overrides the destination path for one output. It
  redirects where the bytes land; it does **not** reinterpret what they
  are — naming a path ending in `.json` does not make the output JSON if
  its configured format is something else (that's what `format:` on the
  output itself is for; see "The module model" — "Outputs and validators
  across the closure"). It requires either `--only` or a target that
  produces exactly one output — with several and no `--only`, it's a config
  error (there's no default fallback here, unlike `--stdout`, since silently
  picking a path for the "default" output while ignoring the others would
  be surprising for a file-write).

All diagnostics (warnings, errors, progress) go to stderr regardless of
`--stdout`.

**`render-all`/`validate-all --only NAME`.** Scopes the whole run to a
single output, of either scope — the fast iteration loop for authoring an
aggregate output without re-rendering every per-target output too. Naming a
`scope: target` output renders/validates just that output for every target
(no aggregate output runs); naming a `scope: aggregate` output skips
per-target rendering entirely and just composes that one output. `--stdout`
and `--output PATH` on `render-all` are only meaningful (one file,
unambiguous) together with `--only` naming an aggregate output — using
either without that is a config error. If any target fails, aggregate
outputs are skipped entirely (a partial aggregate document is worse than
none) and the skip is reported on stderr; see "Aggregate outputs". Naming a
`scope: target` output needs only that output's own secrets/captures across
every target — not the union of everything every target's outputs consume —
which is what makes `--only NAME` a usable authoring loop even when some
target's *other* output needs a secret or capture `NAME` doesn't (see
"Resolution timing").

`inspect` shows resolved groups, per-output fragment order, and variables,
redacting any variable whose name contains `password`, `secret`, `token`,
`private`, or `credential`, and any variable defined via a `secret` or
`capture` source regardless of name (shown as a non-executing description,
e.g. `<capture: openssl passwd -6 -stdin>`). Pass `--show-secrets` to reveal
literal values (captures are still never executed for inspection).

`inspect --aggregate` covers the target-less part of the aggregate surface
that `inspect TARGET` structurally cannot: every aggregate output's effective
prologue/epilogue order — the closure-order concatenation across every
document that contributes one (see "Aggregate outputs" — "Prologue and
epilogue"), which is not derivable from reading any single document — each
entry shown with the document that declared it (`declared_by`, `inventory`
for the root), plus the aggregate scope's own (redacted) variable layer
("The aggregate scope"). TARGET must be omitted; `--only NAME` narrows to one
aggregate output, and naming a `scope: target` output with it is a config
error. `--var` is rejected outright rather than silently ignored — group,
target, and CLI-override variables are not part of the aggregate scope.
`--show-secrets`/`--secrets` work exactly as they do for `inspect TARGET`.
This mode never renders (no target loop, no prologue/epilogue application),
so it keeps working even when no target contributes to an output or an
epilogue `assert` would fail during a real render.

### Exit codes

`0` success · `1` render failure · `2` usage · `3` target resolution (unknown
target, undefined group, reserved variable name in a target or in the
aggregate scope, unreadable secrets file) ·
`4` fragment validation (including an unresolvable or ambiguous fragment
reference) · `5` merge conflict · `6` rendered-document validation
(generic check, schema, assertion, or validator) · `7` module error (a document
is missing or invalid, or its import closure is inconsistent, including an
unknown output `format:`) ·
`8` variable-resolution failure (secret not found / capture failed) ·
`9` serialization failure (a value the output's format cannot express, or an
output template that makes the composed text invalid in that format — see
"Supported formats" and "Serialization").

Codes `3` and `7` divide along *when* the failure happens: `7` is loading the
documents and their import closure, `3` is resolving a target against the
already-loaded closure.

## Example project: Ubuntu autoinstall

The repository ships a complete example that renders Ubuntu 24.04 autoinstall
`user-data` plus a cloud-init `meta-data` file per target, and a `scope:
aggregate` Ansible inventory across every target — in both YAML and JSON —
demonstrating multi-output routing, both output scopes, and format
independence in practice:

- `example/targets.yaml` is the inventory (the render root): it imports two
  modules, `autoinstall: modules/autoinstall` and `ansible: modules/ansible`,
  and declares only site-specific data — `defaults.variables`, the `gb10`/
  `generic_vm` groups' `hardware_model`, and the three targets.
- `example/modules/autoinstall/fragmint.yaml` owns the `user-data`
  (`template: user-data.tmpl`, which adds the `#cloud-config` header; `path:
  rendered/{target}/user-data`; marked `default: true`; declares the optional
  `subiquity` validator) and `meta-data` (`path: rendered/{target}/meta-data`,
  no template) outputs, their `defaults.outputs.*.fragments` (so every
  importing target's `user-data`/`meta-data` fragments are opted in at once),
  and the `gb10`/`generic_vm`/`general_servers`/`proxmox_hosts`/`docker_hosts`
  groups' fragments.
- `example/modules/ansible/fragmint.yaml` owns two aggregate outputs sharing
  one document: `ansible-inventory` (`scope: aggregate`; `path:
  rendered/inventory.yaml`, a single fixed file for the whole run; `schema:
  ansible-inventory.schema.json` for whole-document shape validation) and
  `ansible-inventory-json` (identical scope, fragments, and schema; `path:
  rendered/inventory.json`, so its format infers to JSON) — a YAML file for
  human review alongside a JSON file for a consumer that expects
  `ansible-inventory --list`-style JSON, from the same fragments (README.md
  "Outputs and validators across the closure": `format` is a fixed property
  of the output, independent of scope, fragments, and schema). Both outputs
  share `defaults.outputs.<name>.fragments: [host]` and
  `aggregate.outputs.<name>.epilogue: [checks]` for a whole-document fact the
  schema cannot express (see "Aggregate outputs" for why this, rather than
  the trailing-assertion-fragment idiom the other two outputs use, is how an
  aggregate output gets that check), and a `gb10`/`generic_vm` group setting
  `ansible_group` — each merges with the same-named group the `autoinstall`
  module and the inventory itself contribute to (README.md "Composition and
  ordering"). Each target sets `ansible_host`, consumed only by
  `ansible:host`.
- `example/modules/autoinstall/fragments/autoinstall/base.yaml`,
  `default-user.yaml`, and `checks.yaml`,
  `example/modules/autoinstall/fragments/ubuntu-24.04.yaml`,
  `example/modules/autoinstall/fragments/hardware/*`,
  `example/modules/autoinstall/fragments/roles/*`, and
  `example/fragments/hosts/*` (the inventory's own — site-specific per-host
  data) build and assert the `user-data` document;
  `example/modules/autoinstall/fragments/meta/instance-id.yaml` builds the
  `meta-data` document (just `instance-id` and `local-hostname`, both from
  `identity_hostname`); `example/modules/ansible/fragments/ansible/host.yaml`
  builds the `ansible-inventory`/`ansible-inventory-json` document, one
  target at a time, using a templated operation *path*
  (`/all/children/{{ ansible_group }}/hosts/{{ target }}`) to place each
  contributing target under its own key of the shared document;
  `example/modules/ansible/fragments/ansible/checks.toml` runs once, as each
  of those outputs' epilogue, after every contributing target, and asserts
  that the `gb10` group the module ships is actually populated in the
  finished document — written in TOML rather than YAML to demonstrate that a
  fragment's own format is independent of any output's (README.md
  "Supported formats").
- Each target's `identity_password_hash` is a `capture` source that runs
  `openssl passwd -6` over the plaintext password held in the secret store
  (`example/secrets.example.yaml` shows the shape).
- No autoinstall (or Ansible) knowledge exists in `src/fragmint/`.

```bash
fragmint render-all --only ansible-inventory --stdout --inventory example/targets.yaml --secrets example/secrets.example.yaml
```

```yaml
all:
  children:
    gb10:
      hosts:
        gb10-01:
          ansible_host: 10.0.0.11
        gb10-02:
          ansible_host: 10.0.0.12
    generic_vm:
      hosts:
        generic-vm-01:
          ansible_host: 10.0.0.21
```

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
list entries; key-aware list merges; variables that reference other
variables; automatic hardware discovery; PXE/TFTP/DHCP configuration;
deployment to HTTP servers; secret-manager integration (the secret store is a
plain file); reimplementing any full document schema (e.g. Subiquity);
running Ansible; installing Ubuntu; multi-format support for the tool's own
documents (the inventory, module documents, and the secrets overlay are YAML
only — see "Supported formats"). The renderer's job is to produce correct,
inspectable structured-document artifacts.

Room for future extension: PXE/iPXE script generation; publishing to an HTTP
directory; secret retrieval from a password manager; target enrollment
states; additional built-in validators; schema-aware validation for multiple
document families (distinct from multi-format *serialization*, which is
already supported); encrypted outputs; fragment deprecation warnings;
fragment dependency declarations; optional fragment conditions; multi-format
support for the inventory/module/secrets documents themselves; a `--format`
override flag that would reinterpret `--output`'s destination rather than
just redirect it.

## Development

The contributor workflow is [uv](https://docs.astral.sh/uv/). One command
creates the virtualenv, installs the package editable, and installs the `dev`
dependency group (pytest, mypy, ruff), pinned by the committed `uv.lock`:

```bash
uv sync
```

Then:

```bash
uv run pytest            # full test suite
uv run mypy src          # type checking (strict)
uv run ruff check .      # lint
```

All three must pass; CI runs exactly these three commands (see
`.github/workflows/ci.yml`). `uv run` re-syncs first, so it picks up a changed
`pyproject.toml` without a separate step. Add or change a dependency with
`uv add` / `uv add --dev`, which updates `pyproject.toml` and `uv.lock`
together — commit both.

`uv.lock` is committed so development and CI resolve identically. It has no
effect on anyone installing the published package: a lockfile is ignored when
a project is consumed as a dependency, so it constrains only this repo's own
environments.

Not using uv is fine — the tooling is ordinary. With pip 25.1+, `pip install
-e . --group dev` installs the same set into an activated virtualenv, and the
three commands above work without the `uv run` prefix. You just won't get the
locked versions.

Tests are organized by concern: `test_merge.py` (merge operations, pointer,
templating, provenance), `test_modules.py` (document loading, the import
closure, reference resolution, flattening), `test_inventory.py` (per-target
precedence and ordering), `test_sources.py` (variable value sources),
`test_render.py` (end-to-end rendering + committed snapshot fixtures under
`tests/fixtures/expected/`), and `test_cli.py` (command surface and exit
codes). Capture tests use a stubbed `CommandRunner` — no test should spawn a
real subprocess.
