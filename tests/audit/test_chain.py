import hashlib
import json

from chaperone.audit.chain import GENESIS_HASH, link, verify
from chaperone.audit.entry import AuditEntry


def _entry(seq: int, prev: str, outcome: str = "allowed") -> AuditEntry:
    payload = dict(seq=seq, kind="outcome", tool="send_message", principal="agent",
                   tier=2, scope="send", outcome=outcome, arg_digest="d" * 64, seed=None)
    return AuditEntry(**payload, prev_hash=prev, entry_hash=link(prev, payload))


def _chain(n: int) -> list[AuditEntry]:
    entries, prev = [], GENESIS_HASH
    for i in range(n):
        entry = _entry(i, prev)
        entries.append(entry)
        prev = entry.entry_hash
    return entries


def test_an_untouched_chain_verifies():
    result = verify(_chain(5))
    assert result.ok is True
    assert result.broken_at is None


def test_a_tampered_entry_is_detected_and_the_index_is_named():
    entries = _chain(5)
    entries[2] = entries[2].model_copy(update={"outcome": "denied"})
    result = verify(entries)
    assert result.ok is False
    assert result.broken_at == 2


def test_a_removed_entry_is_detected():
    entries = _chain(5)
    del entries[2]
    assert verify(entries).ok is False


def test_a_reordered_pair_is_detected():
    entries = _chain(5)
    entries[1], entries[2] = entries[2], entries[1]
    assert verify(entries).ok is False


def test_an_empty_chain_verifies_vacuously():
    assert verify([]).ok is True


def test_a_tampered_prev_hash_alone_is_detected():
    """Isolates the linkage check, which the digest check does not subsume.

    `link` hashes the *running* predecessor, not the `prev_hash` the entry carries, so an entry
    whose `prev_hash` field alone is rewritten still recomputes to its stored `entry_hash`. Only
    the `entry.prev_hash != prev` comparison catches it. Deleting that comparison leaves the
    removal and reorder tests above passing, so without this test the stored linkage field is
    unverified -- and it is the field a reader would trust to reconstruct the order.
    """
    entries = _chain(5)
    entries[2] = entries[2].model_copy(update={"prev_hash": "a" * 64})
    result = verify(entries)
    assert result.ok is False
    assert result.broken_at == 2


def test_the_link_digest_matches_an_independently_computed_sha256():
    """An exact reference, not a second call to `link`.

    Recomputing with `link` would compare the code under test against itself: a change to the
    hashed material would move both sides together and the assertion would still pass. The digest
    here is built from stdlib `hashlib` and `json` only, spelling out the canonical form -- sorted
    keys, no whitespace, no ASCII escaping -- so a change to what is hashed, or to how it is
    serialised, breaks this test and nothing else has to notice.
    """
    payload = dict(seq=0, kind="intent", tool="send_message", principal="agent",
                   tier=2, scope="send", outcome="pending", arg_digest="d" * 64, seed=None)
    material = GENESIS_HASH + json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    expected = hashlib.sha256(material.encode("utf-8")).hexdigest()

    assert link(GENESIS_HASH, payload) == expected


def test_the_prev_hash_is_hashed_in_so_the_same_payload_yields_a_different_digest():
    """Position in the chain must change the digest, or reordering equal payloads is invisible.

    `_chain` gives every entry a distinct `seq`, so a `link` that ignored `prev_hash` entirely
    would still pass the reorder test above. This pins the linkage directly: one payload, two
    predecessors, two digests.
    """
    payload = dict(seq=0, kind="outcome", tool="send_message", principal="agent",
                   tier=2, scope="send", outcome="allowed", arg_digest="d" * 64, seed=None)

    assert link(GENESIS_HASH, payload) != link("a" * 64, payload)
