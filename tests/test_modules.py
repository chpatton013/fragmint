"""Tests for :mod:`fragmint.modules`: Ref parsing/display, the import
closure (loading, ordering, collision/aliasing/cycle detection, directory
inference), reference resolution, and flattening. See README.md "The module
model" and "Composition and ordering" — this file carries the bulk of the
new coverage the module-model redesign introduces.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from fragmint import modules
from fragmint.errors import AmbiguousFragmentError, ModuleError, UnknownFragmentError
from fragmint.models import Ref

# --- Ref parsing and display ------------------------------------------------


def test_parse_ref_bare_has_no_module() -> None:
    assert modules.parse_ref("host") == (None, "host")


def test_parse_ref_splits_on_first_colon_only() -> None:
    """A path containing further `:` characters (unusual, but not forbidden)
    is not split again — only the first `:` is the module/path separator."""
    assert modules.parse_ref("ns:a:b") == ("ns", "a:b")


@pytest.mark.parametrize("raw", ["", ":path", "ns:", ":"])
def test_parse_ref_rejects_empty_halves(raw: str) -> None:
    with pytest.raises(ModuleError):
        modules.parse_ref(raw)


def test_parse_ref_rejects_invalid_module_charset() -> None:
    with pytest.raises(ModuleError):
        modules.parse_ref("bad name:path")


def test_display_ref_root_resolved_is_bare() -> None:
    """A ref resolving to the root document displays bare — never a leading
    `:` from an empty module string."""
    assert modules.display_ref(Ref(module=None, path="hardware/gb10")) == "hardware/gb10"


def test_display_ref_module_resolved_is_qualified() -> None:
    """A ref resolving to a module displays qualified, even when it was
    *written* bare inside that module — display is a property of the
    resolved ref, not the written form (README.md "The module model")."""
    assert modules.display_ref(Ref(module="ansible", path="host")) == "ansible:host"


# --- The closure -------------------------------------------------------------


def test_closure_root_last(closure_from_tree) -> None:
    closure = closure_from_tree(
        {
            "targets.yaml": """
version: 1
imports:
  a: modules/a
outputs:
  main:
    path: "out-{target}"
targets:
  t: {}
""",
            "modules/a/fragmint.yaml": "version: 1\n",
        }
    )
    assert closure.root.name is None
    assert closure.documents[-1] is closure.root
    assert [d.name for d in closure.documents] == ["a", None]


def test_closure_post_order_declaration_order(closure_from_tree) -> None:
    """Dependencies contribute before their importers; siblings appear in the
    importer's own declaration order."""
    closure = closure_from_tree(
        {
            "targets.yaml": """
version: 1
imports:
  first: modules/first
  second: modules/second
outputs:
  main:
    path: "out-{target}"
targets:
  t: {}
""",
            "modules/first/fragmint.yaml": "version: 1\n",
            "modules/second/fragmint.yaml": "version: 1\n",
        }
    )
    assert [d.name for d in closure.documents] == ["first", "second", None]


def test_closure_diamond_contributes_once(closure_from_tree) -> None:
    """The same module imported transitively by two different importers is
    loaded and appears exactly once, at first encounter."""
    closure = closure_from_tree(
        {
            "targets.yaml": """
version: 1
imports:
  left: modules/left
  right: modules/right
outputs:
  main:
    path: "out-{target}"
targets:
  t: {}
""",
            "modules/left/fragmint.yaml": """
version: 1
imports:
  shared: ../shared
""",
            "modules/right/fragmint.yaml": """
version: 1
imports:
  shared: ../shared
""",
            "modules/shared/fragmint.yaml": "version: 1\n",
        }
    )
    names = [d.name for d in closure.documents]
    assert names.count("shared") == 1
    # shared is a dependency of both left and right, so it precedes both.
    assert names.index("shared") < names.index("left")
    assert names.index("shared") < names.index("right")


def test_closure_name_collision_rejected(closure_from_tree) -> None:
    """The same local name bound to two different module directories anywhere
    in the closure is an error naming both directories."""
    with pytest.raises(ModuleError, match="collision"):
        closure_from_tree(
            {
                "targets.yaml": """
version: 1
imports:
  shared: modules/a
  other: modules/b
outputs:
  main:
    path: "out-{target}"
targets:
  t: {}
""",
                "modules/a/fragmint.yaml": """
version: 1
imports:
  shared: ../b
""",
                "modules/b/fragmint.yaml": "version: 1\n",
            }
        )


def test_closure_aliasing_rejected(closure_from_tree) -> None:
    """The same module directory bound to two different names is an error —
    it would give one fragment file two provenance identities."""
    with pytest.raises(ModuleError, match="aliasing"):
        closure_from_tree(
            {
                "targets.yaml": """
version: 1
imports:
  a: modules/shared
  b: modules/shared
outputs:
  main:
    path: "out-{target}"
targets:
  t: {}
""",
                "modules/shared/fragmint.yaml": "version: 1\n",
            }
        )


def test_closure_cycle_rejected(closure_from_tree) -> None:
    """An import cycle is an error naming the cycle path."""
    with pytest.raises(ModuleError, match="cycle"):
        closure_from_tree(
            {
                "targets.yaml": """
version: 1
imports:
  a: modules/a
outputs:
  main:
    path: "out-{target}"
targets:
  t: {}
""",
                "modules/a/fragmint.yaml": """
version: 1
imports:
  back: ..
""",
            }
        )


def test_closure_diamond_is_fine(closure_from_tree) -> None:
    """Importing the same module by the same name from two different places
    is not a collision — it's the diamond case, and is allowed."""
    closure = closure_from_tree(
        {
            "targets.yaml": """
version: 1
imports:
  left: modules/left
  shared: modules/shared
outputs:
  main:
    path: "out-{target}"
targets:
  t: {}
""",
            "modules/left/fragmint.yaml": """
version: 1
imports:
  shared: ../shared
""",
            "modules/shared/fragmint.yaml": "version: 1\n",
        }
    )
    assert closure.by_name["shared"] is not None


def test_module_without_targets_is_valid(closure_from_tree) -> None:
    closure = closure_from_tree(
        {
            "targets.yaml": """
version: 1
imports:
  a: modules/a
outputs:
  main:
    path: "out-{target}"
targets:
  t: {}
""",
            "modules/a/fragmint.yaml": "version: 1\n",
        }
    )
    assert closure.by_name["a"].targets == {}


def test_module_with_targets_rejected(closure_from_tree) -> None:
    """Only the root document (the inventory) may define `targets`."""
    with pytest.raises(ModuleError):
        closure_from_tree(
            {
                "targets.yaml": """
version: 1
imports:
  a: modules/a
outputs:
  main:
    path: "out-{target}"
targets:
  t: {}
""",
                "modules/a/fragmint.yaml": """
version: 1
targets:
  x: {}
""",
            }
        )


def test_inventory_without_targets_rejected(closure_from_tree) -> None:
    """The root document (the inventory) must declare at least one target."""
    with pytest.raises(ModuleError):
        closure_from_tree(
            {
                "targets.yaml": """
version: 1
outputs:
  main:
    path: "out-{target}"
""",
            }
        )


# --- Directory inference (README.md "Directory inference") ------------------


def test_inferred_fragments_dir_present(closure_from_tree) -> None:
    closure = closure_from_tree(
        {
            "targets.yaml": "version: 1\noutputs:\n  main:\n    path: out\ntargets:\n  t: {}\n",
            "fragments/x.yaml": "fragment:\n  version: 1\n  description: x\noperations: []\n",
        }
    )
    assert closure.root.fragments_dir is not None
    assert closure.root.fragments_dir.name == "fragments"


def test_inferred_fragments_dir_absent_is_none(closure_from_tree) -> None:
    """An inferred directory that doesn't exist simply means this document
    provides no directory of that kind — not an error."""
    closure = closure_from_tree(
        {
            "targets.yaml": "version: 1\noutputs:\n  main:\n    path: out\ntargets:\n  t: {}\n",
        }
    )
    assert closure.root.fragments_dir is None


def test_explicit_missing_dir_rejected(closure_from_tree) -> None:
    """Declaring a directory is a claim: an explicit path that doesn't exist
    is an error, unlike an unset/inferred one."""
    with pytest.raises(ModuleError):
        closure_from_tree(
            {
                "targets.yaml": (
                    "version: 1\nfragments_dir: does-not-exist\n"
                    "outputs:\n  main:\n    path: out\ntargets:\n  t: {}\n"
                ),
            }
        )


def test_explicit_present_dir_accepted(tmp_path: Path, closure_from_tree) -> None:
    closure = closure_from_tree(
        {
            "targets.yaml": (
                "version: 1\nfragments_dir: custom-frags\n"
                "outputs:\n  main:\n    path: out\ntargets:\n  t: {}\n"
            ),
            "custom-frags/x.yaml": "fragment:\n  version: 1\n  description: x\noperations: []\n",
        }
    )
    assert closure.root.fragments_dir is not None
    assert closure.root.fragments_dir.name == "custom-frags"


# --- Composition and ordering ------------------------------------------------


def test_default_fragment_order_module_then_inventory(closure_from_tree) -> None:
    """Module defaults contribute before the inventory's own defaults."""
    closure = closure_from_tree(
        {
            "targets.yaml": """
version: 1
imports:
  a: modules/a
outputs:
  main:
    path: out
defaults:
  outputs:
    main:
      fragments: [inventory-frag]
targets:
  t: {}
""",
            "modules/a/fragmint.yaml": """
version: 1
defaults:
  outputs:
    main:
      fragments: [module-frag]
""",
        }
    )
    project = modules.flatten(closure)
    assert [modules.display_ref(r) for r in project.default_output_fragments["main"]] == [
        "a:module-frag",
        "inventory-frag",
    ]


def test_group_merge_across_documents(closure_from_tree) -> None:
    """A group name defined in both a module and the inventory merges:
    variables layer, fragments concatenate, both in closure order."""
    closure = closure_from_tree(
        {
            "targets.yaml": """
version: 1
imports:
  a: modules/a
outputs:
  main:
    path: out
groups:
  g:
    variables:
      site: true
targets:
  t:
    groups: [g]
""",
            "modules/a/fragmint.yaml": """
version: 1
groups:
  g:
    variables:
      reusable: true
    outputs:
      main:
        fragments: [module-frag]
""",
        }
    )
    project = modules.flatten(closure)
    group = project.groups["g"]
    assert group.variables == {"reusable": True, "site": True}
    assert [modules.display_ref(r) for r in group.output_fragments["main"]] == ["a:module-frag"]


def test_variable_layering_module_then_inventory(closure_from_tree) -> None:
    closure = closure_from_tree(
        {
            "targets.yaml": """
version: 1
imports:
  a: modules/a
outputs:
  main:
    path: out
defaults:
  variables:
    x: inventory
targets:
  t: {}
""",
            "modules/a/fragmint.yaml": """
version: 1
defaults:
  variables:
    x: module
    y: module-only
""",
        }
    )
    project = modules.flatten(closure)
    assert project.default_variables == {"x": "inventory", "y": "module-only"}


def test_duplicate_output_name_rejected(closure_from_tree) -> None:
    closure = closure_from_tree(
        {
            "targets.yaml": """
version: 1
imports:
  a: modules/a
outputs:
  main:
    path: out
targets:
  t: {}
""",
            "modules/a/fragmint.yaml": """
version: 1
outputs:
  main:
    path: also-out
""",
        }
    )
    with pytest.raises(ModuleError, match="declared more than once"):
        modules.flatten(closure)


def test_duplicate_validator_name_rejected(closure_from_tree) -> None:
    closure = closure_from_tree(
        {
            "targets.yaml": """
version: 1
imports:
  a: modules/a
outputs:
  main:
    path: out
validators:
  check:
    command: ["true"]
targets:
  t: {}
""",
            "modules/a/fragmint.yaml": """
version: 1
validators:
  check:
    command: ["false"]
""",
        }
    )
    with pytest.raises(ModuleError, match="declared more than once"):
        modules.flatten(closure)


def test_two_default_true_across_closure_rejected(closure_from_tree) -> None:
    closure = closure_from_tree(
        {
            "targets.yaml": """
version: 1
imports:
  a: modules/a
outputs:
  main:
    path: out
    default: true
targets:
  t: {}
""",
            "modules/a/fragmint.yaml": """
version: 1
outputs:
  other:
    path: also-out
    default: true
""",
        }
    )
    with pytest.raises(ModuleError, match="default: true"):
        modules.flatten(closure)


def test_attaching_to_undefined_output_rejected(closure_from_tree) -> None:
    closure = closure_from_tree(
        {
            "targets.yaml": """
version: 1
outputs:
  main:
    path: out
defaults:
  outputs:
    nope:
      fragments: [x]
targets:
  t: {}
""",
        }
    )
    with pytest.raises(ModuleError, match="undefined output"):
        modules.flatten(closure)


def test_attaching_to_output_defined_elsewhere_is_fine(closure_from_tree) -> None:
    """Any document may attach fragments to an output it did not itself
    define — that's the whole point of module defaults feeding an
    inventory-owned output, or vice versa."""
    closure = closure_from_tree(
        {
            "targets.yaml": """
version: 1
imports:
  a: modules/a
outputs:
  main:
    path: out
targets:
  t: {}
""",
            "modules/a/fragmint.yaml": """
version: 1
defaults:
  outputs:
    main:
      fragments: [module-frag]
""",
        }
    )
    project = modules.flatten(closure)
    assert "main" in project.default_output_fragments


# --- aggregate: prologue/epilogue/variables (README.md "Aggregate outputs") -


def test_aggregate_prologue_epilogue_concatenate_in_closure_order(closure_from_tree) -> None:
    """A module and the inventory each contribute a prologue and an epilogue
    to the same aggregate output; both tuples concatenate in closure order
    (dependency before importer), independently of each other."""
    closure = closure_from_tree(
        {
            "targets.yaml": """
version: 1
imports:
  a: modules/a
outputs:
  combined:
    scope: aggregate
    path: out.yaml
aggregate:
  outputs:
    combined:
      prologue: [inventory-pre]
      epilogue: [inventory-post]
targets:
  t: {}
""",
            "modules/a/fragmint.yaml": """
version: 1
aggregate:
  outputs:
    combined:
      prologue: [module-pre]
      epilogue: [module-post]
""",
        }
    )
    project = modules.flatten(closure)
    spec = project.aggregate_output_fragments["combined"]
    assert [modules.display_ref(r) for r in spec.prologue] == ["a:module-pre", "inventory-pre"]
    assert [modules.display_ref(r) for r in spec.epilogue] == ["a:module-post", "inventory-post"]


def test_aggregate_variables_layer_module_then_inventory(closure_from_tree) -> None:
    """`aggregate.variables` layer in closure order, later wins — same shape
    as `defaults.variables`."""
    closure = closure_from_tree(
        {
            "targets.yaml": """
version: 1
imports:
  a: modules/a
outputs:
  combined:
    scope: aggregate
    path: out.yaml
aggregate:
  variables:
    x: inventory
targets:
  t: {}
""",
            "modules/a/fragmint.yaml": """
version: 1
aggregate:
  variables:
    x: module
    y: module-only
""",
        }
    )
    project = modules.flatten(closure)
    assert project.aggregate_variables == {"x": "inventory", "y": "module-only"}


def test_module_may_declare_aggregate_block(closure_from_tree) -> None:
    """A module-only `aggregate:` block flattens even with no inventory
    `aggregate:` block present."""
    closure = closure_from_tree(
        {
            "targets.yaml": """
version: 1
imports:
  a: modules/a
outputs:
  combined:
    scope: aggregate
    path: out.yaml
targets:
  t: {}
""",
            "modules/a/fragmint.yaml": """
version: 1
aggregate:
  outputs:
    combined:
      epilogue: [module-post]
""",
        }
    )
    project = modules.flatten(closure)
    assert [
        modules.display_ref(r) for r in project.aggregate_output_fragments["combined"].epilogue
    ] == ["a:module-post"]


def test_aggregate_refs_resolve_against_declaring_document(closure_from_tree) -> None:
    """A bare ref in a module's `aggregate.outputs.<name>.epilogue` resolves
    against that module's own tree and displays qualified."""
    closure = closure_from_tree(
        {
            "targets.yaml": """
version: 1
imports:
  a: modules/a
outputs:
  combined:
    scope: aggregate
    path: out.yaml
targets:
  t: {}
""",
            "modules/a/fragmint.yaml": """
version: 1
aggregate:
  outputs:
    combined:
      epilogue: [checks]
""",
        }
    )
    project = modules.flatten(closure)
    assert [
        modules.display_ref(r) for r in project.aggregate_output_fragments["combined"].epilogue
    ] == ["a:checks"]


def test_aggregate_attaching_to_undefined_output_rejected(closure_from_tree) -> None:
    closure = closure_from_tree(
        {
            "targets.yaml": """
version: 1
outputs:
  main:
    path: out
aggregate:
  outputs:
    nope:
      epilogue: [x]
targets:
  t: {}
""",
        }
    )
    with pytest.raises(ModuleError, match="undefined output"):
        modules.flatten(closure)


@pytest.mark.parametrize(
    "aggregate_block",
    [
        "aggregate:\n  outputs:\n    main:\n      prologue: [x]\n",
        "aggregate:\n  outputs:\n    main:\n      epilogue: [x]\n",
        "aggregate:\n  outputs:\n    main:\n      prologue: []\n      epilogue: []\n",
    ],
)
def test_aggregate_on_target_scoped_output_rejected(closure_from_tree, aggregate_block: str) -> None:
    """`aggregate.outputs.<name>` naming a `scope: target` output is rejected
    — presence of the key is the claim, even with empty lists."""
    closure = closure_from_tree(
        {
            "targets.yaml": f"""
version: 1
outputs:
  main:
    path: "out-{{target}}"
{aggregate_block}targets:
  t: {{}}
""",
            "fragments/x.yaml": (
                "fragment:\n  version: 1\n  description: x\noperations: []\n"
            ),
        }
    )
    with pytest.raises(ModuleError, match="scope 'target'"):
        modules.flatten(closure)


def test_closure_without_aggregate_block_has_empty_aggregate_scope(closure_from_tree) -> None:
    closure = closure_from_tree(
        {
            "targets.yaml": """
version: 1
outputs:
  main:
    path: "out-{target}"
targets:
  t: {}
""",
        }
    )
    project = modules.flatten(closure)
    assert project.aggregate_variables == {}
    assert project.aggregate_output_fragments == {}


# --- Reference resolution ----------------------------------------------------


def test_bare_ref_resolves_against_declaring_document(closure_from_tree) -> None:
    """A bare fragment ref written inside a module resolves against that
    module's own tree, not the root's."""
    closure = closure_from_tree(
        {
            "targets.yaml": """
version: 1
imports:
  a: modules/a
outputs:
  main:
    path: out
targets:
  t: {}
""",
            "modules/a/fragmint.yaml": "version: 1\n",
            "modules/a/fragments/host.yaml": (
                "fragment:\n  version: 1\n  description: x\noperations: []\n"
            ),
        }
    )
    declaring = closure.by_name["a"]
    ref = modules.resolve_ref("host", declaring=declaring, closure=closure)
    assert ref == Ref(module="a", path="host")
    path = modules.fragment_path(ref, closure)
    assert path == (closure.by_name["a"].fragments_dir / "host.yaml").resolve()


def test_bare_ref_in_root_resolves_to_root(closure_from_tree) -> None:
    closure = closure_from_tree(
        {
            "targets.yaml": "version: 1\noutputs:\n  main:\n    path: out\ntargets:\n  t: {}\n",
            "fragments/host.yaml": "fragment:\n  version: 1\n  description: x\noperations: []\n",
        }
    )
    ref = modules.resolve_ref("host", declaring=closure.root, closure=closure)
    assert ref == Ref(module=None, path="host")
    assert modules.display_ref(ref) == "host"


def test_qualified_ref_across_modules(closure_from_tree) -> None:
    closure = closure_from_tree(
        {
            "targets.yaml": """
version: 1
imports:
  a: modules/a
outputs:
  main:
    path: out
targets:
  t: {}
""",
            "modules/a/fragmint.yaml": "version: 1\n",
            "modules/a/fragments/host.yaml": (
                "fragment:\n  version: 1\n  description: x\noperations: []\n"
            ),
        }
    )
    ref = modules.resolve_ref("a:host", declaring=closure.root, closure=closure)
    assert ref == Ref(module="a", path="host")


def test_qualified_ref_unknown_module_rejected(closure_from_tree) -> None:
    closure = closure_from_tree(
        {
            "targets.yaml": "version: 1\noutputs:\n  main:\n    path: out\ntargets:\n  t: {}\n",
        }
    )
    with pytest.raises(ModuleError):
        modules.resolve_ref("nope:host", declaring=closure.root, closure=closure)


def test_two_modules_with_same_fragment_path_stay_distinct(closure_from_tree) -> None:
    closure = closure_from_tree(
        {
            "targets.yaml": """
version: 1
imports:
  a: modules/a
  b: modules/b
outputs:
  main:
    path: out
targets:
  t: {}
""",
            "modules/a/fragmint.yaml": "version: 1\n",
            "modules/a/fragments/host.yaml": (
                "fragment:\n  version: 1\n  description: a\noperations: []\n"
            ),
            "modules/b/fragmint.yaml": "version: 1\n",
            "modules/b/fragments/host.yaml": (
                "fragment:\n  version: 1\n  description: b\noperations: []\n"
            ),
        }
    )
    ref_a = modules.resolve_ref("a:host", declaring=closure.root, closure=closure)
    ref_b = modules.resolve_ref("b:host", declaring=closure.root, closure=closure)
    path_a = modules.fragment_path(ref_a, closure)
    path_b = modules.fragment_path(ref_b, closure)
    assert path_a != path_b


def test_qualified_template_and_schema_refs(closure_from_tree) -> None:
    closure = closure_from_tree(
        {
            "targets.yaml": """
version: 1
imports:
  a: modules/a
outputs:
  main:
    path: out
    template: a:main.tmpl
    schema: a:main.json
targets:
  t: {}
""",
            "modules/a/fragmint.yaml": "version: 1\n",
            "modules/a/templates/main.tmpl": "{{ document }}",
            "modules/a/schemas/main.json": "{}",
        }
    )
    project = modules.flatten(closure)
    output = project.outputs["main"]
    assert output.template is not None and output.template.endswith("main.tmpl")
    assert output.schema is not None and output.schema.endswith("main.json")


def test_containment_violation_rejected_for_fragments(closure_from_tree) -> None:
    """A fragment ref that escapes its own document's directory (e.g. via
    `../..`) fails closed even though the document declares a fragments_dir —
    the containment boundary is the whole module directory, not just its
    fragments_dir, so this needs to escape *that* to trigger."""
    closure = closure_from_tree(
        {
            "proj/targets.yaml": """
version: 1
outputs:
  main:
    path: out
targets:
  t: {}
""",
            "proj/fragments/host.yaml": "fragment:\n  version: 1\n  description: x\noperations: []\n",
            "secret.txt": "nope",
        },
        root="proj/targets.yaml",
    )
    ref = modules.resolve_ref("../../secret", declaring=closure.root, closure=closure)
    with pytest.raises(ModuleError, match="escapes"):
        modules.fragment_path(ref, closure)


def test_containment_violation_rejected_for_templates(closure_from_tree) -> None:
    closure = closure_from_tree(
        {
            "proj/targets.yaml": """
version: 1
outputs:
  main:
    path: out
    template: "../../outside.tmpl"
targets:
  t: {}
""",
            "proj/templates/main.tmpl": "x",
            "outside.tmpl": "x",
        },
        root="proj/targets.yaml",
    )
    with pytest.raises(ModuleError, match="escapes"):
        modules.flatten(closure)


def test_missing_directory_kind_rejected(closure_from_tree) -> None:
    """Referencing a fragment when the declaring document provides no
    fragments directory at all (never declared, and none inferred) fails
    closed, naming the document and the kind."""
    closure = closure_from_tree(
        {
            "targets.yaml": "version: 1\noutputs:\n  main:\n    path: out\ntargets:\n  t: {}\n",
        }
    )
    ref = modules.resolve_ref("host", declaring=closure.root, closure=closure)
    with pytest.raises(ModuleError, match="no fragments directory"):
        modules.fragment_path(ref, closure)


# --- Fragment resolution across input formats -------------------------------


def test_fragment_ref_resolves_a_toml_file(closure_from_tree) -> None:
    closure = closure_from_tree(
        {
            "targets.yaml": "version: 1\noutputs:\n  main:\n    path: out\ntargets:\n  t: {}\n",
            "fragments/host.toml": '[fragment]\nversion = 1\ndescription = "x"\n',
        }
    )
    ref = modules.resolve_ref("host", declaring=closure.root, closure=closure)
    path = modules.fragment_path(ref, closure)
    assert path == (closure.root.fragments_dir / "host.toml").resolve()


def test_fragment_ref_resolves_a_json_file(closure_from_tree) -> None:
    closure = closure_from_tree(
        {
            "targets.yaml": "version: 1\noutputs:\n  main:\n    path: out\ntargets:\n  t: {}\n",
            "fragments/host.json": '{"fragment": {"version": 1, "description": "x"}}',
        }
    )
    ref = modules.resolve_ref("host", declaring=closure.root, closure=closure)
    path = modules.fragment_path(ref, closure)
    assert path == (closure.root.fragments_dir / "host.json").resolve()


def test_fragment_ref_matching_two_files_fails_closed(closure_from_tree) -> None:
    closure = closure_from_tree(
        {
            "targets.yaml": "version: 1\noutputs:\n  main:\n    path: out\ntargets:\n  t: {}\n",
            "fragments/host.yaml": "fragment:\n  version: 1\n  description: x\noperations: []\n",
            "fragments/host.toml": '[fragment]\nversion = 1\ndescription = "x"\n',
        }
    )
    ref = modules.resolve_ref("host", declaring=closure.root, closure=closure)
    with pytest.raises(AmbiguousFragmentError):
        modules.fragment_path(ref, closure)


def test_ambiguous_fragment_error_names_both_files(closure_from_tree) -> None:
    closure = closure_from_tree(
        {
            "targets.yaml": "version: 1\noutputs:\n  main:\n    path: out\ntargets:\n  t: {}\n",
            "fragments/host.yaml": "fragment:\n  version: 1\n  description: x\noperations: []\n",
            "fragments/host.json": '{"fragment": {"version": 1, "description": "x"}}',
        }
    )
    ref = modules.resolve_ref("host", declaring=closure.root, closure=closure)
    with pytest.raises(AmbiguousFragmentError) as excinfo:
        modules.fragment_path(ref, closure)
    message = str(excinfo.value)
    assert "host.yaml" in message
    assert "host.json" in message


def test_missing_fragment_error_names_every_candidate_path(closure_from_tree) -> None:
    closure = closure_from_tree(
        {
            "targets.yaml": "version: 1\noutputs:\n  main:\n    path: out\ntargets:\n  t: {}\n",
            "fragments/.keep": "",
        }
    )
    ref = modules.resolve_ref("host", declaring=closure.root, closure=closure)
    with pytest.raises(UnknownFragmentError) as excinfo:
        modules.fragment_path(ref, closure)
    message = str(excinfo.value)
    assert "host.yaml" in message
    assert "host.toml" in message
    assert "host.json" in message


def test_yml_is_not_a_fragment_candidate_suffix(closure_from_tree) -> None:
    closure = closure_from_tree(
        {
            "targets.yaml": "version: 1\noutputs:\n  main:\n    path: out\ntargets:\n  t: {}\n",
            "fragments/host.yml": "fragment:\n  version: 1\n  description: x\noperations: []\n",
        }
    )
    ref = modules.resolve_ref("host", declaring=closure.root, closure=closure)
    with pytest.raises(UnknownFragmentError):
        modules.fragment_path(ref, closure)


def test_display_ref_is_extension_less_for_a_toml_fragment(closure_from_tree) -> None:
    closure = closure_from_tree(
        {
            "targets.yaml": "version: 1\noutputs:\n  main:\n    path: out\ntargets:\n  t: {}\n",
            "fragments/host.toml": '[fragment]\nversion = 1\ndescription = "x"\n',
        }
    )
    ref = modules.resolve_ref("host", declaring=closure.root, closure=closure)
    assert modules.display_ref(ref) == "host"


def test_fragment_candidate_paths_are_containment_checked(closure_from_tree) -> None:
    """A `../` escape still fails closed for every candidate suffix, not just
    the `.yaml` one."""
    closure = closure_from_tree(
        {
            "proj/targets.yaml": "version: 1\noutputs:\n  main:\n    path: out\ntargets:\n  t: {}\n",
            "proj/fragments/host.yaml": "fragment:\n  version: 1\n  description: x\noperations: []\n",
            "secret.toml": "nope = true\n",
        },
        root="proj/targets.yaml",
    )
    ref = modules.resolve_ref("../../secret", declaring=closure.root, closure=closure)
    with pytest.raises(ModuleError, match="escapes"):
        modules.fragment_path(ref, closure)


# --- Output serialization format ---------------------------------------------


def test_output_format_defaults_to_yaml_for_extensionless_path(closure_from_tree) -> None:
    closure = closure_from_tree(
        {
            "targets.yaml": "version: 1\noutputs:\n  main:\n    path: out\ntargets:\n  t: {}\n",
        }
    )
    project = modules.flatten(closure)
    assert project.outputs["main"].format == "yaml"


def test_output_format_inferred_from_path_suffix(closure_from_tree) -> None:
    closure = closure_from_tree(
        {
            "targets.yaml": "version: 1\noutputs:\n  main:\n    path: out.json\ntargets:\n  t: {}\n",
        }
    )
    project = modules.flatten(closure)
    assert project.outputs["main"].format == "json"


def test_output_format_accepts_yml_suffix(closure_from_tree) -> None:
    closure = closure_from_tree(
        {
            "targets.yaml": "version: 1\noutputs:\n  main:\n    path: out.yml\ntargets:\n  t: {}\n",
        }
    )
    project = modules.flatten(closure)
    assert project.outputs["main"].format == "yaml"


def test_output_format_explicit_key_wins_over_path_suffix(closure_from_tree) -> None:
    closure = closure_from_tree(
        {
            "targets.yaml": (
                "version: 1\noutputs:\n  main:\n    path: out.json\n    format: toml\n"
                "targets:\n  t: {}\n"
            ),
        }
    )
    project = modules.flatten(closure)
    assert project.outputs["main"].format == "toml"


def test_output_format_unknown_value_is_a_module_error(closure_from_tree) -> None:
    with pytest.raises(ModuleError):
        closure_from_tree(
            {
                "targets.yaml": (
                    "version: 1\noutputs:\n  main:\n    path: out\n    format: xml\n"
                    "targets:\n  t: {}\n"
                ),
            }
        )


def test_output_format_inferred_from_the_unsubstituted_path_pattern(closure_from_tree) -> None:
    """A target legitimately named `web.json` must not make `format:`
    inference target-dependent — inference reads the pattern before `{target}`
    substitution, so an output has exactly one format for every target."""
    closure = closure_from_tree(
        {
            "targets.yaml": (
                "version: 1\noutputs:\n  main:\n    path: 'rendered/{target}'\n"
                "targets:\n  web.json: {}\n"
            ),
        }
    )
    project = modules.flatten(closure)
    assert project.outputs["main"].format == "yaml"


# --- resolve_inventory_path (README.md "The module model") ------------------


def test_resolve_inventory_path_directory(tmp_path: Path) -> None:
    (tmp_path / "targets.yaml").write_text("version: 1\n")
    assert modules.resolve_inventory_path(tmp_path) == tmp_path / "targets.yaml"


def test_resolve_inventory_path_file_used_as_given(tmp_path: Path) -> None:
    custom = tmp_path / "hosts.yaml"
    custom.write_text("version: 1\n")
    assert modules.resolve_inventory_path(custom) == custom


def test_resolve_inventory_path_missing_directory_errors(tmp_path: Path) -> None:
    with pytest.raises(ModuleError):
        modules.resolve_inventory_path(tmp_path)


def test_resolve_inventory_path_missing_file_errors(tmp_path: Path) -> None:
    with pytest.raises(ModuleError):
        modules.resolve_inventory_path(tmp_path / "nope.yaml")


def test_resolve_inventory_path_defaults_to_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "targets.yaml").write_text("version: 1\n")
    monkeypatch.chdir(tmp_path)
    assert modules.resolve_inventory_path(None) == Path("targets.yaml")


# --- Real example project ----------------------------------------------------


def test_real_example_closure_loads(example_closure) -> None:
    names = [d.name for d in example_closure.documents]
    assert names == ["autoinstall", "ansible", None]


def test_real_example_flattens(example_closure) -> None:
    project = modules.flatten(example_closure)
    assert set(project.outputs) == {"user-data", "meta-data", "ansible-inventory"}
    assert project.outputs["ansible-inventory"].scope == "aggregate"
    assert project.default_output == "user-data"
    assert [
        modules.display_ref(r)
        for r in project.aggregate_output_fragments["ansible-inventory"].epilogue
    ] == ["ansible:ansible/checks"]
