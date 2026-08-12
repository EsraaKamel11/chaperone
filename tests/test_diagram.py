"""The two pictures in the README are generated, and what is guarded here is that they cannot lie.

A hand-drawn diagram goes stale in one direction and gives no sign: a function is renamed, a call
stops being made, and the picture keeps showing the old shape while every word around it is still
true. `tools/diagram.py` exists so that the shape is read out of the tree instead, and this module
is what holds it to that -- every symbol a node names is resolved, every call an arrow claims is
found in an AST, and the committed fences are compared against a fresh render.

**Why a generator that prints a fixed string would fail these tests.** Such a generator resolves
nothing and derives nothing, so `test_a_node_naming_a_symbol_the_tree_does_not_hold_is_refused` and
`test_an_edge_the_ast_cannot_find_is_refused_naming_the_pair` are the two that separate the honest
version from it: both drive the refusal on a specification built here, over a tree neither of them
touches.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from tools.diagram import (
    CHOKEPOINT,
    DAY2,
    DERIVED_STRINGS,
    ENGINE,
    JUDGE,
    README_PATH,
    SCRIPTED,
    TWO_LANES,
    Diagram,
    DiagramError,
    Edge,
    Node,
    begin_marker,
    call_pairs,
    call_paths,
    check_drawn_lane_containment,
    check_edges,
    check_lane_separation,
    check_symbols,
    end_marker,
    render,
    replace_between,
    resolve,
    symbols_named,
    write_readme,
)

GENERATOR = Path(__file__).resolve().parent.parent / "tools" / "diagram.py"

#: Every symbol the two pictures are required to keep naming, as the short name a reader sees on a
#: node. A floor rather than an inventory: the derivations below are what catch the *next* stale
#: node, and this is what stops the specifications themselves quietly shrinking until there is
#: nothing left for those derivations to check. `_PREDICATE_FLOOR` in `tests/gates/test_hook.py`
#: exists for the same reason and against the same failure.
SYMBOL_FLOOR = frozenset({
    "score_quality", "guarded_call", "decide", "evaluate_act_classes", "validate_citations",
    "evaluate_tripwires", "Checker.check", "destination_for", "ReviewQueues.put", "Gateway.call",
    "verify",
})


def _short(symbol: str) -> str:
    """`chaperone.audit.gateway:Gateway.call` as `Gateway.call`, which is what a node shows."""
    return symbol.split(":")[-1]


@pytest.mark.parametrize("diagram", (TWO_LANES, DAY2), ids=lambda d: d.marker)
def test_every_symbol_a_diagram_names_resolves_in_the_tree(diagram: Diagram):
    """The anti-stale-picture property, driven through the generator's own resolution.

    Production change that breaks this: renaming or deleting any function a node names. The picture
    would otherwise keep showing it, and no other guard in this repository reads a node label --
    `tests/test_readme_claims.py` resolves backticked test names and source paths, and a mermaid
    node label is neither.

    Asserted by calling the resolution the generator uses rather than by importing the symbols here.
    A second lookup written in this file would agree with the tree while the generator was resolving
    nothing at all, which is exactly the generator this file has to be able to fail.
    """
    named = symbols_named(diagram)
    assert named, f"{diagram.marker} names no symbol, so this guard would pass vacuously"

    check_symbols(diagram)


def test_the_diagrams_together_still_name_every_symbol_the_argument_rests_on():
    """A specification that quietly narrows passes every derivation above it.

    Production change that breaks this: dropping a node -- the checker, say, or the tripwires --
    from `TWO_LANES`. Every remaining symbol still resolves and every remaining edge is still
    AST-backed, so the guards above stay green over a picture that has stopped showing the lane.
    """
    named = {_short(s) for d in (TWO_LANES, DAY2) for s in symbols_named(d)}

    assert SYMBOL_FLOOR <= named, (
        f"the diagrams have stopped naming: {sorted(SYMBOL_FLOOR - named)}"
    )


def test_a_node_naming_a_symbol_the_tree_does_not_hold_is_refused():
    """The refusal itself, driven on a specification this repository does not ship.

    A sweep over the real tree is equally green with a working resolver and with one that returns
    early, so the resolver needs a case it must refuse. This is that case, and it is built here
    rather than by editing `src/`: the module and the class are real, and the attribute is not.

    The name has to appear in the message. A build that fails with `DiagramError` and no name sends
    a reader looking through both pictures for whichever node moved.
    """
    fabricated = "chaperone.gates.engine:decide_everything"
    invented = Diagram(
        marker="scratch",
        kind="flowchart",
        title="a picture naming something the tree does not hold",
        nodes=(Node("a", "the node", symbol=fabricated),),
        edges=(),
    )

    with pytest.raises(DiagramError) as refused:
        check_symbols(invented)

    assert "decide_everything" in str(refused.value), (
        f"the refusal does not name the missing symbol: {refused.value}"
    )


def test_a_symbol_whose_module_is_gone_is_refused_by_the_same_route():
    """The other half of a rename: the module moves rather than the function.

    Production change that breaks this: a resolver that catches `ImportError` and treats an
    unimportable module as a node with nothing to check. That version passes the guard above,
    because the class it names is real.
    """
    with pytest.raises(DiagramError) as refused:
        resolve("chaperone.gates.no_such_module:decide")

    assert "no_such_module" in str(refused.value), (
        f"the refusal does not name the missing module: {refused.value}"
    )


def test_a_node_that_names_no_symbol_is_allowed_and_is_not_silently_treated_as_one():
    """Not every box is a function. The draft, the denial and the queue are none of them.

    This is the hole the guards above would have if a symbol-less node raised: the specifications
    would be pushed into naming a symbol for every box, and the honest answer for three of them is
    that there is not one. Asserted so that the allowance is deliberate rather than incidental.
    """
    plain = Diagram(
        marker="scratch",
        kind="flowchart",
        title="a picture with one box that names nothing",
        nodes=(Node("a", "one draft, one moment"),),
        edges=(),
    )

    check_symbols(plain)

    assert symbols_named(plain) == (), (
        "a node with no symbol was counted as naming one, so the resolution above ran on a label"
    )


def test_an_edge_callee_is_resolved_as_a_symbol_and_not_only_the_nodes_it_joins():
    """An edge may name the method it calls, and that name is as stale-able as a node's.

    The review queue is one participant and two methods reach it: the chokepoint puts an escalation
    in, and the demo reads one back out. The step carries the method, so the step is what would go
    stale under a rename, and a resolver reading nodes alone would never look at it.
    """
    fabricated = "chaperone.gates.queues:ReviewQueues.take"
    invented = Diagram(
        marker="scratch",
        kind="sequence",
        title="a step naming a method the class does not have",
        nodes=(Node("a", "caller", symbol="chaperone.gates.hook:guarded_call"),
               Node("b", "the review queue", symbol="chaperone.gates.queues:ReviewQueues")),
        edges=(Edge("a", "b", "takes", callee=fabricated),),
    )

    with pytest.raises(DiagramError) as refused:
        check_symbols(invented)

    assert "ReviewQueues.take" in str(refused.value), (
        f"the refusal does not name the missing method: {refused.value}"
    )


#: Every call relationship the two pictures are required to keep drawing, as short names.
#:
#: The same floor argument as `SYMBOL_FLOOR`, one layer down and against a different failure: an
#: edge set that narrows to nothing satisfies "every edge is AST-backed" perfectly. The four pairs
#: rooted at `guarded_call` are the chokepoint's whole contract -- it decides, it records, it looks
#: the destination up and it routes -- and the four rooted at `decide` are the predicate lane.
EDGE_FLOOR = frozenset({
    ("guarded_call", "Gateway.call"),
    ("guarded_call", "decide"),
    ("guarded_call", "destination_for"),
    ("guarded_call", "ReviewQueues.put"),
    ("decide", "evaluate_act_classes"),
    ("decide", "validate_citations"),
    ("decide", "evaluate_tripwires"),
    ("decide", "Checker.check"),
    ("scripted", "score_quality"),
    ("scripted", "guarded_call"),
    ("scripted", "verify"),
})


@pytest.mark.parametrize("diagram", (TWO_LANES, DAY2), ids=lambda d: d.marker)
def test_every_call_arrow_a_diagram_draws_is_a_call_the_ast_can_find(diagram: Diagram):
    """An arrow between two symbols is a claim about the tree, so it is held to the tree.

    Production change that breaks this: deleting a call the picture draws -- taking the citation
    predicate out of `decide`, or routing the escalation from somewhere other than the chokepoint.
    Symbol resolution cannot see either: both functions still exist, and the arrow between them is
    the thing that has stopped being true.

    Driven through the generator's own derivation, for the reason the symbol guard is: a call graph
    rebuilt in this file would agree with the tree while the generator derived nothing.
    """
    pairs = call_pairs(diagram)
    assert pairs, f"{diagram.marker} draws no checked arrow, so this guard would pass vacuously"

    check_edges(diagram)


def test_the_call_arrows_still_cover_the_relationships_the_argument_rests_on():
    """A picture may not quietly stop drawing the mechanism it is a picture of.

    Production change that breaks this: dropping the routing arrows and leaving the denial as a
    box with nothing after it. Every remaining arrow is still AST-backed, so the guard above stays
    green over a picture that has stopped making the claim.
    """
    drawn = {
        (caller.split(":")[-1], callee.split(":")[-1])
        for diagram in (TWO_LANES, DAY2)
        for caller, callee in call_pairs(diagram)
    }

    assert EDGE_FLOOR <= drawn, f"the diagrams have stopped drawing: {sorted(EDGE_FLOOR - drawn)}"


def test_an_edge_the_ast_cannot_find_is_refused_naming_the_pair():
    """The refusal, on a pair this tree genuinely does not hold.

    `destination_for` returns a queue name from a disposition and calls nothing in the audit
    package. An arrow from it to `verify` is the shape of a picture drawn from memory, and it is
    what a build has to stop on.

    Both ends have to appear in the message. "an edge is not AST-backed" leaves a reader diffing two
    pictures against a call graph to find out which arrow moved.

    Built here rather than by editing `src/`, which the mutation sweep already owns and which an
    interrupted test would leave modified.
    """
    invented = Diagram(
        marker="scratch",
        kind="flowchart",
        title="a picture drawing a call nobody makes",
        nodes=(Node("a", "destination_for", symbol="chaperone.gates.engine:destination_for"),
               Node("b", "verify", symbol="chaperone.audit.chain:verify")),
        edges=(Edge("a", "b", "checks the chain"),),
    )

    with pytest.raises(DiagramError) as refused:
        check_edges(invented)

    message = str(refused.value)
    assert "destination_for" in message and "verify" in message, (
        f"the refusal does not name the caller and the callee: {message}"
    )


def test_a_call_on_a_parameter_is_not_read_as_a_call_to_the_function_of_that_name():
    """The false positive that would back the load-bearing arrow for the wrong reason.

    `Gateway.call` takes a `decide` **parameter** and invokes it, so a walk that matched callee
    names would report `Gateway.call --> decide` as a real call to `chaperone.gates.engine:decide`.
    It is not one: the gateway imports no engine and knows nothing about what it was handed. The
    chain the picture draws is `guarded_call --> Gateway.call` and `guarded_call --> decide`, two
    separate arrows, and this is what stops the middle one being invented.

    Production change that breaks this: resolving a callee by its bare name against the whole tree
    rather than against the calling module's own imports and definitions.
    """
    invented = Diagram(
        marker="scratch",
        kind="flowchart",
        title="a picture reading a parameter as the function it shares a name with",
        nodes=(Node("a", "Gateway.call", symbol="chaperone.audit.gateway:Gateway.call"),
               Node("b", "decide", symbol=ENGINE)),
        edges=(Edge("a", "b", "decides"),),
    )

    with pytest.raises(DiagramError) as refused:
        check_edges(invented)

    assert "decide" in str(refused.value), f"the refusal does not name the callee: {refused.value}"


def test_an_arrow_may_be_backed_by_a_call_made_through_a_helper_and_the_path_is_reported():
    """`guarded_call` does not call `decide`. It calls `_decide_for`, which does.

    So the arrow the README draws is real and is not direct, and a walk that read one level would
    refuse the truest edge in the picture. The transitive walk follows helpers inside
    `chaperone.gates`, which is `tests/gates/test_hook.py::_pure_predicates_reachable_from`'s rule,
    written there for the same reason: `_decide_for` was introduced in front of `decide` and a
    detector rooted one level down stopped seeing what it was watching.

    The path is asserted rather than only the membership, because "reachable" without a route is
    what a reviewer cannot check. A refusal prints it for the same reason.
    """
    reached = call_paths(CHOKEPOINT)
    assert ENGINE in reached, f"{CHOKEPOINT} no longer reaches {ENGINE}: {sorted(reached)}"

    route = reached[ENGINE]
    assert any("_decide_for" in step for step in route), (
        f"the walk reached decide directly, so this test is no longer about a helper: {route}"
    )


def test_no_permission_lane_entry_point_reaches_the_quality_judge():
    """The thesis of the first picture is an absence, and an absence is enforced rather than drawn.

    A score authorizing nothing cannot be derived positively: there is no edge to find. What can be
    derived is the refusal. If a gate ever consults `score_quality`, the walk finds it and the build
    stops, rather than the picture continuing to show two lanes that no longer are two.

    Production change that breaks this: a `decide` that reads a quality score, or a `guarded_call`
    that admits one. Either is the exact collapse design spec 1 refuses, and neither would move a
    single label in the diagram.
    """
    assert call_paths(CHOKEPOINT), "the chokepoint reaches nothing, so this refusal is vacuous"

    check_lane_separation()


def test_the_lane_separation_check_fires_on_a_caller_that_does_reach_the_judge():
    """A refusal that is green on the tree is equally green when it has stopped looking.

    `demo/day2.py`'s `scripted` really does call `score_quality` -- it is the file that prints both
    lanes -- so it is the honest positive control for the guard above, and it needs no scratch
    module and no edit to `src/`.
    """
    with pytest.raises(DiagramError) as refused:
        check_lane_separation(entries=(SCRIPTED,))

    assert "score_quality" in str(refused.value), (
        f"the refusal does not name what was reached: {refused.value}"
    )
    assert JUDGE in call_paths(SCRIPTED), "the control no longer reaches the judge"


def test_a_reply_arrow_without_a_call_going_the_other_way_is_refused():
    """The dashed return leg is the one place an unchecked arrow could hide.

    A sequence diagram answers as well as calls, and an answer is not a call, so replies are not
    held to the AST. That is an opening: relabel a call `reply=True` and it stops being checked. So
    a reply must have a matching call in the opposite direction, which makes the relabelling refuse
    rather than pass -- the call it would have to pair with is the one that was not there.
    """
    invented = Diagram(
        marker="scratch",
        kind="sequence",
        title="an answer to a question nobody asked",
        nodes=(Node("a", "verify", symbol="chaperone.audit.chain:verify"),
               Node("b", "decide", symbol=ENGINE)),
        edges=(Edge("a", "b", "ok", reply=True),),
    )

    with pytest.raises(DiagramError) as refused:
        check_edges(invented)

    message = str(refused.value)
    assert "reply" in message.lower(), f"the refusal does not say what is wrong: {message}"


# --------------------------------------------------------------------------------------------
# The markers, the fences, and the no-drift binding.
# --------------------------------------------------------------------------------------------

README = README_PATH.read_text(encoding="utf-8")


@pytest.mark.parametrize("diagram", (TWO_LANES, DAY2), ids=lambda d: d.marker)
def test_each_marker_appears_exactly_once_in_the_committed_readme(diagram: Diagram):
    """A vanished marker must fail loudly rather than regenerate into the wrong place.

    The generator owns the bytes between one pair and nothing else, which is only true while there
    is exactly one pair. Two `begin` markers and the replacement has two candidate spans; none, and
    it has none, and the tempting repair in both cases is to append. Either way a picture ends up
    somewhere no reader is looking, on a page whose other generated block is byte-compared.

    Production change that breaks this: hand-editing the README and dropping a comment that looks
    like scaffolding, which is exactly what an HTML comment looks like.
    """
    for marker in (begin_marker(diagram.marker), end_marker(diagram.marker)):
        assert README.count(marker) == 1, (
            f"README.md carries {README.count(marker)} of {marker!r}; the generator owns one span "
            f"per picture and cannot own none or two"
        )

    assert README.index(begin_marker(diagram.marker)) < README.index(end_marker(diagram.marker)), (
        f"the {diagram.marker} markers are the wrong way round in README.md"
    )


@pytest.mark.parametrize("bad", ("none", "twice", "reversed"), ids=lambda s: s)
def test_a_document_whose_markers_are_missing_duplicated_or_reversed_is_refused(bad: str):
    """The three ways a marker pair stops delimiting one span, each refused rather than repaired.

    Driven on documents built here. A guard that only read the committed README would be green on a
    working replacement and green on one that appends to whatever it was handed.
    """
    begin, end = begin_marker("two-lanes"), end_marker("two-lanes")
    documents = {
        "none": "a page with no markers at all\n",
        "twice": f"{begin}\nold\n{end}\nmiddle\n{begin}\nalso old\n{end}\n",
        "reversed": f"{end}\nold\n{begin}\n",
    }

    with pytest.raises(DiagramError) as refused:
        replace_between(documents[bad], "two-lanes", "new\n")

    assert "two-lanes" in str(refused.value), (
        f"the refusal does not name the marker: {refused.value}"
    )


def test_a_replacement_changes_only_what_lies_between_the_markers():
    """The property the whole approach rests on: the generator owns a span, not a file.

    Production change that breaks this: rewriting the document from a template, which is how a
    generated block acquires the power to delete the prose around it. The README is seventy
    kilobytes of argument and two of picture.
    """
    begin, end = begin_marker("day2"), end_marker("day2")
    document = f"before\n{begin}\nold picture\n{end}\nafter\n"

    replaced = replace_between(document, "day2", "new picture\n")

    assert replaced == f"before\n{begin}\nnew picture\n{end}\nafter\n", (
        f"the replacement did not leave the surrounding page alone: {replaced!r}"
    )


def test_the_committed_readme_is_what_the_generator_writes_between_both_marker_pairs(
    tmp_path: Path,
):
    """The no-drift binding: the pictures on the page are the pictures this tree produces.

    Production change that breaks this: any edit to a specification, a label or the renderer shipped
    without regenerating, and any hand-edit inside a fence. Every other guard in this module reads
    `render()`, so all of them stay green over a committed page that has stopped agreeing with it --
    which is the whole failure a generated diagram exists to prevent, arriving one level up.

    **Both regions are perturbed before the writer runs**, which is what makes this a test of the
    writer rather than of the copy. Handed the committed bytes unaltered, a `write_readme` that
    returned immediately would pass, and so would one that wrote only the first picture.

    Compared as bytes rather than as text for `tests/test_perturbation_log.py`'s reason: the
    repository pins LF, and a writer using text mode on Windows produces a page that differs from the
    committed one in every line while reading identically.
    """
    committed = README_PATH.read_bytes()
    document = README_PATH.read_text(encoding="utf-8")
    for name in (TWO_LANES.marker, DAY2.marker):
        document = replace_between(document, name, "a picture that is not the one this tree draws\n")
    fresh = tmp_path / "README.md"
    fresh.write_bytes(document.encode("utf-8"))
    assert fresh.read_bytes() != committed, "the perturbation changed nothing, so this guard is vacuous"

    write_readme(fresh)

    assert fresh.read_bytes() == committed, (
        "README.md is not what tools/diagram.py writes; regenerate it with `python tools/diagram.py`"
    )


#: A mermaid arrow, as the two node ids it joins. Written here rather than taken from the generator
#: for standing check 3's reason: a reference computed the way the code under test computes it
#: agrees with it by construction. This reads the rendered text the way a mermaid parser would --
#: an identifier, an arrow, an identifier -- and knows nothing about `Edge`.
ARROW = re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:-->>|-\.->|-->|->>|-x)\s*(?:\|[^|]*\|)?\s*"
    r"([A-Za-z_][A-Za-z0-9_]*)",
    re.M,
)

#: A declared box or participant: `id["label"]`, or `participant id as label`.
DECLARED = re.compile(r"^\s*(?:participant\s+)?([A-Za-z_][A-Za-z0-9_]*)(?:\[\"|\s+as\s)", re.M)


@pytest.mark.parametrize("diagram", (TWO_LANES, DAY2), ids=lambda d: d.marker)
def test_the_rendered_block_is_one_closed_mermaid_fence(diagram: Diagram):
    """An unclosed fence swallows the rest of the page on GitHub.

    Production change that breaks this: emitting the diagram body without its fence, or with the
    opening fence and no closing one. Both leave every guard about the *content* of the block green.
    """
    lines = render(diagram).splitlines()

    assert lines[0] == "```mermaid", f"the block does not open a mermaid fence: {lines[0]!r}"
    assert lines[-1] == "```", f"the block does not close its fence: {lines[-1]!r}"
    assert "```" not in "\n".join(lines[1:-1]), "the block carries a fence inside itself"
    opener = "flowchart TD" if diagram.kind == "flowchart" else "sequenceDiagram"
    assert opener in lines[1:4], f"the block does not declare a {diagram.kind}: {lines[1:4]}"


@pytest.mark.parametrize("diagram", (TWO_LANES, DAY2), ids=lambda d: d.marker)
def test_every_node_an_arrow_names_is_declared_in_the_same_fence(diagram: Diagram):
    """Mermaid invents a box for an undeclared id, so a typo renders as a second, empty node.

    That is the failure this catches and no other guard could: the arrow is AST-backed, the symbol
    resolves, and the picture shows an unlabelled box beside the one it was meant to point at.

    Read out of the rendered text rather than out of `diagram.edges`, so a renderer that emits an
    id different from the one it was given is caught too.
    """
    body = "\n".join(render(diagram).splitlines()[1:-1])
    declared = set(DECLARED.findall(body))
    assert declared, f"no node declaration was parsed out of {diagram.marker}"

    joined = {end for arrow in ARROW.findall(body) for end in arrow}
    assert joined, f"no arrow was parsed out of {diagram.marker}"

    assert joined <= declared, (
        f"{diagram.marker} points at boxes it never declares: {sorted(joined - declared)}"
    )


#: Characters the reader-facing layer does not use. The em-dash rule is
#: `tests/test_readme_claims.py::test_the_reader_facing_docs_carry_no_em_dashes`, and the curly
#: quotes travel with it: inside a fence they are as visible to a reader as outside one, and a
#: generated block is the one place a convention silently stops being applied.
BANNED = {"—": "an em-dash", "‘": "a curly quote", "’": "a curly quote",
          "“": "a curly quote", "”": "a curly quote"}


@pytest.mark.parametrize("diagram", (TWO_LANES, DAY2), ids=lambda d: d.marker)
def test_no_rendered_diagram_carries_a_character_the_reader_facing_layer_bans(diagram: Diagram):
    """The convention applies inside the fences, which is where nothing was checking it."""
    block = render(diagram)
    for character, what in BANNED.items():
        assert character not in block, f"{diagram.marker} contains {what}"


@pytest.mark.parametrize("diagram", (TWO_LANES, DAY2), ids=lambda d: d.marker)
def test_no_rendered_diagram_puts_a_closing_bracket_next_to_an_opening_parenthesis(diagram: Diagram):
    """`](` is a markdown link, and the link checker resolves one wherever it appears.

    `test_every_relative_link_in_the_reader_facing_docs_resolves` reads the README as text and does
    not stop at a fence, so a mermaid round node written with a bracket beside a parenthesis becomes
    a link to a path that is not there. The failure would then arrive in a different test module
    naming a file nobody wrote, which is the worst place for a reader to start.
    """
    assert "](" not in render(diagram), (
        f"{diagram.marker} contains a bracket beside a parenthesis, which the README link checker "
        f"reads as a link"
    )


def test_no_value_the_diagrams_derive_is_also_typed_into_the_generator():
    """The difference between a generated picture and a drawn one, asserted directly.

    Every string in `DERIVED_STRINGS` is read out of the tree at build time: the queue name is
    `destination_for`'s return, and the question, the drafted body, the tool name, the blocked class
    and the quoted span are read out of `demo/day2.py`. A generator that typed any of them would
    render the same bytes today and go on rendering them after the tree moved.

    Both halves are needed. The first says the value reached the picture; the second says it was not
    written down here, which is the half that separates a derivation from a well-informed literal.
    """
    source = GENERATOR.read_text(encoding="utf-8")
    rendered = render(TWO_LANES) + render(DAY2)
    assert DERIVED_STRINGS, "the generator derives no string, so this guard would be vacuous"

    for label, value in DERIVED_STRINGS.items():
        assert value, f"{label} derived an empty string"
        assert value in rendered, f"{label} was derived and never reached a picture: {value!r}"
        assert value not in source, (
            f"{label} is typed into tools/diagram.py as well as derived from the tree, so the "
            f"picture would survive the tree moving: {value!r}"
        )


def test_the_walk_cache_keys_on_the_follow_scope_and_not_only_the_entry():
    """`call_paths(entry, follow=...)` and a later default-scope call are different questions.

    The production change this catches: a cache keyed on `entry` alone, which answers the second
    question with the first call's result. A narrower first walk then falsely refuses a real
    arrow, and a wider one falsely backs an arrow through a route outside the followed packages --
    a silent wrong answer in the one function every edge check and the lane refusal rest on.
    """
    import tools.diagram as diagram_module

    diagram_module._WALK_CACHE.clear()
    narrow = call_paths(CHOKEPOINT, follow="nothing.at.all")
    assert not any(c.endswith(":decide") for c in narrow), (
        "the narrowed walk reached decide anyway, so this test's premise is wrong"
    )
    default = call_paths(CHOKEPOINT)
    diagram_module._WALK_CACHE.clear()
    assert any(c.endswith(":decide") for c in default), (
        "the default-scope walk lost chaperone.gates.engine:decide to a cache entry keyed on the "
        "entry alone; the follow scope must be part of the key"
    )


def test_a_drawn_edge_out_of_the_quality_lane_is_refused_even_between_prose_boxes():
    """The first picture's thesis is an absence, and the tree-side refusal cannot see the drawing.

    `check_lane_separation` refuses a TREE in which the permission lane reaches the judge. But
    `call_pairs` skips any edge with a symbol-less endpoint, so a spec edit drawing an arrow from a
    prose quality box into the permission lane would render two lanes meeting while every tree
    check stayed green. The production change this catches: exactly that spec edit. The rule is
    drawing-side: no edge may leave a group named "quality", whatever the endpoints name.
    """
    joined = Diagram(
        marker="probe", kind="flowchart", title="probe",
        nodes=(Node("q1", "a prose quality box"), Node("p1", "a prose permission box")),
        edges=(Edge("q1", "p1", "a lane-joining arrow no tree check can see"),),
        groups=(("quality", "QUALITY", ("q1",)), ("permission", "PERMISSION", ("p1",))),
    )
    with pytest.raises(DiagramError, match="q1"):
        check_drawn_lane_containment(joined)

    reply_out = Diagram(
        marker="probe", kind="sequence", title="probe",
        nodes=(Node("q1", "a prose quality box"), Node("p1", "a prose permission box")),
        edges=(Edge("q1", "p1", "a reply is an arrow too", reply=True),),
        groups=(("quality", "QUALITY", ("q1",)), ("permission", "PERMISSION", ("p1",))),
    )
    with pytest.raises(DiagramError, match="q1"):
        check_drawn_lane_containment(reply_out)

    contained = Diagram(
        marker="probe", kind="flowchart", title="probe",
        nodes=(Node("q1", "prose"), Node("q2", "prose"), Node("p1", "prose")),
        edges=(Edge("q1", "q2", "inside the lane"), Edge("p1", "q1", "into the lane is the judged draft arriving")),
        groups=(("quality", "QUALITY", ("q1", "q2")), ("permission", "PERMISSION", ("p1",))),
    )
    check_drawn_lane_containment(contained)
