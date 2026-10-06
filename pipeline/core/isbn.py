from __future__ import annotations

import re
from typing import Optional


def clean(s: str) -> str:
    return re.sub(r"[^0-9Xx]", "", s or "").upper()


def isbn13_valid(s: str) -> bool:
    s = clean(s)
    if len(s) != 13 or not s.isdigit() or not s.startswith(("978", "979")):
        return False
    total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(s[:12]))
    return (10 - total % 10) % 10 == int(s[12])


def isbn10_valid(s: str) -> bool:
    s = clean(s)
    if len(s) != 10 or not s[:9].isdigit():
        return False
    total = sum((10 - i) * int(d) for i, d in enumerate(s[:9]))
    total += 10 if s[9] == "X" else (int(s[9]) if s[9].isdigit() else -1000)
    return total % 11 == 0


def isbn10_to_13(s: str) -> str:
    core = "978" + clean(s)[:9]
    total = sum(int(d) * (1 if i % 2 == 0 else 3) for i, d in enumerate(core))
    return core + str((10 - total % 10) % 10)


def normalize(s: str) -> Optional[str]:
    """Return a valid ISBN-13 (digits only) or None."""
    s = clean(s)
    if len(s) == 13 and isbn13_valid(s):
        return s
    if len(s) == 10 and isbn10_valid(s):
        return isbn10_to_13(s)
    return None
