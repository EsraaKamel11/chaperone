"""Emits the two Mermaid diagrams in `README.md` from the tree they describe.

**A picture is the one part of a document nothing checks.** Every backticked test name and source
path on that page is resolved by `tests/test_readme_claims.py`, both pasted transcripts are compared
against a fresh run, and `docs/RESULTS.md` is bound to `tools/report.py`. A diagram sits in the
middle of all that and answers to nobody: rename a function and the picture keeps showing the old
name, stop making a call and the arrow stays. So the two here are generated, and the generator
refuses to draw what the tree no longer backs.

**What is derived, stated plainly, because this is the claim a reader will probe:**

- **Every symbol a node or a step names is resolved against the tree.** `chaperone.` symbols are
  imported and walked attribute by attribute; `demo/day2.py:scripted` is parsed and looked up in the
  file's own AST. A missing name raises `DiagramError` **naming it**, so a rename fails the build
  rather than leaving a stale picture.
- **Every arrow between two symbols is a call the AST can find.** `call_paths` walks the caller's
  body, resolves each callee the way the interpreter would, and follows helpers inside
  `chaperone.gates` -- which is how `guarded_call --> decide` is backed, since that call is made
  through `_decide_for` rather than directly. The path is carried so a refusal can print it.
- **The queue's name is `destination_for`'s return value**, taken over `REDIRECT_DISPOSITIONS` and
  never typed here.
- **The blocked class, its span, the inbound question, the drafted body and the tool name come out
  of `demo/day2.py`'s AST**, so the sequence diagram is the scene that file actually runs.
- **One absence is enforced rather than drawn.** The thesis of the first picture is that the quality
  lane authorizes nothing, and an absence cannot be derived positively. `check_lane_separation`
  asserts that `score_quality` is unreachable from the permission lane's entry points, so the day a
  gate consults a score the build fails instead of the picture going quietly wrong.

**What is authored: node ids, the prose in labels, lane titles, arrow text, and -- said plainly
rather than left for a reviewer to find -- the order of the steps in the sequence diagram.** An AST
yields call relationships, not execution order. The steps are in the order `demo/day2.py` runs them
because that is how the file reads, and nothing here proves it; what is proved is that each step is
a call that file or its callees really make.

**The markers own their span and nothing else.** `README.md` is read, the bytes between each
`<!-- diagram:NAME:begin -->` and `<!-- diagram:NAME:end -->` pair are replaced, and the rest is
returned untouched. A marker that is missing, duplicated or out of order raises rather than
regenerating into the wrong place, for the reason `tools/report.py` renders before it writes: a
generator that guesses is worse than one that stops.

Written in LF against the `eol=lf` pin, and anchored to the repository rather than to the process
cwd -- both for the reasons `tools/report.py` records, having been bitten by each.

**The derivations run at import** (`QUEUE_NAME`, `SCENE`), so a demo file this module can no longer
read fails `import tools.diagram` itself -- in a pytest run that is a collection error over the
whole test module, not one named failure. The refusal still lands before any write; only the
failure's address is coarse, and this sentence is here so the next reader recognises the shape.
"""
from __future__ import annotations

import ast
import importlib
import inspect
import sys
from dataclasses import dataclass, field
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
README_PATH = _ROOT / "README.md"
DEMO_PATH = "demo/day2.py"


class DiagramError(ValueError):
    """A picture this tree no longer backs. Never rendered, never written."""


@dataclass(frozen=True)
class Node:
    """One box or one participant.

    `symbol` is optional because not every box is a function: the draft, the denial and the queue
    are none of them. A node without one is prose, and the guards say so rather than forcing a
    specification to invent a symbol for a thing that has none.
    """
    id: str
    label: str
    symbol: str | None = None


@dataclass(frozen=True)
class Edge:
    """One arrow.

    An arrow joining two symbol-bearing nodes is a **claim that one calls the other**, and it is
    checked as one. `callee` overrides the target's symbol where the participant is a class and the
    step names the method -- the review queue is entered twice, by `put` and by `items`, and a step
    that said only "the queue" would be unfalsifiable.

    `reply` marks the dashed return leg of a sequence diagram, which is an answer rather than a
    call. It is not an escape hatch: `check_edges` requires a reply to have a matching call in the
    opposite direction, so relabelling a call as a reply refuses instead of passing.
    """
    source: str
    target: str
    text: str = ""
    reply: bool = False
    callee: str | None = None
    dotted: bool = False
    lost: bool = False


@dataclass(frozen=True)
class Diagram:
    marker: str
    kind: str
    title: str
    nodes: tuple[Node, ...]
    edges: tuple[Edge, ...]
    groups: tuple[tuple[str, str, tuple[str, ...]], ...] = field(default=())


def _node_map(diagram: Diagram) -> dict[str, Node]:
    return {node.id: node for node in diagram.nodes}


def symbols_named(diagram: Diagram) -> tuple[str, ...]:
    """Every symbol the picture names, from its nodes and from its steps, in order and deduplicated."""
    seen: list[str] = []
    for symbol in [n.symbol for n in diagram.nodes] + [e.callee for e in diagram.edges]:
        if symbol is not None and symbol not in seen:
            seen.append(symbol)
    return tuple(seen)


def _module_source(where: str) -> tuple[ast.Module, str]:
    """The parsed source of `where`, which is either an importable module or a repository path.

    A path is parsed and never imported. `demo/` is not a package and running a demo from a
    generator would be a side effect this module has no business having; what is wanted from that
    file is its shape, and the shape is in the text.
    """
    if where.endswith(".py") or "/" in where:
        path = _ROOT / where
        if not path.exists():
            raise DiagramError(f"the diagram names {where!r}, and no such file is in the tree")
        return ast.parse(path.read_text(encoding="utf-8")), where
    try:
        module = importlib.import_module(where)
    except ImportError as exc:
        raise DiagramError(
            f"the diagram names the module {where!r}, which this tree cannot import: {exc}"
        ) from exc
    source = inspect.getsourcefile(module)
    if source is None:
        raise DiagramError(f"the module {where!r} has no source file to read a call graph from")
    return ast.parse(Path(source).read_text(encoding="utf-8")), where


def _definition(tree: ast.Module, qualname: str) -> ast.AST | None:
    """The `def` or `class` a dotted qualname names, or None."""
    node: ast.AST = tree
    for part in qualname.split("."):
        children = getattr(node, "body", [])
        found = None
        for child in children:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) \
                    and child.name == part:
                found = child
                break
        if found is None:
            return None
        node = found
    return node


def resolve(symbol: str) -> ast.AST:
    """The definition `symbol` names, as `where:qualname`. Raises `DiagramError` naming what is gone.

    Resolved through the AST for both shapes rather than through `getattr` for one and the AST for
    the other. A single route means a rename fails the same way wherever the symbol lives, and it
    means the thing resolved is the thing the call walk then reads -- a `getattr` can succeed on an
    attribute the source does not define, which is the one answer that would let a stale node pass.
    """
    where, _, qualname = symbol.partition(":")
    if not where or not qualname:
        raise DiagramError(f"{symbol!r} is not a `where:qualname` symbol")
    tree, label = _module_source(where)
    definition = _definition(tree, qualname)
    if definition is None:
        raise DiagramError(
            f"the diagram names {qualname!r} in {label}, and that definition is not there. "
            f"Rename the node or restore the symbol; a picture is not allowed to outlive it."
        )
    return definition


def check_symbols(diagram: Diagram) -> None:
    """Resolve every symbol the picture names, refusing on the first one the tree does not hold."""
    for symbol in symbols_named(diagram):
        resolve(symbol)


# --------------------------------------------------------------------------------------------
# The call walk. Every arrow between two symbols is checked against this and against nothing else.
# --------------------------------------------------------------------------------------------

#: Callees inside this package are followed transitively. `guarded_call` reaches `decide` through
#: `_decide_for`, so a walk that read one level would refuse the truest arrow in the first picture.
#: The same rule and the same reason as `tests/gates/test_hook.py::_pure_predicates_reachable_from`,
#: which was rooted one level too low once and stopped watching what it was written for.
FOLLOW = "chaperone.gates"


def _import_map(tree: ast.Module) -> tuple[dict[str, str], dict[str, str]]:
    """`({local name: the symbol it binds}, {local name: the module it binds})` for one file.

    Both, because `from chaperone.policy import citations` binds a module and
    `from chaperone.policy.citations import validate_citations` binds a function, and the two are
    written the same way. Collected over the whole tree rather than the module body: `sent_count`
    imports inside the function that uses it, and a map built from `tree.body` alone would call
    that call unresolvable.
    """
    names: dict[str, str] = {}
    modules: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules[alias.asname or alias.name] = alias.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                local = alias.asname or alias.name
                names[local] = f"{node.module}:{alias.name}"
                modules[local] = f"{node.module}.{alias.name}"
    return names, modules


def _parameters(function: ast.AST) -> tuple[frozenset[str], dict[str, ast.expr]]:
    """`(every parameter name, {parameter: its annotation})`, across all five argument kinds.

    The names are what makes a call on a parameter unresolvable rather than resolved to whatever
    the module happens to import under that name. `Gateway.call` invokes a `decide` parameter, and
    a walk without this reports a call to `chaperone.gates.engine:decide` that nothing makes --
    the one false positive that would back the middle of the chain for the wrong reason.
    """
    args = getattr(function, "args", None)
    if args is None:
        return frozenset(), {}
    every = [*args.posonlyargs, *args.args, *args.kwonlyargs]
    if args.vararg:
        every.append(args.vararg)
    if args.kwarg:
        every.append(args.kwarg)
    return (frozenset(a.arg for a in every),
            {a.arg: a.annotation for a in every if a.annotation is not None})


def _annotated_classes(function: ast.AST, names: dict[str, str]) -> dict[str, str]:
    """`{parameter: the class symbol its annotation names}`.

    This is what turns `gateway.call(...)` into `Gateway.call` and `queues.put(...)` into
    `ReviewQueues.put` -- a receiver resolved through the signature rather than matched by method
    name. `_shipped_call_sites` in `tests/test_readme_claims.py` matches names because it is looking
    for one name across the tree; an arrow needs to know *whose* method was entered, and the
    annotation is where the caller says so.

    Read off the AST, never off `inspect.signature`: every module here carries
    `from __future__ import annotations`, so the runtime annotation is a string.
    """
    _, annotations = _parameters(function)
    typed: dict[str, str] = {}
    for parameter, annotation in annotations.items():
        if isinstance(annotation, ast.Name) and annotation.id in names:
            typed[parameter] = names[annotation.id]
    return typed


def _constructed_locals(function: ast.AST, names: dict[str, str]) -> dict[str, str]:
    """`{local: the class symbol it was constructed from}` for `x = SomeClass(...)`.

    `demo/day2.py` builds its queue, its store and its gateway as locals and then calls methods on
    them, so without this the whole scene resolves to nothing. The same local-flow idiom
    `tests/gates/test_hook.py` uses to follow a record into the branch that consults it.
    """
    built: dict[str, str] = {}
    for node in ast.walk(function):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) \
                and isinstance(node.value, ast.Call) \
                and isinstance(node.value.func, ast.Name) \
                and node.value.func.id in names:
            built[node.targets[0].id] = names[node.value.func.id]
    return built


def _module_level_names(tree: ast.Module) -> frozenset[str]:
    return frozenset(
        node.name for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    )


def _callees(tree: ast.Module, function: ast.AST, where: str, owner: str | None) -> list[str]:
    """Every symbol `function` calls, resolved the way the interpreter would resolve it.

    Unresolvable calls are **skipped rather than guessed**. A callable handed in as an argument, an
    attribute of something this file never named, a call on a `self` attribute that is not a
    method: none of them is a call to any symbol this module can point at, and inventing one would
    put an arrow in a picture on the strength of a matching name.
    """
    names, modules = _import_map(tree)
    shadowed, _ = _parameters(function)
    typed = _annotated_classes(function, names)
    built = _constructed_locals(function, names)
    module_level = _module_level_names(tree)

    found: list[str] = []
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        symbol = None
        if isinstance(func, ast.Name):
            if func.id in shadowed:
                continue
            if func.id in names:
                symbol = names[func.id]
            elif func.id in module_level:
                symbol = f"{where}:{func.id}"
        elif isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            receiver = func.value.id
            if receiver in typed:
                symbol = f"{typed[receiver]}.{func.attr}"
            elif receiver in built:
                symbol = f"{built[receiver]}.{func.attr}"
            elif receiver in modules:
                symbol = f"{modules[receiver]}:{func.attr}"
            elif receiver == "self" and owner is not None:
                symbol = f"{where}:{owner}.{func.attr}"
        if symbol is not None and symbol not in found:
            found.append(symbol)
    return found


_SOURCE_CACHE: dict[str, ast.Module] = {}
#: Keyed on `(entry, follow)`, never entry alone: the walk's answer depends on both, and a cache
#: that forgot the scope answered a default-scope question with a narrowed walk's result.
_WALK_CACHE: dict[tuple[str, str], dict[str, tuple[str, ...]]] = {}


def _tree_for(where: str) -> ast.Module:
    if where not in _SOURCE_CACHE:
        _SOURCE_CACHE[where] = _module_source(where)[0]
    return _SOURCE_CACHE[where]


def call_paths(entry: str, follow: str = FOLLOW) -> dict[str, tuple[str, ...]]:
    """`{callee symbol: the route from `entry` to it}`, transitively through `follow`.

    The route is carried rather than a bare membership set, because "reachable" without a route is
    the one answer a reviewer cannot check and a refusal cannot print. `guarded_call` reaching
    `decide` is only convincing beside `guarded_call -> _decide_for -> decide`.

    **A class is recorded and never followed.** Constructing a `Checker` is not a call into every
    method a `Checker` has, and following one would let an arrow be backed by a route nobody takes.
    """
    if (entry, follow) in _WALK_CACHE:
        return _WALK_CACHE[(entry, follow)]
    reached: dict[str, tuple[str, ...]] = {}
    seen: set[str] = set()
    pending: list[tuple[str, tuple[str, ...]]] = [(entry, (entry,))]
    while pending:
        symbol, route = pending.pop()
        if symbol in seen:
            continue
        seen.add(symbol)
        where, _, qualname = symbol.partition(":")
        try:
            tree = _tree_for(where)
        except DiagramError:
            continue
        definition = _definition(tree, qualname)
        if not isinstance(definition, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        owner = qualname.rsplit(".", 1)[0] if "." in qualname else None
        for callee in _callees(tree, definition, where, owner):
            if callee not in reached:
                reached[callee] = (*route, callee)
            if callee.split(":")[0].startswith(follow) and callee not in seen:
                pending.append((callee, (*route, callee)))
    _WALK_CACHE[(entry, follow)] = reached
    return reached


def call_pairs(diagram: Diagram) -> tuple[tuple[str, str], ...]:
    """`(caller symbol, callee symbol)` for every arrow that claims a call.

    An arrow touching a box that names no symbol claims nothing about the call graph -- the draft,
    the denial and the queue are not functions -- so it is not here. Two guards stop that being a
    way out, and they cover different directions: `EDGE_FLOOR` in `tests/test_diagram.py` names the
    relationships that must keep being drawn, so a checked arrow cannot be quietly dropped; and
    `check_drawn_lane_containment` refuses any arrow leaving the quality group, so a symbol-less
    edge cannot quietly join the lanes the first picture exists to keep apart.
    """
    nodes = _node_map(diagram)
    pairs: list[tuple[str, str]] = []
    for edge in diagram.edges:
        if edge.reply:
            continue
        caller = nodes[edge.source].symbol if edge.source in nodes else None
        callee = edge.callee or (nodes[edge.target].symbol if edge.target in nodes else None)
        if caller is not None and callee is not None:
            pairs.append((caller, callee))
    return tuple(pairs)


def check_edges(diagram: Diagram) -> None:
    """Refuse a picture whose arrows the tree no longer backs, naming the pair that broke."""
    nodes = _node_map(diagram)
    for edge in diagram.edges:
        for end in (edge.source, edge.target):
            if end not in nodes:
                raise DiagramError(
                    f"{diagram.marker} draws an arrow touching {end!r}, which is not a declared node"
                )

    calls = {(edge.source, edge.target) for edge in diagram.edges if not edge.reply}
    for edge in diagram.edges:
        if edge.reply and (edge.target, edge.source) not in calls:
            raise DiagramError(
                f"{diagram.marker} draws a reply from {edge.source!r} to {edge.target!r} with no "
                f"call going the other way. A reply is an answer to a call, and an arrow that is "
                f"neither is an arrow nothing checks."
            )

    for caller, callee in call_pairs(diagram):
        reached = call_paths(caller)
        if callee not in reached:
            raise DiagramError(
                f"{diagram.marker} draws {caller} --> {callee}, and no call from {caller} reaches "
                f"{callee}. It reaches {len(reached)} symbols and that is not one of them. Redraw "
                f"the arrow or restore the call; a picture is not allowed to outlive it."
            )


def check_lane_separation(entries: tuple[str, ...] = (), forbidden: str = "") -> None:
    """Refuse a tree in which the permission lane can reach the quality judge.

    The thesis of the first picture is an absence: a score is a measurement and authorizes nothing,
    so no arrow leaves the quality lane. An absence cannot be derived -- there is no edge to find --
    but a refusal can be, and this is it. The day `decide` consults a score, the build stops rather
    than the picture continuing to show two lanes that are no longer two.
    """
    entries = entries or (CHOKEPOINT, ENGINE)
    forbidden = forbidden or JUDGE
    for entry in entries:
        reached = call_paths(entry)
        if forbidden in reached:
            raise DiagramError(
                f"{entry} now reaches {forbidden} by {' -> '.join(reached[forbidden])}. The first "
                f"diagram draws two lanes that do not meet, and this tree has joined them."
            )


# --------------------------------------------------------------------------------------------
# The values read out of the tree at build time. None of these may be typed into this file, and
# `test_no_value_the_diagrams_derive_is_also_typed_into_the_generator` is what says so.
# --------------------------------------------------------------------------------------------


def queue_destination() -> str:
    """Where a redirect routes, taken over the module's own set of redirect dispositions.

    `destination_for` is the one place in this repository that decides a queue name, and the first
    picture ends at that queue. Reading the answer here rather than typing it is the difference
    between a picture that follows a rename and a picture that has to be remembered.

    Taken over `REDIRECT_DISPOSITIONS` rather than over one member of `Disposition`, so this reads
    the set the engine keeps rather than opening a second place that names a disposition. Two
    different answers is a picture that cannot show one queue, and it refuses instead of picking.
    """
    from chaperone.gates.engine import REDIRECT_DISPOSITIONS, destination_for

    if not REDIRECT_DISPOSITIONS:
        raise DiagramError("the engine names no redirect disposition, so no queue can be drawn")
    named = {destination_for(disposition) for disposition in REDIRECT_DISPOSITIONS}
    if None in named or len(named) != 1:
        raise DiagramError(
            f"destination_for answers {sorted(str(n) for n in named)} across the redirect "
            f"dispositions; the diagram draws one queue and cannot draw these"
        )
    return named.pop()


@dataclass(frozen=True)
class Scene:
    """The scene `demo/day2.py` runs, read out of that file rather than retold here."""
    question: str
    body: str
    tool: str
    violation_class: str
    span: str


def _string_at(node: ast.AST | None, what: str) -> str:
    if not isinstance(node, ast.Constant) or not isinstance(node.value, str) or not node.value:
        raise DiagramError(f"{what} is not a non-empty string literal in {DEMO_PATH}")
    return node.value


def _keyword(call: ast.Call, name: str, what: str) -> ast.expr:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    raise DiagramError(f"{what}: {DEMO_PATH} no longer passes {name!r}")


def _call_named(scope: ast.AST, name: str, what: str) -> ast.Call:
    for node in ast.walk(scope):
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == name:
            return node
    raise DiagramError(f"{what}: {DEMO_PATH} no longer constructs a {name}")


def read_demo_scene() -> Scene:
    """The inbound question, the drafted body, the tool, the blocked class and the quoted span.

    All five out of `demo/day2.py`'s AST, so the sequence diagram is the scene that file runs. The
    file is **parsed and never imported**: `demo/` is not a package, and a generator that executed a
    demo to draw a picture of it would have side effects a generator has no business having.

    The class is resolved through the demo's own import of it and then through the enum, so what
    reaches the picture is the value the gate would return and not the attribute name. A member
    renamed in `policy/types.py` fails here.
    """
    tree = _tree_for(DEMO_PATH)
    draft = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 \
                and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "DRAFT" \
                and isinstance(node.value, ast.Call):
            draft = node.value
    if draft is None:
        raise DiagramError(f"{DEMO_PATH} no longer assigns a DRAFT, so no scene can be drawn")

    thread = _keyword(draft, "thread", "the inbound question")
    if not isinstance(thread, ast.Tuple) or not thread.elts:
        raise DiagramError(f"{DEMO_PATH}'s DRAFT carries no thread, so there is no question to draw")
    question = _string_at(
        _keyword(_call_named(thread.elts[0], "Message", "the inbound question"), "body",
                 "the inbound question"),
        "the inbound question",
    )

    scripted = _definition(tree, "scripted")
    if scripted is None:
        raise DiagramError(f"{DEMO_PATH} no longer defines scripted, so its scene cannot be drawn")
    verdict = _call_named(scripted, "Verdict", "the scripted checker's answer")
    klass = _keyword(verdict, "violation_class", "the class the scripted checker names")
    if not isinstance(klass, ast.Attribute) or not isinstance(klass.value, ast.Name):
        raise DiagramError(f"{DEMO_PATH}'s scripted verdict no longer names a class by attribute")

    names, _ = _import_map(tree)
    if klass.value.id not in names:
        raise DiagramError(f"{DEMO_PATH} does not import {klass.value.id!r}, so its class is unresolvable")
    where, _, enum_name = names[klass.value.id].partition(":")
    enum = getattr(importlib.import_module(where), enum_name, None)
    member = getattr(enum, klass.attr, None)
    if member is None:
        raise DiagramError(f"{enum_name} has no member {klass.attr!r}, so the blocked class is gone")

    return Scene(
        question=question,
        body=_string_at(_keyword(draft, "body", "the drafted body"), "the drafted body"),
        tool=_string_at(_keyword(draft, "tool_name", "the tool the draft names"),
                        "the tool the draft names"),
        violation_class=str(member.value),
        span=_string_at(_keyword(verdict, "span", "the span the scripted checker quotes"),
                        "the span the scripted checker quotes"),
    )


QUEUE_NAME = queue_destination()
SCENE = read_demo_scene()

#: Every string the pictures take from the tree, by what it is. Read by
#: `test_no_value_the_diagrams_derive_is_also_typed_into_the_generator`, which requires each to
#: reach a picture **and** to be absent from this file: a value that is both derived and typed
#: renders identically today and goes on rendering after the tree has moved.
DERIVED_STRINGS = {
    "the review queue's name, from destination_for": QUEUE_NAME,
    "the inbound question": SCENE.question,
    "the drafted body": SCENE.body,
    "the tool the draft names": SCENE.tool,
    "the class the scripted checker names": SCENE.violation_class,
    "the span the scripted checker quotes": SCENE.span,
}


# --------------------------------------------------------------------------------------------
# The two specifications. Structure is derived and checked above and below; the prose is authored.
# --------------------------------------------------------------------------------------------

JUDGE = "chaperone.evals.judge:score_quality"
JUDGE_PROMPT = "chaperone.evals.judge:build_judge_messages"
CHOKEPOINT = "chaperone.gates.hook:guarded_call"
GATEWAY_CALL = "chaperone.audit.gateway:Gateway.call"
ENGINE = "chaperone.gates.engine:decide"
ACT_CLASSES = "chaperone.policy.act_classes:evaluate_act_classes"
CITATIONS = "chaperone.policy.citations:validate_citations"
TRIPWIRES = "chaperone.policy.tripwires:evaluate_tripwires"
CHECKER = "chaperone.gates.checker:Checker.check"
DESTINATION = "chaperone.gates.engine:destination_for"
QUEUES = "chaperone.gates.queues:ReviewQueues"
QUEUE_PUT = "chaperone.gates.queues:ReviewQueues.put"
QUEUE_ITEMS = "chaperone.gates.queues:ReviewQueues.items"
CHAIN = "chaperone.audit.chain:verify"
SCRIPTED = f"{DEMO_PATH}:scripted"

TWO_LANES = Diagram(
    marker="two-lanes",
    kind="flowchart",
    title="One draft, two lanes",
    nodes=(
        Node("draft", "One draft, one moment"),
        Node("judge", "score_quality", symbol=JUDGE),
        Node("judge_prompt", "build_judge_messages", symbol=JUDGE_PROMPT),
        Node("scored", "PASS. Well grounded, fluent, and it answers what was asked."),
        Node("measurement",
             "The lane stops here. No arrow leaves it, and no gate reads this score."),
        Node("chokepoint", "guarded_call", symbol=CHOKEPOINT),
        Node("gateway", "Gateway.call", symbol=GATEWAY_CALL),
        Node("engine", "decide", symbol=ENGINE),
        Node("acts", "evaluate_act_classes", symbol=ACT_CLASSES),
        Node("citations", "validate_citations", symbol=CITATIONS),
        Node("tripwires", "evaluate_tripwires", symbol=TRIPWIRES),
        Node("checker", "Checker.check", symbol=CHECKER),
        Node("denial", f"BLOCK: {SCENE.violation_class} fired; the {SCENE.tool} tool is "
                       f"never entered"),
        Node("destination", "destination_for", symbol=DESTINATION),
        Node("put", "ReviewQueues.put", symbol=QUEUE_PUT),
        Node("queue", f"the {QUEUE_NAME} queue"),
        Node("log", "Two entries, hash-linked: the intent and the outcome"),
        Node("chain", "verify", symbol=CHAIN),
    ),
    edges=(
        Edge("draft", "judge", "the quality lane is handed it"),
        Edge("judge", "judge_prompt", "assembles the judge prompt"),
        Edge("judge", "scored", "returns a score"),
        Edge("scored", "measurement", "and that is all it is"),
        Edge("draft", "chokepoint", "the same draft"),
        Edge("chokepoint", "gateway", "every guarded call goes through it"),
        Edge("chokepoint", "engine", "through _decide_for"),
        Edge("engine", "acts", "act-classes"),
        Edge("engine", "citations", "citations"),
        Edge("engine", "tripwires", "tripwires"),
        Edge("engine", "checker", "the checker"),
        Edge("engine", "denial", "findings and a disposition"),
        Edge("denial", "destination", "routes where"),
        Edge("chokepoint", "destination", "asks where; no queue name typed"),
        Edge("destination", "queue", "returns"),
        Edge("chokepoint", "put", "at the chokepoint, before it returns"),
        Edge("put", "queue", "the escalation payload"),
        Edge("gateway", "log", "writes an intent and an outcome"),
        Edge("log", "chain", "hash-linked"),
    ),
    groups=(
        ("quality", "QUALITY LANE. A measurement, and it authorizes nothing.",
         ("judge", "judge_prompt", "scored", "measurement")),
        ("permission", "PERMISSION LANE. An authorization boundary.",
         ("chokepoint", "gateway", "engine", "acts", "citations", "tripwires", "checker")),
    ),
)

DAY2 = Diagram(
    marker="day2",
    kind="sequence",
    title="The scene demo/day2.py runs",
    nodes=(
        Node("investor", "Investor"),
        Node("demo", "demo/day2.py scripted", symbol=SCRIPTED),
        Node("judge", "score_quality", symbol=JUDGE),
        Node("gate", "guarded_call", symbol=CHOKEPOINT),
        Node("gateway", "Gateway.call", symbol=GATEWAY_CALL),
        Node("engine", "decide", symbol=ENGINE),
        Node("checker", "Checker.check", symbol=CHECKER),
        Node("send", f"{SCENE.tool}, the tool the registry records"),
        Node("queues", f"the {QUEUE_NAME} queue", symbol=QUEUES),
        Node("chain", "verify", symbol=CHAIN),
    ),
    edges=(
        Edge("investor", "demo", SCENE.question),
        Edge("demo", "judge", f"the draft: {SCENE.body}"),
        Edge("judge", "demo",
             "PASS. Grounded, fluent, and it answers what was asked -- a measurement.", reply=True),
        Edge("demo", "gate", f"the {SCENE.tool} call, through the executor chokepoint"),
        Edge("gate", "gateway", "an intent entry, written before anything decides"),
        Edge("gate", "engine", "the one decision both layers take, through _decide_for"),
        Edge("engine", "checker", "the three published constraints, put to the checker"),
        Edge("checker", "engine",
             f"violates: {SCENE.violation_class}, quoting {SCENE.span}", reply=True),
        Edge("engine", "gate", f"BLOCK {SCENE.violation_class}, and the disposition is futile",
             reply=True),
        Edge("gate", "send", f"the {SCENE.tool} tool is never entered", lost=True),
        Edge("gate", "queues", "the escalation, put where destination_for says", callee=QUEUE_PUT),
        Edge("gate", "demo", "denied and redirected, before it returns", reply=True),
        Edge("demo", "queues", f"reads the escalation back out of {QUEUE_NAME}", callee=QUEUE_ITEMS),
        Edge("demo", "chain", "verifies the log"),
        Edge("chain", "demo", "ok. Two entries: the intent and the outcome.", reply=True),
    ),
)

DIAGRAMS = (TWO_LANES, DAY2)


# --------------------------------------------------------------------------------------------
# Rendering, and the span of `README.md` this generator owns.
# --------------------------------------------------------------------------------------------

GENERATED_BY = "%% Generated by tools/diagram.py from the tree it draws. Do not edit by hand."


def begin_marker(name: str) -> str:
    return f"<!-- diagram:{name}:begin -->"


def end_marker(name: str) -> str:
    return f"<!-- diagram:{name}:end -->"


def _label(text: str) -> str:
    """A mermaid label, quoted so punctuation in the prose cannot end the box early."""
    return f'"{text}"'


def _flowchart(diagram: Diagram) -> list[str]:
    grouped = {node_id for _, _, members in diagram.groups for node_id in members}
    lines = ["flowchart TD", f"    {GENERATED_BY}"]
    for node in diagram.nodes:
        if node.id not in grouped:
            lines.append(f"    {node.id}[{_label(node.label)}]")
    inside = {node.id: node for node in diagram.nodes}
    for group_id, title, members in diagram.groups:
        lines.append(f"    subgraph {group_id}[{_label(title)}]")
        for member in members:
            lines.append(f"        {member}[{_label(inside[member].label)}]")
        lines.append("    end")
    for edge in diagram.edges:
        arrow = "-.->" if edge.dotted else "-->"
        text = _label(edge.text).join("||") if edge.text else " "
        lines.append(f"    {edge.source} {arrow}{text}{edge.target}")
    return lines


def _sequence(diagram: Diagram) -> list[str]:
    lines = ["sequenceDiagram", f"    {GENERATED_BY}", "    autonumber"]
    for node in diagram.nodes:
        lines.append(f"    participant {node.id} as {node.label}")
    for edge in diagram.edges:
        arrow = "-->>" if edge.reply else ("-x" if edge.lost else "->>")
        lines.append(f"    {edge.source}{arrow}{edge.target}: {edge.text}")
    return lines


def render(diagram: Diagram) -> str:
    """One fenced mermaid block. Separable from the write, so a test can read it without a tree edit.

    The same split `tools/report.py` keeps between `render` and `write_report`, for the same reason:
    a generator that can only be observed through the file it rewrites cannot be asserted about
    without touching the file it rewrites.
    """
    body = _flowchart(diagram) if diagram.kind == "flowchart" else _sequence(diagram)
    return "\n".join(["```mermaid", *body, "```"]) + "\n"


def replace_between(document: str, name: str, body: str) -> str:
    """`document` with the span between one marker pair replaced, and every other byte untouched.

    Refusal over guessing, three times over. A marker that is missing, duplicated or reversed leaves
    this with no single span to own, and the repair that suggests itself in each case -- appending,
    taking the first, swapping the ends -- writes a picture somewhere nobody is reading. The README
    is seventy kilobytes of argument and two of picture, and the generator owns the two.
    """
    begin, end = begin_marker(name), end_marker(name)
    for marker in (begin, end):
        found = document.count(marker)
        if found != 1:
            raise DiagramError(
                f"the document carries {found} of the {name} marker {marker!r}, and this generator "
                f"owns exactly one span per picture. It writes nothing rather than guessing which."
            )
    opens, closes = document.index(begin), document.index(end)
    if opens > closes:
        raise DiagramError(
            f"the {name} markers are the wrong way round: the end marker stands before the begin "
            f"marker, so there is no span between them to own"
        )
    return document[:opens + len(begin)] + "\n" + body + document[closes:]


def write_readme(path: Path = README_PATH) -> None:
    """Replace the span each marker pair owns, and leave every other byte of the page as it was.

    **Read, replace in memory, write once.** The document is built across both pictures before the
    file is opened, so a refusal on the second leaves the page as it was rather than half rewritten
    with the marker that would have caught it already consumed. `tools/report.py` renders before it
    writes for the same reason.

    Read back from `path` rather than from `README_PATH`, so this is applied to the document it is
    about to overwrite. A writer that took its input from one place and its output from another
    would be green on a page it had never read, and could not be idempotent on its own output --
    which is the one property `git diff --exit-code` in CI is actually testing.

    `newline="\\n"` because text mode translates on Windows and `.gitattributes` pins this repository
    to LF, which has flipped a file here before.
    """
    path = Path(path)
    document = path.read_text(encoding="utf-8")
    for diagram in DIAGRAMS:
        document = replace_between(document, diagram.marker, render(diagram))
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(document)


def check_drawn_lane_containment(diagram: Diagram) -> None:
    """Refuse any arrow that leaves a group named "quality", whatever its endpoints name.

    `check_lane_separation` refuses a TREE in which the permission lane reaches the judge, and
    `call_pairs` checks every arrow between two symbols -- but an arrow between prose boxes claims
    no call, so neither would see a spec edit drawing the quality lane into the permission lane.
    The first picture's thesis is that absence, so the drawing is held to it directly: replies
    included, because a reader does not distinguish an answer-arrow from a call-arrow when both
    cross the one gap the picture exists to show empty.
    """
    for group_id, _label, members in diagram.groups:
        if group_id != "quality":
            continue
        inside = set(members)
        for edge in diagram.edges:
            if edge.source in inside and edge.target not in inside:
                raise DiagramError(
                    f"the drawing sends an arrow out of the quality lane, {edge.source} -> "
                    f"{edge.target}: a measurement authorizes nothing, so no arrow may leave that "
                    f"group, and this one is refused before it can render the lanes joined"
                )


def check_all() -> None:
    """Every refusal this module can make, before a byte is written."""
    for diagram in DIAGRAMS:
        check_symbols(diagram)
        check_edges(diagram)
        check_drawn_lane_containment(diagram)
    check_lane_separation()


def main() -> int:
    check_all()
    write_readme()
    print(f"{README_PATH}: two diagrams written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
