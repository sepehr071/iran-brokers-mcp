"""Small helpers shared by the tool modules."""

from __future__ import annotations

from typing import Any

# Arabic ye (064A) and kaf (0643) become Persian ye (06CC) and kaf (06A9); ZWNJ, RLM, tatweel and spaces are dropped.
_FOLD = {0x64A: chr(0x6CC), 0x643: chr(0x6A9), 0x200C: None, 0x200F: None, 0x640: None, 0x20: None}


def norm(s: str | None) -> str:
    """Fold Arabic ye/kaf to Persian, drop spaces and ZWNJ, lowercase: how symbols are compared."""
    return (s or "").translate(_FOLD).lower()


def pct(x: float | None, nd: int = 2) -> float | None:
    """A fraction (0.0268) as percent (2.68); None stays None."""
    return None if x is None else round(x * 100, nd)


def pick(d: dict[str, Any], keys: dict[str, str]) -> dict[str, Any]:
    """Copy `d[src]` to `out[dst]` for each `{src: dst}`; missing keys are skipped."""
    return {dst: d[src] for src, dst in keys.items() if src in d}


def rows(v: Any) -> list[dict[str, Any]]:
    """A list answer as a list, anything else (error-ish dict, empty body) as []."""
    return v if isinstance(v, list) else []
