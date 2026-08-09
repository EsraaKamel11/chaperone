"""One payload dict, one `(Draft, Record, ActContext)` triple, for every layer that enforces here.

Pure: no I/O, no clock, no LLM client, and a function of its argument alone. That is what lets the
construction live in `policy/` at all, and it is the same reason `arguments.py` gives for holding
`unsendable_in`: **both** enforcement layers need this and neither may import the other.
`tools/policy_hook.py` cannot import `gates/`, which would pull the model layer into a guard that
must run with nothing installed; and the package the wheel ships is `src/chaperone` alone, so a
module here that imported `tools/` would depend on a directory it does not ship. Until this module
existed the out-of-process guard was the only thing that knew how to read a payload, so a second
decision surface had two choices -- import from `tools/`, or keep a second copy of the trust
boundary. A trust boundary written out twice is the drift design spec 6.3 forbids. One function,
imported twice, cannot drift.

**`CONSENTED` and `GRANTED` are constants here rather than fields of the payload.** A guard that
accepts its own permissions from the caller is not a guard, so this is the fail-closed choice -- but
it means a deployment can configure two layers apart, and the shared artefact across layers is the
predicate set, never the configuration. A test exhibits a jurisdiction two layers disagree on.
Moving them out of the guard module changed which file writes them down and not where they come
from: still never the payload, which is the whole of the property.
"""
from __future__ import annotations

from chaperone.policy.act_classes import ActContext
from chaperone.policy.types import Draft, Message, Record, ViolationClass

CONSENTED = frozenset({"US", "UK"})
GRANTED = frozenset({"send_message", "send_reply", "draft_message", "read_policy"})

#: The violation classes a layer building its inputs here cannot decide, so the subset relation is
#: declared rather than discovered. Declared as a set rather than prose so the predicate-parity test
#: can assert it, and so widening it is an edit somebody has to make on purpose.
#:
#: **The criterion is: no layer building its inputs here holds a trustworthy source for the input
#: the class turns on.** That is wider than the criterion first written here, which was "what state
#: makes impossible", and the widening is deliberate rather than a stretch -- the second entry below
#: fits the new wording and not the old one, and leaving the old wording in place would have been
#: the same defect the entry records.
#:
#: - `act:send_cap_exceeded`. `evaluate_act_classes` can produce it, but only from a count that
#:   lives in the audit log a stateless guard cannot read -- `sent_count` is filled from the payload
#:   below -- so a caller who omits the count gets an allow where the in-process gate denies.
#: - `act:no_approval_token`. Worse than absent: `approval_token` and `tier` arrive in `tool_input`,
#:   so the agent being judged supplies the evidence. `{"tier": 1}` puts the action below the rule
#:   and any non-`None` token satisfies it, because nothing here can validate an approval. A guard
#:   that accepts its own permissions from the caller is not a guard -- this module's docstring
#:   holds `CONSENTED` and `GRANTED` as constants for exactly that reason -- and the same remedy
#:   does not transfer: an approval is granted per action, so a token pinned as a module constant
#:   or read once from the environment would make the predicate "is the deployment configured"
#:   rather than "was this action approved", and a `tier` pinned high would stop reading the
#:   action's tier at all. A declaration that the class is undecidable here is the true statement;
#:   a constant that made it *look* decidable would be the stronger claim this repository exists to
#:   refuse. `tier`'s fail-closed default of 2 is kept: it closes absence, which is the half that
#:   can be closed.
#:
#: - `act:figure_not_in_record`. The same shape as the entry above, one step further out: not the
#:   evidence *for* the action but the ground truth it is checked *against*. `record` arrives in
#:   `tool_input`, so a draft stating a figure the real record does not hold is allowed by a payload
#:   carrying a record that says it does, and `validate_citations` resolves against that same field,
#:   so the citation half goes with it -- `cited_fields` defaulting to `()` suppresses that half a
#:   second way. The constant remedy does not transfer here either, for a sharper reason than above:
#:   a record pinned in this module would make the predicate "does the draft agree with one fixed
#:   mandate" rather than "does it agree with this deal's record", and would refuse every correct
#:   draft about every other deal. A stateless guard holds no per-deal record, which is this
#:   criterion exactly.
#:
#: **Why a `Draft` is not on this list, when every field of one also arrives in `tool_input`.** A
#: draft is the action's description of itself and is the thing being judged; a `Record` and an
#: `ActContext` are what it is judged against, and only the second kind is evidence. That is why
#: `act:jurisdiction_not_consented` and `act:tool_outside_grant` are absent: both compare the
#: action's self-description against `CONSENTED` and `GRANTED`, held here as constants precisely so
#: the caller cannot supply them. **The residual, because the distinction is about this payload
#: shape rather than about the predicates:** a caller can still misdescribe its own action, and
#: `jurisdiction` stops being self-description the moment a real send tool derives it from the
#: recipient rather than taking it as an argument.
#:
#: **This set is no longer maintained by hand.** It was, and it was found under-inclusive twice --
#: created holding `act:send_cap_exceeded` alone, then grown for the approval class, then for the
#: record. The count in that sentence is read off this set's history, not recalled.
#: `tests/gates/test_hook.py` now derives the requirement, and reads two files to do it: this
#: module's own AST for which `Record` and `ActContext` inputs `build_act_inputs` fills from
#: `tool_input`, `tools/policy_hook.py`'s `main` for which predicates are then handed those objects,
#: and each predicate's AST for which class each branch turns on. So a class that starts turning on
#: payload evidence fails on the commit that does it rather than on the review that happens to
#: notice.
#:
#: **The residual, because declaring a gap does not close it.** Every predicate still runs, so a
#: layer building from here can still *deny* on `act:no_approval_token` and on
#: `act:figure_not_in_record` from caller input. What it cannot do is refuse to be talked out of
#: either denial, and that is the direction the declaration names: these entries mark classes that
#: cannot be relied on to fire, not classes that never fire.
#:
#: This set holds only gaps that cannot be closed by a caller. It briefly also covered unconsumed
#: payload keys, which was wrong twice over: refusing them is a pure function of the payload and
#: needs no state at all, and a subset declaration that lists a *closable* gap reads as exhaustive
#: while being incomplete -- worse than no declaration. That gap is closed by running
#: `unsendable_finding` over the keys outside `CONSUMED_KEYS`, which `tools/policy_hook.py` does
#: ahead of every predicate; it is not declared here.
UNENFORCEABLE_HERE = frozenset({
    ViolationClass.SEND_CAP_EXCEEDED,
    ViolationClass.NO_APPROVAL_TOKEN,
    ViolationClass.FIGURE_NOT_IN_RECORD,
})

#: Every key of `tool_input` this module reads. Anything else is an argument no predicate consumes,
#: and it has to be checked as outbound content rather than ignored: nine consumed keys and a silent
#: pass on the rest meant `{"extra_text": "Returns are guaranteed."}` rode through on an otherwise-
#: compliant payload while the in-process gate denied it -- design spec 6.3 false again, one layer
#: over from where it was first false.
#:
#: **That check is the caller's, and this set is what stops it being written twice.**
#: `build_act_inputs` reads these ten keys and passes silently over the rest; `tools/policy_hook.py`
#: runs `unsendable_finding` over the difference before it consults any predicate. A surface that
#: builds from here and skips that step has the leak above back, which is why the set is exported
#: rather than inlined into the builder below.
#:
#: A test derives this set from this module's own AST rather than trusting the literal, so a key
#: read here and forgotten here cannot exist.
CONSUMED_KEYS = frozenset({
    "body", "cited_fields", "jurisdiction", "domain", "tool_name", "record",
    "approval_token", "tier", "sent_count", "send_cap",
})


def build_act_inputs(payload: dict) -> tuple[Draft, Record, ActContext]:
    """The three objects every deterministic predicate takes, read out of one hook payload.

    **Two guards that belong beside this one are deliberately not in it**, because both need
    something a pure constructor does not have -- somewhere to send a refusal -- and a caller that
    omits either is weaker than `tools/policy_hook.py`:

    - **An absent `body` is not an empty one.** `tool_input["body"]` is a subscript, so a payload
      carrying no body raises here rather than scoring a blank draft as clean. `policy_hook.main`
      turns that case into a block *before* calling this, which is what makes the subscript safe
      there; a caller that does not check first gets the raise.
    - **Keys outside `CONSUMED_KEYS` are ignored here.** They reach no predicate, so they must be
      checked as outbound content by the caller. `policy_hook.main` runs `unsendable_finding` over
      the difference as its first finding.
    """
    tool_input = payload.get("tool_input") or {}

    # A tool's own name is not one of its arguments: the runtime sends it beside `tool_input`,
    # which is why `guard_edit.py` reads `tool_input.file_path` and no tool name at all. So the
    # payload level is read *first* and the argument level is only a fallback. Preferring the
    # argument level is the same confused deputy `_decide_for` refuses one layer up: a payload
    # naming `wire_funds` beside an argument naming `send_message` would have had its grant checked
    # against the tool that is not being run.
    #
    # Falls back to "" rather than None: `evaluate_act_classes` skips the grant check entirely on
    # None, so a payload naming no tool would otherwise be the one payload never checked at all.
    tool_name = payload.get("tool_name") or tool_input.get("tool_name") or ""

    draft = Draft(
        thread=(Message(role="investor", body=""),),
        body=tool_input["body"],
        cited_fields=tuple(tool_input.get("cited_fields", ())),
        recipient_jurisdiction=tool_input.get("jurisdiction", ""),
        recipient_domain=tool_input.get("domain", "example.test"),
        tool_name=tool_name,
    )
    record = Record(fields=tool_input.get("record", {}))
    context = ActContext(
        # No default token. The old default was the very string the act-class looks for, so a
        # payload carrying no token satisfied the tier-2 rule. A guard may not invent the evidence
        # it is guarding, and tier defaults high for the same reason.
        approval_token=tool_input.get("approval_token"),
        tier=int(tool_input.get("tier", 2)),
        consented_jurisdictions=CONSENTED,
        granted_tools=GRANTED,
        sent_count=int(tool_input.get("sent_count", 0)),
        send_cap=int(tool_input.get("send_cap", 10_000)),
    )
    return draft, record, context
