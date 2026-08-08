from __future__ import annotations

import math
import re
import sys
from pathlib import Path

Finding = tuple[Path, int, str]

_PREFIXES = ("sk-", "ghp_", "gho_", "ghs_", "github_pat_", "AKIA", "xoxb-", "xoxp-", "AIza", "voc-")
_PREFIX_PATTERN = re.compile(r"\b(?:" + "|".join(re.escape(p) for p in _PREFIXES) + r")[A-Za-z0-9_-]{16,}")
_CREDENTIAL_NAME = re.compile(r"(?i)\b\w*(secret|token|api[_-]?key|password|passwd|credential)\w*\s*[:=]\s*['\"]([^'\"]{16,})['\"]")
_SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", ".pytest_cache", ".superpowers"}
_TEXT_SUFFIXES = {".py", ".md", ".txt", ".json", ".jsonl", ".toml", ".yml", ".yaml", ".cfg", ".ini", ".env", ".sh", ".ts", ".js"}
_ALLOWLIST_MARKER = "allowlist-secret"


def _entropy(value: str) -> float:
    if not value:
        return 0.0
    return -sum(
        (value.count(c) / len(value)) * math.log2(value.count(c) / len(value))
        for c in set(value)
    )


#: The repository, not the working directory. `main()` scanned `Path.cwd()`, so the CI step and the
#: `Stop` hook both examined whatever directory the process happened to start in. Every sibling
#: tool anchors here.
ROOT = Path(__file__).resolve().parents[1]


def eligible_files(root: Path) -> list[Path]:
    """The files this scanner is willing to read, in a stable order.

    Separate from `scan_tree` so `main` can tell "no secret found" from "nothing was looked at".

    An extensionless file is read rather than skipped -- a `Dockerfile` or a `.env` written without
    a suffix is exactly where a key lands -- so this is deliberately wider than `_TEXT_SUFFIXES`,
    and `main`'s emptiness check is the narrower one for that reason.
    """
    return [
        path
        for path in sorted(root.rglob("*"))
        if path.is_file()
        and not any(part in _SKIP_DIRS for part in path.parts)
        and (not path.suffix or path.suffix in _TEXT_SUFFIXES)
    ]


def scan_tree(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    for path in eligible_files(root):
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _ALLOWLIST_MARKER in line:
                continue
            if _PREFIX_PATTERN.search(line):
                findings.append((path, lineno, "known_prefix"))
                continue
            match = _CREDENTIAL_NAME.search(line)
            if match and _entropy(match.group(2)) > 3.5:
                findings.append((path, lineno, "credential_named_high_entropy"))
    return findings


def main(root: Path | None = None) -> int:
    """CI's first enforcement step. Anything other than 0 fails the build.

    `root` defaults to the repository and exists so a test can point the real enforcement at a tree
    it controls, exactly as `tools/static_audit.py` splits `audit_tree` from `main`. The exit code
    is the property, so both directions are asserted through here rather than through `scan_tree`.

    **A scan that read no eligible file exits 1.** `_TEXT_SUFFIXES = set()` otherwise leaves every
    extension ineligible, finds nothing, and reports clean over a scan that examined nothing --
    which is the shape three sibling tools already refuse with "audited nothing", "classified
    nothing" and "linted nothing".
    """
    root = ROOT if root is None else root
    eligible = eligible_files(root)
    if not any(path.suffix in _TEXT_SUFFIXES for path in eligible):
        print(f"{root}: no file carrying a scannable suffix -- scanned nothing")
        return 1
    findings = scan_tree(root)
    for path, lineno, rule in findings:
        print(f"{path}:{lineno}: {rule}")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
