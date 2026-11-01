"""Run one request against Mofid, Agah or both and label each answer by source."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Literal

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

Source = Annotated[
    Literal["mofid", "agah", "all"],
    Field(
        description="mofid, agah or all. Each source answers in its own block; with all, a source that fails shows {'error': ...} and the other still answers."
    ),
]


async def by_source(source: str, calls: dict[str, Callable[[], Awaitable[Any]]]) -> dict[str, Any]:
    """Run the calls of the chosen source(s) in parallel. Raises only if every chosen source failed."""
    names = [n for n in calls if source in (n, "all")]
    if not names:
        raise ToolError(f"Source {source!r} cannot answer this; use {' or '.join(calls)}.")
    done = await asyncio.gather(*(calls[n]() for n in names), return_exceptions=True)
    out: dict[str, Any] = {}
    failed: dict[str, Exception] = {}
    for n, r in zip(names, done, strict=True):
        if isinstance(r, BaseException) and not isinstance(r, Exception):
            raise r
        if isinstance(r, Exception):
            failed[n] = r
            out[n] = {"error": str(r) if isinstance(r, ToolError) else f"{type(r).__name__}: {r}"}
        else:
            out[n] = r
    if len(failed) == len(names):
        if len(names) == 1:
            raise next(iter(failed.values()))
        raise ToolError("; ".join(f"{n}: {out[n]['error']}" for n in names))
    return out
