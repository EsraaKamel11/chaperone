---
description: Run the full verification gate
---

Run these in order and report any failure verbatim:

```bash
pytest -q
python tools/scan_secrets.py
python tools/static_audit.py
python tools/coverage_map.py
python tools/lint_descriptions.py
```

Tools added in later tasks will not exist yet; a "no such file" for `coverage_map.py` or
`lint_descriptions.py` before Task 14 and Task 25 respectively is expected, not a failure.
