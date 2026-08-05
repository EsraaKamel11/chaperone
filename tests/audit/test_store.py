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


def test_an_unparseable_line_in_the_middle_refuses_to_read_rather_than_dropping_it(tmp_path: Path):
    """Only the *final* line may be forgiven; a hole anywhere else must stop the read.

    Dropping an unreadable middle line and carrying on is the fail-open this log exists to
    prevent. `count` is the send cap's predicate, so a silently skipped line makes the cap
    undercount and the next send go through -- an enforcement predicate failing open, not a
    forensics gap. Refusing to answer is the safe direction: a caller that cannot get a count
    cannot conclude it is under the cap.

    `count` is asserted alongside `read_all` because `count` is where the escape would land, and
    it discards the `torn_tail` flag that would otherwise warn the caller.
    """
    path = tmp_path / "audit.jsonl"
    store = AuditStore(path)
    for i in range(3):
        store.append(_payload(i))
    lines = path.read_bytes().rstrip(b"\n").split(b"\n")
    lines[1] = b'{"seq": 1, "kind": "outc'
    path.write_bytes(b"\n".join(lines) + b"\n")

    # JSONDecodeError and pydantic's ValidationError are both ValueError subclasses.
    with pytest.raises(ValueError):
        AuditStore(path).read_all()
    with pytest.raises(ValueError):
        AuditStore(path).count(lambda e: e.outcome == "allowed")


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
