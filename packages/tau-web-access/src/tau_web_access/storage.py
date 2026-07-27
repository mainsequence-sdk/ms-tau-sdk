"""Stored-result contract used by response-ID retrieval."""

from __future__ import annotations

import asyncio
from typing import Protocol

from .models import StoredResult


class SearchResultStore(Protocol):
    async def put(self, value: StoredResult) -> None:
        """Persist a web-search or content-fetch result."""

    async def get(self, response_id: str) -> StoredResult | None:
        """Retrieve a result by response ID."""


class MemorySearchResultStore:
    """Bounded process-local storage suitable for one loaded agent session."""

    def __init__(self, *, max_entries: int = 128) -> None:
        self._max_entries = max_entries
        self._values: dict[str, StoredResult] = {}
        self._lock = asyncio.Lock()

    async def put(self, value: StoredResult) -> None:
        async with self._lock:
            self._values[value.id] = value
            while len(self._values) > self._max_entries:
                oldest = next(iter(self._values))
                del self._values[oldest]

    async def get(self, response_id: str) -> StoredResult | None:
        async with self._lock:
            return self._values.get(response_id)


__all__ = ["MemorySearchResultStore", "SearchResultStore"]
