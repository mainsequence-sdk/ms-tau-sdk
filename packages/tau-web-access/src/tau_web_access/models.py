"""Transport-neutral web access models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class WebModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SearchResult(WebModel):
    title: str
    url: str
    snippet: str = ""


class ExtractedContent(WebModel):
    url: str
    title: str = ""
    content: str = ""
    error: str | None = None


class QueryResult(WebModel):
    query: str
    answer: str
    results: list[SearchResult] = Field(default_factory=list)
    error: str | None = None
    provider: str | None = None
    inline_content: list[ExtractedContent] = Field(default_factory=list)


class StoredSearchData(WebModel):
    id: str
    type: Literal["search"] = "search"
    timestamp_ms: int
    queries: list[QueryResult]


class StoredFetchData(WebModel):
    id: str
    type: Literal["fetch"] = "fetch"
    timestamp_ms: int
    urls: list[ExtractedContent]


StoredResult = StoredSearchData | StoredFetchData


__all__ = [
    "ExtractedContent",
    "QueryResult",
    "SearchResult",
    "StoredFetchData",
    "StoredResult",
    "StoredSearchData",
]
