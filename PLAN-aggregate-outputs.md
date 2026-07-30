# Plan: aggregate outputs (one file composed from all targets)

Status: draft for review. Not yet implemented.

## Goal

Support rendering a **single file composed from every configured target**, so a
yaml-frag inventory can produce an Ansible inventory alongside the existing
per-target files. Today every output is per-target: `OutputSpec.path` contains
`{target}` and `render_target` composes one document per target.

Target end state — a new output in `example/yaml-frag.yaml`:

```yaml
outputs:
  ansible-inventory:
    scope: aggregate            # NEW: one file for all targets
    path: "rendered/inventory.yaml"
```

routed from the inventory exactly like today's outputs:

```yaml
groups:
  gb10:
    variables:
      ansible_group: gb10
    outputs:
      ansible-inventory:
        fragments: [ansible/host]
```

producing:

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

## Key insight

This is **not** a new command family or a second rendering engine. It is a new
*scope* on an existing named output. Almost all the machinery already exists:

- fragments are already routed per output, per layer, in the inventory;
- variables are already resolved per target;
- merge operations already create missing parents, so a fragment can write
  `/all/children/<group>/hosts/<target>` without a skeleton;
- provenance and override warnings already detect two writers of one path —
  which for an aggregate document is exactly the "two targets claimed the same
  host key" bug you want reported.

The change is: **who owns the document**. Per-target scope = one document per
target, fragments applied once. Aggregate scope = one document for the run,
with each target's resolved fragment list applied into it in turn.

So the core of the implementation is a second, small composition loop, plus
plumbing to keep the existing one honest.

## Prerequisite: operation paths are not actually templated

`README.md` "Template rendering" claims templates may appear in "operation
paths". **They do not today.** `render.py:139-156` renders `op.value` and
`op.assertion` only; `op.path` is passed through verbatim. Nothing in
`tests/` or `example/` exercises a templated path, so the gap is invisible.

Templated mapping *keys* are also unsupported — `templating.render_value`
recurses into `value.items()` and rebuilds with the original `key`
(`templating.py:102-113`).

This matters because the aggregate use case fundamentally needs the target
name in a *position*, not a value:

```yaml
- op: merge
  path: "/all/children/{{ ansible_group }}/hosts/{{ target }}"
  value:
    ansible_host: "{{ ansible_host }}"
```

**Decision: implement path templating first, as its own change.** It is small,
independently useful, already documented as working, and unblocks everything
below. Templated keys stay unsupported (path templating subsumes the need, and
keeping keys literal keeps `explain` paths predictable).

Notes for that change:
- Render `op.path` through `templating.render_value` in `render_target`, then
  coerce to `str` and re-validate as a JSON Pointer (a template could produce
  a malformed pointer, or a non-string — fail closed with the fragment name and
  operation index).
- A target name containing `/` or `~` would corrupt the pointer. Either
  reject such target names in `inventory.load_inventory`, or escape on
  substitution. **Recommend: reject at inventory load** — it is a clearer error
  and the tool already fails closed on ambiguous input.
- Validate before use, so `pointer.set_` never sees a half-rendered path.

## Design decisions

### 1. `scope: target | aggregate` on the output spec

Add an optional `scope` field to each named output, defaulting to `target`
(fully backward compatible).

Rejected alternative: a separate top-level `aggregates:` section in the project
config. It would duplicate `path`/`template`/`schema`/`validators`/routing and
split one concept ("a named output file") into two. Keeping `scope` on
`OutputSpec` means aggregate outputs inherit templates, schemas, validators,
and inventory routing for free.

Config-time rules (enforced in `config.py`, not the schema, per the existing
convention for cross-cutting rules):

- `scope: aggregate` + `{target}` in `path` → `ConfigError`. The file is not
  per-target; a `{target}` placeholder there is a mistake, not a wildcard.
- `scope: target` keeps today's rule (`{target}` required when rendering more
  than one target).
- `scope: aggregate` + `default: true` → `ConfigError`. `default` exists solely
  to disambiguate per-target commands (`--stdout` on `render TARGET`); an
  aggregate output can never be the answer there. *(Minor call — flag for
  review.)*

`resolve_output_path` needs a companion for the aggregate case, or a guard that
rejects an aggregate spec. Recommend a distinct
`resolve_aggregate_output_path(output) -> Path` so the type of path
computation is explicit at each call site.

### 2. Routing and opt-in: unchanged mechanism

A target contributes to an aggregate output through the same
`outputs.<name>.fragments` at the `defaults` / group / target layers. No new
inventory syntax.

Consequences, all of which fall out naturally and should be documented:

- A target contributing no fragments for the aggregate output contributes
  nothing to the document — that is the opt-out.
- If *no* target contributes, the file is not produced (consistent with
  today's "an output with no fragments is not written, no empty file").
- Putting `ansible/host` in `defaults` opts every target in at once; putting it
  in a group opts in that group's members.

### 3. Ordering

Two levels, both positional and deterministic:

1. **Targets** in inventory declaration order (ruamel preserves YAML order;
   `render-all` already iterates `inv.targets` this way).
2. **Fragments within a target** in that target's resolved order for the
   output (defaults → groups → target), exactly as today.

Later writes override earlier ones, so a later target can override an earlier
target's contribution. Never sort — including never sorting hosts in the
output. If a user wants hosts alphabetized, they order the inventory. This is
consistent with "preserve mapping insertion order (never sort keys)".

### 4. Provenance must carry the target

`ProvenanceEntry(fragment, operation_index, operation)` is ambiguous in an
aggregate document: the same fragment is applied once per target, so
"`ansible/host` operation 0" identifies N different writes.

Add `target: str | None = None` to `ProvenanceEntry` (`None` for per-target
scope, where it is redundant). Then:

- `explain` renders it as a parenthetical, only when present:
  `source: ansible/host operation 0 (target gb10-02)`
- Override warnings become genuinely useful for the aggregate case:

  ```
  [ansible-inventory] warning: ansible/host operation 0 replaced
    /all/children/gb10/hosts/gb10-01/ansible_host
    previous source: ansible/host (target gb10-01)
    new source: ansible/host (target gb10-01-dup)
  ```

  This is the duplicate-host detector, for free. `ProvenanceTracker.record`
  needs the target in its warning formatting; the tracker itself stays
  target-agnostic (it just formats whatever the entry carries).

Default the field so no existing construction site breaks.

### 5. Assertions see a partial document

An `assert` operation inside an aggregate fragment runs during *that target's*
application, so it only sees targets processed so far. This is a real footgun
worth documenting prominently, because `autoinstall/checks` has trained the
project's users to put whole-document assertions in a trailing fragment.

Phase 1: document the limitation; whole-document validation for aggregate
outputs goes through the output's `schema` or a named validator, both of which
run on the finished document.

Phase 2 (see below): inventory-level `prologue`/`epilogue` fragment lists.

### 6. Refactor: load the inventory once

`render_target` currently calls `load_inventory` itself, so `render-all`
re-reads and re-validates the whole inventory once per target
(`cli.py:305-314`). Aggregate rendering makes this worse: it needs every
target's resolution in one pass, and a `render-all` producing both per-target
and aggregate outputs would otherwise resolve and re-run captures per target
per scope.

Introduce a session object owning the once-per-run work:

```python
class RenderSession:
    """Config + inventory + secret store loaded once for a whole run."""
    def __init__(self, config, inventory_path, fragments_dir, *,
                 secrets_path=None, runner=None, redact_sources=False): ...

    def resolve(self, target: str) -> ResolvedTarget: ...
    def variables(self, target: str) -> Variables:
        """Layered + source-resolved, memoized so captures run once per target."""
    def render_target_outputs(self, target: str, *, validate=True) -> RenderResult:
        """Today's per-target composition, scope == target outputs only."""
    def render_aggregate(self, output_name: str, *, validate=True) -> RenderedOutput:
        """New: compose one document across all contributing targets."""
```

Memoizing `variables(target)` is the point that keeps captures at one
subprocess per target per run regardless of how many outputs consume them.

Keep `render_target(...)` as a thin wrapper over `RenderSession` so existing
tests and any external callers keep working; it becomes
"build a session, render one target".

## Rendering algorithm (aggregate)

```
render_aggregate(output_name):
  spec = config.outputs[output_name]; assert spec.scope == "aggregate"
  doc = {}; tracker = ProvenanceTracker()
  contributed = False

  for target_name in inventory.targets:                  # declaration order
      resolved = self.resolve(target_name)
      refs = resolved.output_fragments.get(output_name)
      if not refs: continue                              # target opted out
      contributed = True
      vars = dict(self.variables(target_name))           # memoized; incl. `target`
      vars["output"] = output_name
      for ref in refs:
          fragment = load_fragment(fragments_dir, ref)   # cache these too
          check required variables
          for i, op in enumerate(fragment.operations):
              render op.path / op.value / op.assertion with vars
              entry = ProvenanceEntry(fragment.name, i, op.op, target=target_name)
              merge.apply_operation(doc, rendered_op, entry=entry, tracker=tracker)

  if not contributed: return None                        # file not produced
  if validate: check_unresolved_markers(doc); optional spec.schema
  return RenderedOutput(name=output_name, document=doc, ...)
```

Everything after this — `compose_output`, `write_output`, validators — is
unchanged and scope-agnostic, since it operates on a `RenderedOutput` plus an
`OutputSpec`. Worth stating explicitly: **the aggregate path adds no new
serialization, templating, or writing code.** A `template:` on an aggregate
output works exactly as it does per-target (e.g. a header comment).

Fragment loading should be cached per run (`dict[str, Fragment]`): an aggregate
fragment is otherwise re-read and re-validated once per target.

## CLI surface

The unit of rendering stops being strictly "a target". Proposed semantics:

| Command | Behavior |
|---|---|
| `render TARGET` | Per-target outputs only. Aggregate outputs are silently skipped — they are not a function of one target. |
| `render TARGET --only <aggregate>` | `ConfigError`: "output `X` has scope `aggregate`; use `render-all --only X`." |
| `render-all` | Every target's per-target outputs, then each aggregate output once. |
| `render-all --only NAME` | **New flag.** Scope the run to one output (either scope). This is the iteration loop for authoring the Ansible inventory. |
| `validate` / `validate-all` | Mirror the above. |
| `explain [TARGET] --only NAME` | Make `TARGET` optional: omitted is valid only when `--only` names an aggregate output. `TARGET` + an aggregate `--only` is the same error as `render`. |
| `list outputs` | **New (small, optional).** Print each output name and its scope. Cheap discoverability for a config that now has two kinds. |

Silently skipping aggregate outputs in `render TARGET` (rather than erroring)
keeps the common single-host iteration loop quiet, and `render TARGET --only`
gives an explicit, actionable error for anyone who expected otherwise.

`--stdout` and `--output PATH` on `render-all --only <aggregate>` are both
meaningful (one file, unambiguous) and should be supported.

No new exit codes: aggregate failures reuse the existing mapping
(`ConfigError` → 7, merge conflict → 5, document validation → 6, etc.).

## Data-model / schema changes

- `models.OutputSpec`: `+ scope: str = "target"`.
  Consider a `Literal["target", "aggregate"]` or a small `StrEnum` for mypy
  strict; `Literal` is lighter and matches the existing plain-dataclass style.
- `models.ProvenanceEntry`: `+ target: str | None = None`.
- `schemas/project.schema.json`: add `"scope": { "enum": ["target",
  "aggregate"] }` to `$defs/output` (`additionalProperties: false` means an
  unknown key is already rejected, so this must be added or `scope` fails
  validation).
- `config.py`: parse `scope`; enforce the aggregate `{target}` / `default`
  rules; add `resolve_aggregate_output_path`.
- `render.py`: `RenderSession`, `render_aggregate`, path templating, fragment
  cache.
- `provenance.py`: include the target in override warnings when present.
- `inventory.py`: reject target names containing `/` or `~` (pointer-hostile).
- `cli.py`: `--only` on `render-all`/`validate-all`; optional `TARGET` for
  `explain`; scope-aware selection and error messages; `list outputs`.

No inventory schema change — routing is unchanged.

## Example project

Extend `example/` rather than inventing a second example project, so the
Ansible inventory demonstrates aggregate scope against the same targets:

- `example/yaml-frag.yaml`: add the `ansible-inventory` output
  (`scope: aggregate`, `path: rendered/inventory.yaml`).
- `example/fragments/ansible/host.yaml`: writes
  `/all/children/{{ ansible_group }}/hosts/{{ target }}`.
- `example/inventory/targets.yaml`: add `ansible_group` to each hardware group
  and route `ansible/host` — ideally from `defaults` so every target is
  included, which also exercises the "one fragment, N targets" provenance case.
- Optionally a `schema:` on the output to show whole-document validation
  replacing the trailing-`checks`-fragment idiom that aggregate scope cannot
  support.

This also gives the snapshot test real content.

## Test plan

New/extended coverage, following the existing file organization:

- `test_config.py`: `scope` parsing and default; aggregate + `{target}` in
  path → error; aggregate + `default: true` → error; per-target behavior
  unregressed.
- `test_merge.py`: templated operation paths — happy path, malformed pointer
  produced by a template, non-string result, missing variable in a path.
- `test_inventory.py`: pointer-hostile target names rejected.
- `test_render.py`: aggregate composition order (target order × fragment
  order); a target opting out; no contributors → not produced; cross-target
  override warning names both targets; provenance carries the target;
  captures run once per target when several outputs consume them (assert on
  the `stub_runner` call count — this is the regression guard for the
  memoization); snapshot fixture for the example Ansible inventory.
- `test_cli.py`: `render TARGET` skips aggregate outputs; `render TARGET
  --only <aggregate>` error and exit code; `render-all` writes both kinds;
  `render-all --only`; `explain` with no TARGET; `list outputs`.

Existing snapshots under `tests/fixtures/expected/` should be untouched — a
useful signal that the change is additive.

## Phasing

1. **Path templating** — make the README true. Independently shippable.
2. **`RenderSession` refactor** — load inventory once, memoize variables,
   cache fragments. Pure refactor, no behavior change, no new config surface.
   Existing tests are the safety net.
3. **Aggregate scope** — config field + `render_aggregate` + provenance
   target. Tests and example project.
4. **CLI polish** — `--only` on the `-all` commands, optional `explain`
   TARGET, `list outputs`.
5. **README** — it is the authoritative reference, so it needs real edits, not
   an appendix: "Project configuration" (`scope`), a new "Aggregate outputs"
   section (ordering, opt-in, the partial-document assertion caveat),
   "Rendering algorithm", "Provenance and `explain`", "Conflict reporting",
   "CLI usage", and the example-project section.

Phases 1 and 2 are worth landing separately even if phase 3 is later
reconsidered — both are improvements on their own terms.

## Phase 2 extension (deferred): prologue / epilogue

To recover whole-document assertions for aggregate outputs, add fragment lists
that run once, outside the per-target loop:

```yaml
# inventory, new top-level block mirroring `defaults`
aggregate:
  variables:
    ansible_ssh_user: chris
  outputs:
    ansible-inventory:
      prologue: [ansible/skeleton]
      epilogue: [ansible/checks]
```

- `prologue` applies before any target; `epilogue` after all of them.
- Variables: `defaults.variables` + `aggregate.variables`. `target` is **not**
  defined in this scope, so `{{ target }}` in a prologue/epilogue fragment
  fails closed with the usual strict-undefined error — which is correct.
- Provenance entries carry `target=None` here, distinguishing them in
  `explain`.

Deliberately deferred: aggregate scope is useful without it (schema and
validators cover whole-document checks), and it is easier to design once there
is a real fragment in the example project to test against.

## Open questions

1. Should an aggregate output be allowed to also be `default: true`? Current
   recommendation: no.
2. Does `render TARGET` skipping aggregate outputs need to say so on stderr?
   Leaning no — it would fire on every single-host render.
3. Should `render-all` render aggregate outputs when some target failed? A
   partial Ansible inventory is arguably worse than none. Recommendation:
   **skip aggregate outputs entirely if any target failed**, and say so —
   consistent with "fail closed" and with not leaving partial files.
4. Is `scope` the right field name? Alternatives: `per_target: false`,
   `mode: aggregate`. `scope` reads best in the config and in errors.
