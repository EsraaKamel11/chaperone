from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable

from chaperone.audit.chain import GENESIS_HASH, link
from chaperone.audit.entry import AuditEntry


class AuditStore:
    """Durable append-only log. Binary append, flush, fsync, per entry."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def _last_hash(self) -> str:
        entries, _ = self.read_all()
        return entries[-1].entry_hash if entries else GENESIS_HASH

    def append(self, payload: dict) -> AuditEntry:
        prev = self._last_hash()
        entry = AuditEntry(**payload, prev_hash=prev, entry_hash=link(prev, payload))
        line = json.dumps(entry.model_dump(), sort_keys=True, separators=(",", ":")) + "\n"
        with self._path.open("ab") as handle:
            handle.write(line.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        return entry

    def read_all(self) -> tuple[list[AuditEntry], bool]:
        """Returns (entries, torn_tail). A half-written final line is expected, not exceptional."""
        if not self._path.exists():
            return [], False
        raw = self._path.read_bytes().decode("utf-8")
        lines = [line for line in raw.split("\n") if line]
        entries: list[AuditEntry] = []
        torn_tail = False
        for index, line in enumerate(lines):
            try:
                entries.append(AuditEntry(**json.loads(line)))
            except (json.JSONDecodeError, ValueError):
                if index == len(lines) - 1:
                    torn_tail = True
                    break
                raise
        return entries, torn_tail

    def count(self, predicate: Callable[[AuditEntry], bool]) -> int:
        entries, _ = self.read_all()
        return sum(1 for entry in entries if predicate(entry))
