from __future__ import annotations

import math
import re
import sys
from pathlib import Path

Finding = tuple[Path, int, str]

_PREFIXES = ("sk-", "ghp_", "gho_", "ghs_", "github_pat_", "AKIA", "xoxb-", "xoxp-", "AIza", "voc-")
_CREDENTIAL_NAME = re.compile(r"(?i)\b\w*(secret|token|api[_-]?key|password|passwd|credential)\w*\s*[:=]\s*['\"]([^'\"]{16,})['\"]")
_SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", ".pytest_cache"}
_TEXT_SUFFIXES = {".py", ".md", ".txt", ".json", ".jsonl", ".toml", ".yml", ".yaml", ".cfg", ".ini", ".env", ".sh", ".ts", ".js"}


def _entropy(value: str) -> float:
    if not value:
        return 0.0
    return -sum(
        (value.count(c) / len(value)) * math.log2(value.count(c) / len(value))
        for c in set(value)
    )


def scan_tree(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if path.suffix and path.suffix not in _TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if any(p in line for p in _PREFIXES):
                findings.append((path, lineno, "known_prefix"))
                continue
            match = _CREDENTIAL_NAME.search(line)
            if match and _entropy(match.group(2)) > 3.5:
                findings.append((path, lineno, "credential_named_high_entropy"))
    return findings


def main() -> int:
    findings = scan_tree(Path.cwd())
    for path, lineno, rule in findings:
        print(f"{path}:{lineno}: {rule}")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
