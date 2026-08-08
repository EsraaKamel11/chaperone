# Audit walkthrough

One denied send, entry by entry.

The hash chain is the least interesting thing on this page. Chains are easy to build and most readers
have built one. **The idea worth the walkthrough is that this log is designed as an enforcement
input, not as a report.** The send cap is a predicate over a count of intent entries, so a lost line
would not be a gap in the forensics, it would be a predicate failing open. That single fact is what
forces write-ahead ordering, fsync, binary append, and torn-tail detection, none of which a pure
record would need.

**Read the rest of this page in that tense.** `Gateway.sent_count()` in
`src/chaperone/audit/gateway.py` derives the count, and **no shipped path derives one from this log
and hands it to an `ActContext`**. That is the narrow claim and it is the one that is true: the
wider version this page used to carry -- *called from `tests/audit/test_send_cap.py` and nowhere
else, so nothing shipped reads it* -- was refutable, because `Gateway.sent_count` is a two-line
delegation to `counted_sends` and `tools/perturbation_log.py` calls that twice in shipped, non-test
code. It **prints** the number beside a torn-tail count; it does not put it in a context any
predicate reads. The durability argument below is what makes the count trustworthy once it is wired
in; it is not evidence that it is wired in. See [B0 in the failure-mode catalog](failure-modes.md).

---

## 1. What the demo writes

`demo/day2.py` denies one send and prints `AUDIT -> 2 entries, chain verifies: True`. Two entries, for
one attempted call:

```
seq 0   intent    tool=send_message   arg_digest=<sha256>   -
seq 1   outcome   tool=send_message   arg_digest=<sha256>   denied: content:advises_on_merits
```

The `arg_digest` is identical on both lines. That is what pairs them, and
`test_an_intent_and_its_outcome_carry_the_same_argument_digest` holds it.

---

## 2. Why the intent is written before the effect

The obvious design writes one entry after the call, recording what happened. It has a hole: if the
process dies between the effect and the write, the effect occurred and the record says nothing.

For a pure audit log that is a forensics gap, recoverable from the counterparty's side. **Here it
would be a permission failure**, because `act:send_cap_exceeded` is a predicate over a count of intent
entries. A lost line would mean the count came back lower than the truth, and the cap would then
permit a send it should have refused. The log going quiet makes the system more permissive, which is
the worst possible direction for an error in an enforcement layer.

Conditional, because that is the state of the tree: the count is not wired to the predicate yet, per
the note at the top of this page.

So the ordering is: **write the intent, make it durable, attempt the effect, write the outcome.**

- `test_an_effectful_send_writes_an_intent_entry_before_the_outcome`
- `test_intent_precedes_outcome_in_the_recorded_order`
- `test_an_interrupted_send_leaves_its_intent_pending_and_its_outcome_an_error`

The last one is the crash case: an interrupted send leaves a pending intent and an error outcome
rather than a silent absence. A pending intent still counts toward the cap, which is the fail-**closed**
direction. Counting an attempt that may not have happened costs one send of headroom. Not counting one
that did costs a breach.

### 2.1 The gate that raises

If the gate itself raises, an intent could sit forever with no outcome, and a later reader cannot
distinguish a crash from an in-flight call. So a raising gate still writes an outcome:
`test_a_gate_that_raises_still_writes_an_outcome_and_leaves_no_dangling_intent`.

---

## 3. Durability, and why these specific mechanics

Each of these exists because of the fail-open consequence above, not because of general tidiness.

**fsync after the bytes.** A buffered write that survives the process but not the machine is a lost
line, and a lost line is a permissive count. `test_append_fsyncs_the_store_files_descriptor_after_the_bytes_are_written`.

**Binary append mode.** Text mode on Windows rewrites `\n` as `\r\n`, which changes the bytes that were
hashed and breaks verification on a file that was never tampered with. The store opens in binary append
and the bytes on disk are newline-terminated with no carriage returns:
`test_the_store_opens_in_binary_append_mode`;
`test_the_bytes_on_disk_are_newline_terminated_with_no_carriage_returns`.

**Torn tails are detected, not truncated.** A partial final line from an interrupted write must not
silently reduce the count. The tear is reported, the preceding chain still verifies, and an append
after a tear remains readable without under-counting:

- `test_a_torn_final_line_is_reported_and_the_preceding_chain_still_verifies`
- `test_an_append_after_a_tear_is_readable_and_the_count_does_not_undercount`
- `test_the_tear_itself_is_not_erased_by_the_append_that_follows_it`

That last test is the subtle one. The repair path must not be a cover-up: an append that follows a tear
must leave the tear visible, or the log quietly heals itself into something that verifies while having
lost a line.

**A reopened store continues the same chain**, so a restart does not begin a second chain that verifies
independently while hiding the seam: `test_a_reopened_store_continues_the_same_chain`.

---

## 4. What the entry does not contain

`arg_digest` is a SHA-256 over canonical JSON of the arguments. The entry never holds the raw argument
values.

This is the whole reason the audit layer does not become a second copy of the sensitive payload. The
digest is enough to pair an intent with its outcome and to prove that the arguments at the outcome were
the arguments reviewed at the intent, without either line containing a message body, a recipient
address, or a figure.

- `test_arg_digest_is_stable_and_does_not_contain_the_raw_value`
- `test_a_digest_that_could_not_be_computed_is_marked_and_still_holds_no_raw_argument`

The second is the edge case that usually leaks. When an argument cannot be canonicalised, the tempting
fallback is `repr()` into the log "just for debugging". The entry marks the digest as uncomputable and
still holds nothing raw.

The human-readable text that *does* appear is the `span` on the denial payload, which quotes the
offending phrase back so a person deciding whether a draft may go out can see the actual words. That is
a deliberate, bounded exception: a quoted span of the draft the reviewer is already reading, rather
than the arguments of the call.

An unrecognised intent is refused rather than logged and passed through:
`test_an_unrecognised_intent_is_refused`.

---

## 5. The chain, briefly

Each entry's digest covers the previous entry's hash, so any change invalidates every link after it.
Verification returns `ok` and, when it fails, `broken_at` with the index.

- `test_a_tampered_entry_is_detected_and_the_index_is_named`
- `test_a_removed_entry_is_detected`
- `test_a_reordered_pair_is_detected`
- `test_a_tampered_prev_hash_alone_is_detected`
- `test_the_prev_hash_is_hashed_in_so_the_same_payload_yields_a_different_digest`
- `test_the_link_digest_matches_an_independently_computed_sha256`

The last two are worth a sentence each. Hashing the previous hash *into* the payload digest is what
makes position part of the identity, so an attacker cannot move a valid entry elsewhere in the file and
have it still verify. And computing the expected digest independently, rather than by calling the same
helper the implementation calls, is the difference between testing the property and testing that a
function agrees with itself.

An untouched chain verifies, and an empty chain verifies vacuously:
`test_an_untouched_chain_verifies`; `test_an_empty_chain_verifies_vacuously`. The empty case is
included because a verifier that returns false on an empty log makes a fresh install look tampered.

---

## 6. Limits

**A whole-chain rewrite defeats this.** An actor with write access who rewrites every entry and
recomputes every hash produces a chain that verifies. What the design defeats is edits, deletions, and
reorderings by an actor who cannot rewrite the entire file. Append-only storage under separate
credentials is the real answer, and it is infrastructure rather than code.

**Detection is not prevention.** The chain tells you afterwards. Nothing here stops a write.

**Crash recovery is built, and nothing schedules it.** The pass that reconciles pending intents after
a crash is `resume` in `src/chaperone/audit/recovery.py`, with branch (b) `aborted` and branch (c)
`unknown`, and `requires_approval_for` is the branch (c) gate. Both are tested. **Nothing schedules
`resume` and nothing consults `requires_approval_for` before a send**, so until they are wired in a
pending intent stays pending and continues to count toward the cap, which is the safe direction but
not the complete one. This is the wording the [README](../README.md) and
[docs/ON_CALL.md](ON_CALL.md) both carry; this page said the pass "does not exist in this tree",
which was true on the commit before the one that built it.
