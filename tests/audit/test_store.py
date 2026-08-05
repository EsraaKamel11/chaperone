import json
import os
from pathlib import Path

import pytest

from chaperone.audit.chain import verify
from chaperone.audit.store import AuditStore


def _payload(seq: int, outcome: str = "allowed") -> dict:
    return dict(seq=seq, kind="outcome", tool="send_message", principal="agent",
                tier=2, scope="send", outcome=outcome, arg_digest="d" * 64, seed=None)


def test_appended_entries_read_back_and_verify(tmp_path: Path):
    store = AuditStore(tmp_path / "audit.jsonl")
    for i in range(3):
        store.append(_payload(i))
    entries, torn = store.read_all()
    assert len(entries) == 3
    assert torn is False
    assert verify(entries).ok is True


def test_a_reopened_store_continues_the_same_chain(tmp_path: Path):
    path = tmp_path / "audit.jsonl"
    AuditStore(path).append(_payload(0))
    AuditStore(path).append(_payload(1))
    entries, _ = AuditStore(path).read_all()
    assert verify(entries).ok is True


def test_a_torn_final_line_is_reported_and_the_preceding_chain_still_verifies(tmp_path: Path):
    path = tmp_path / "audit.jsonl"
    store = AuditStore(path)
    for i in range(3):
        store.append(_payload(i))
    with path.open("ab") as handle:
        handle.write(b'{"seq": 3, "kind": "outc')
    entries, torn = AuditStore(path).read_all()
    assert torn is True
    assert len(entries) == 3
    assert verify(entries, torn_tail=torn).ok is True


def test_an_edited_line_on_disk_is_detected(tmp_path: Path):
    path = tmp_path / "audit.jsonl"
    store = AuditStore(path)
    for i in range(3):
        store.append(_payload(i))
    lines = path.read_bytes().split(b"\n")
    lines[1] = lines[1].replace(b'"allowed"', b'"denied"')
    path.write_bytes(b"\n".join(lines))
    entries, _ = AuditStore(path).read_all()
    assert verify(entries).broken_at == 1


def test_the_store_opens_in_binary_append_mode(tmp_path: Path):
    """Text-mode buffering lets the file lie about its durability state."""
    import inspect
    from chaperone.audit import store as store_module
    source = inspect.getsource(store_module)
    assert '"ab"' in source
    assert "fsync" in source


def test_count_filters_by_predicate(tmp_path: Path):
    store = AuditStore(tmp_path / "audit.jsonl")
    store.append(_payload(0, outcome="allowed"))
    store.append(_payload(1, outcome="denied"))
    store.append(_payload(2, outcome="allowed"))
    assert store.count(lambda e: e.outcome == "allowed") == 2


def test_append_fsyncs_the_store_files_descriptor_after_the_bytes_are_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Companion to the source-substring test above, asserting the property it only names.

    Recording that `os.fsync` was called is the one deliberate departure from "assert effects,
    never invocations", and it is not a lapse: a successful `fsync` has no observable result in
    this process -- it returns None and changes no byte of the file. The syscall's invocation *is*
    its effect, so the only place to observe it is at the boundary.

    Two things beyond the bare call are asserted here, and both are real effects:

    - the descriptor handed to `fsync` is the store file's, checked by device+inode identity
      against `path.stat()` while the handle is still open. A `fsync` of some unrelated
      descriptor would satisfy a naive spy and leave the log just as unflushed.
    - the file already holds the line at the moment `fsync` runs. `fsync` before `write` is a
      no-op dressed as durability; `st_size` at call time is what tells them apart.

    The real `fsync` is still invoked, so the test exercises the durable path rather than
    replacing it.
    """
    path = tmp_path / "audit.jsonl"
    real_fsync = os.fsync
    synced: list[tuple[int, int, int]] = []

    def recording_fsync(fd: int) -> None:
        info = os.fstat(fd)
        synced.append((info.st_dev, info.st_ino, info.st_size))
        real_fsync(fd)

    monkeypatch.setattr(os, "fsync", recording_fsync)
    store = AuditStore(path)
    store.append(_payload(0))

    assert len(synced) == 1, "append must fsync exactly once per entry"
    st_dev, st_ino, st_size = synced[0]
    on_disk = path.stat()
    assert (st_dev, st_ino) == (on_disk.st_dev, on_disk.st_ino)
    assert st_size == len(path.read_bytes()) > 0


def test_the_bytes_on_disk_are_newline_terminated_with_no_carriage_returns(tmp_path: Path):
    """Binary append writes the byte asked for; text mode on Windows would write \\r\\n.

    This discriminates only because the platform would actually translate: on win32 a text-mode
    handle turns each "\\n" into "\\r\\n" on the way out. `.gitattributes` pins the repo to
    `eol=lf`, so no checkout can reintroduce a carriage return and make the assertion pass or fail
    for a reason other than the store's own file mode. A stray "\\r" would also land inside the
    hashed line and desynchronise the chain from what `link` computed.
    """
    path = tmp_path / "audit.jsonl"
    store = AuditStore(path)
    for i in range(3):
        store.append(_payload(i))

    raw = path.read_bytes()
    assert b"\r" not in raw
    assert raw.endswith(b"\n")
    assert raw.count(b"\n") == 3


def test_an_append_after_a_tear_is_readable_and_the_count_does_not_undercount(tmp_path: Path):
    """The named fail-open: a crash mid-write must not swallow the *next* entry.

    Binary append writes at the end of the file, so an entry appended after an unterminated line
    is concatenated onto it and both become one unparseable line. `append` still returns a
    well-formed `AuditEntry` and raises nothing, so the caller believes the record is durable
    while `count` -- the send cap's predicate -- silently reports one fewer. That is an
    enforcement predicate failing open, and design spec 5.5 calls torn tails *expected*, so it
    is a routine post-crash path.

    Four appends succeed here, so four entries must be readable and counted, regardless of the
    tear between them.
    """
    path = tmp_path / "audit.jsonl"
    store = AuditStore(path)
    for i in range(3):
        store.append(_payload(i))
    with path.open("ab") as handle:                    # crash part-way through a record
        handle.write(b'{"seq": 99, "kind": "outc')
    store.append(_payload(3))                          # process restarts and appends

    entries, torn = AuditStore(path).read_all()

    assert [entry.seq for entry in entries] == [0, 1, 2, 3]
    assert AuditStore(path).count(lambda e: e.outcome == "allowed") == 4
    assert torn is True, "the tear must stay visible to the recovery policy"
    assert verify(entries, torn_tail=torn).ok is True


def test_the_tear_itself_is_not_erased_by_the_append_that_follows_it(tmp_path: Path):
    """Recovery has to see that something was lost; healing the log silently hides it.

    Task 24's branch (c) exists to catch an intent whose outcome record vanished. Overwriting or
    truncating the torn bytes to make the file tidy would destroy exactly the evidence that
    branch reads, so the partial record must survive on disk and `torn` must stay true across
    any number of subsequent appends.
    """
    path = tmp_path / "audit.jsonl"
    store = AuditStore(path)
    store.append(_payload(0))
    with path.open("ab") as handle:
        handle.write(b'{"seq": 99, "kind": "outc')
    store.append(_payload(1))
    store.append(_payload(2))

    raw = path.read_bytes()
    assert b'{"seq": 99, "kind": "outc' in raw, "the partial record must not be erased"
    assert raw.count(b'{"seq": 99') == 1, "and must not be duplicated"
    entries, torn = AuditStore(path).read_all()
    assert torn is True
    assert [entry.seq for entry in entries] == [0, 1, 2]


def test_a_tear_anywhere_in_the_file_is_reported_and_never_silently_dropped(tmp_path: Path):
    """`torn` means "a torn line exists", not "the last line is torn".

    Once an append may follow a tear, the torn line is no longer final, and a flag that only ever
    described the tail would report a holed log as clean. `count` discards the flag, so a caller
    that never sees `torn` go true has no way to learn an entry is missing -- the silent drop
    this widening exists to prevent.
    """
    path = tmp_path / "audit.jsonl"
    store = AuditStore(path)
    for i in range(3):
        store.append(_payload(i))
    lines = path.read_bytes().rstrip(b"\n").split(b"\n")
    lines[1] = b'{"seq": 1, "kind": "outc'             # a hole in the middle, terminated
    path.write_bytes(b"\n".join(lines) + b"\n")

    entries, torn = AuditStore(path).read_all()

    assert torn is True
    assert [entry.seq for entry in entries] == [0, 2]


def test_a_tear_that_splits_a_multibyte_character_is_reported_rather_than_failing_the_whole_read(
    tmp_path: Path
):
    """A crash lands between bytes, not between characters.

    Decoding the whole file at once makes one half-written character raise `UnicodeDecodeError`
    over every entry in the log, so a routine tear presents as total loss. Decoding per line
    keeps the failure local to the line it damaged.
    """
    path = tmp_path / "audit.jsonl"
    store = AuditStore(path)
    store.append(_payload(0))
    with path.open("ab") as handle:
        handle.write(b'{"principal": "\xc3')           # first byte of a two-byte sequence

    entries, torn = AuditStore(path).read_all()

    assert torn is True
    assert [entry.seq for entry in entries] == [0]


def test_a_line_that_is_valid_json_but_violates_the_schema_is_not_treated_as_a_tear(tmp_path: Path):
    """A truncated write and a schema violation are different failures.

    A torn write is a prefix of a JSON object and so is never itself valid JSON -- a crash cannot
    land on a balanced brace mid-record. A line that parses cleanly but lacks required fields is
    therefore not a tear; it is corruption or tampering wearing a tear's clothes. Reporting it as
    an expected crash artifact would let a tampered final line pass as routine, so it must refuse
    the read instead.
    """
    path = tmp_path / "audit.jsonl"
    store = AuditStore(path)
    for i in range(3):
        store.append(_payload(i))
    with path.open("ab") as handle:
        handle.write(b'{"seq": 3, "kind": "outcome"}\n')

    # pydantic's ValidationError is a ValueError subclass.
    with pytest.raises(ValueError):
        AuditStore(path).read_all()
    with pytest.raises(ValueError):
        AuditStore(path).count(lambda e: e.outcome == "allowed")


def test_a_json_line_that_is_not_an_object_refuses_the_read(tmp_path: Path):
    """A bare scalar or array parses cleanly and is still not a record.

    `AuditEntry(**fields)` on a non-mapping raises `TypeError`, which is not a `ValueError` and so
    escapes every caller written to expect one type from this module. A line that parses is not a
    tear, so it must not be waved through as one either -- it refuses the read, under the same
    exception type as a schema violation.
    """
    path = tmp_path / "audit.jsonl"
    store = AuditStore(path)
    store.append(_payload(0))
    with path.open("ab") as handle:
        handle.write(b"[1, 2]\n")

    with pytest.raises(ValueError):
        AuditStore(path).read_all()


def test_a_payload_pydantic_coerces_still_verifies_because_one_projection_is_hashed(tmp_path: Path):
    """`append` and `verify` must hash the same projection by construction, not by convention.

    Hashing the caller's raw dict on the way in and the model's projection on the way out makes
    the two agree only while the caller happens to pass exactly-typed keys. Any coercion --
    `seq` arriving as a string from JSON, `tier` as a float, a stray key pydantic ignores --
    desynchronises them, and a log that was never touched reports as tampered at index 0.
    Building the entry first and hashing `entry.payload()` removes the second projection, so the
    class of bug cannot recur.
    """
    coerced = dict(_payload(0), seq="0", tier=2.0, unexpected="ignored-by-the-model")

    store = AuditStore(tmp_path / "audit.jsonl")
    store.append(coerced)
    entries, _ = store.read_all()

    assert entries[0].seq == 0
    assert verify(entries).ok is True


def test_a_corrupt_hash_on_the_final_line_is_not_a_torn_tail_and_verify_names_it(tmp_path: Path):
    """A tampered last line and a half-written last line are different failures.

    A line that parses is an entry, torn or not -- so a corrupted `entry_hash` on the final line
    must come back as a *readable* entry with `torn_tail` false, and be caught by `verify` at its
    index. Were it folded into the torn-tail path instead, the entry would be dropped, the
    preceding chain would verify, and a deliberate edit would present exactly as a crash.
    """
    path = tmp_path / "audit.jsonl"
    store = AuditStore(path)
    for i in range(3):
        store.append(_payload(i))
    lines = path.read_bytes().decode("utf-8").rstrip("\n").split("\n")
    last = json.loads(lines[-1])
    last["entry_hash"] = "f" * 64
    lines[-1] = json.dumps(last, sort_keys=True, separators=(",", ":"))
    path.write_bytes(("\n".join(lines) + "\n").encode("utf-8"))

    entries, torn = AuditStore(path).read_all()

    assert torn is False, "a line that parses is an entry, not a tear"
    assert len(entries) == 3
    result = verify(entries, torn_tail=torn)
    assert result.ok is False
    assert result.broken_at == 2
