from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable

from chaperone.audit.chain import GENESIS_HASH, link
from chaperone.audit.entry import AuditEntry


class AuditStore:
    """Durable append-only log. Binary append, flush, fsync, per entry.

    Two failures are handled here rather than left to callers, because both make the log
    misreport rather than complain, and the send cap counts off this log.

    **A torn line never absorbs the record that follows it.** Binary append writes at the end of
    the file, so an entry appended after an unterminated line would be concatenated onto it and
    both would become one unparseable line -- while `append` returned a well-formed `AuditEntry`
    and raised nothing. The next `count` would then be one short, which is an enforcement
    predicate failing open, not a forensics gap. `append` terminates a partial line before
    writing. It does not remove it: the tear stays on disk and keeps `torn` true, because the
    recovery policy has to be able to see that a record was lost.

    **One projection is hashed, not two.** The entry is built before its hash is computed, so
    `append` and `verify` both hash `entry.payload()` by construction. Hashing the caller's dict
    on the way in and the model's projection on the way out agrees only while the caller passes
    exactly-typed keys, and desynchronises the moment pydantic coerces anything.
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _last_hash(self) -> str:
        entries, _ = self.read_all()
        return entries[-1].entry_hash if entries else GENESIS_HASH

    def _ends_mid_line(self) -> bool:
        """True when the last byte on disk is not a newline -- a write that was cut short."""
        if not self._path.exists() or self._path.stat().st_size == 0:
            return False
        with self._path.open("rb") as handle:
            handle.seek(-1, os.SEEK_END)
            return handle.read(1) != b"\n"

    def append(self, payload: dict) -> AuditEntry:
        prev = self._last_hash()
        # `payload()` excludes `entry_hash`, so the placeholder never enters the hashed material
        # and the digest covers exactly the fields `verify` will recompute from.
        draft = AuditEntry(**payload, prev_hash=prev, entry_hash="")
        entry = draft.model_copy(update={"entry_hash": link(prev, draft.payload())})
        line = json.dumps(entry.model_dump(), sort_keys=True, separators=(",", ":")) + "\n"
        terminate = self._ends_mid_line()
        with self._path.open("ab") as handle:
            if terminate:
                handle.write(b"\n")
            handle.write(line.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        return entry

    def read_all(self) -> tuple[list[AuditEntry], bool]:
        """Returns (entries, torn). A partial write is expected, not exceptional.

        `torn` means "a torn line exists", not "the last line is torn". Once an append may follow
        a tear the torn line is no longer final, and a flag that only described the tail would
        report a holed log as clean.

        A tear and a schema violation are different failures and are not conflated. A torn line
        is a prefix of a record, so it is never itself valid JSON -- a crash cannot land on a
        balanced brace mid-object. Anything that parses is therefore a record, and a record that
        fails the schema is corruption or tampering rather than a crash artifact, so it refuses
        the read instead of being waved through as expected damage.

        Lines are split on bytes and decoded individually because a crash lands between bytes,
        not between characters: decoding the file as a whole would let one half-written character
        raise over every entry in the log.
        """
        if not self._path.exists():
            return [], False
        entries: list[AuditEntry] = []
        torn = False
        for raw in self._path.read_bytes().split(b"\n"):
            if not raw:
                continue
            try:
                fields = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                torn = True
                continue
            if not isinstance(fields, dict):
                raise ValueError(f"{self._path}: a log line is valid JSON but not an object")
            entries.append(AuditEntry(**fields))
        return entries, torn

    def next_seq(self) -> int:
        """The next free entry number. **The only place a seq is decided.**

        Two writers append here -- `Gateway` and `recovery.resume` -- and the ordering they both
        depend on is that a later record carries a higher seq: it is what lets recovery treat a
        prefix of the log as stale and its tail as possibly still in flight. Every previous attempt
        to hold that with two copies of one rule failed, in a different way each time. `len(entries)`
        counted a holed log short and reissued a number already in use. `max + 1` cached at
        construction went stale as soon as the other writer ran. `max + 1` derived once before a
        loop went stale *within* one recovery pass, because the probe it calls reaches the outside
        world through the gateway and so appends to this log between iterations.

        The rule lives here because the log is what it is a property of, and because a rule with one
        home cannot be half-updated. Callers ask at the moment of writing and never hold the answer.

        A torn line is not counted and cannot lower this: it never parsed, so it never contributed a
        seq, and the maximum is taken over the records that did.
        """
        entries, _ = self.read_all()
        return max((entry.seq for entry in entries), default=-1) + 1

    def count(self, predicate: Callable[[AuditEntry], bool]) -> int:
        """Entries matching `predicate`. A caller using this for a cap must read `torn` too: a
        torn line is a record that never became durable, and it is not counted here."""
        entries, _ = self.read_all()
        return sum(1 for entry in entries if predicate(entry))
