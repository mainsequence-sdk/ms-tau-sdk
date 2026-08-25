"""Tau AgentTool factories preserving the pi-web-access headless contract."""

from __future__ import annotations

import time
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import cast

import httpx
from tau_agent.messages import ImageContent, TextContent
from tau_agent.tools import AgentTool, AgentToolResult, ToolCancellationToken, ToolUpdateCallback
from tau_agent.types import JSONValue

from .extractors import ContentExtractor, FetchOutcome
from .models import (
    ExtractedContent,
    QueryResult,
    StoredFetchData,
    StoredSearchData,
)
from .providers import SearchOptions, SearchProviderName, SearchProviders
from .settings import WebAccessSettings
from .storage import MemorySearchResultStore, SearchResultStore

WEB_SEARCH_SCHEMA: Mapping[str, JSONValue] = {
    "type": "object",
    "properties": {
        "query": {"type": "string"},
        "queries": {"type": "array", "items": {"type": "string"}},
        "numResults": {"type": "number", "minimum": 1, "maximum": 20},
        "includeContent": {"type": "boolean"},
        "recencyFilter": {"type": "string", "enum": ["day", "week", "month", "year"]},
        "domainFilter": {"type": "array", "items": {"type": "string"}},
        "provider": {"type": "string", "enum": ["auto", "perplexity", "gemini", "exa"]},
        "workflow": {"type": "string", "enum": ["none", "summary-review"]},
    },
    "additionalProperties": False,
}

CODE_SEARCH_SCHEMA: Mapping[str, JSONValue] = {
    "type": "object",
    "properties": {
        "query": {"type": "string"},
        "maxTokens": {"type": "integer", "minimum": 1_000, "maximum": 50_000},
    },
    "required": ["query"],
    "additionalProperties": False,
}

FETCH_CONTENT_SCHEMA: Mapping[str, JSONValue] = {
    "type": "object",
    "properties": {
        "url": {"type": "string"},
        "urls": {"type": "array", "items": {"type": "string"}},
        "forceClone": {"type": "boolean"},
        "prompt": {"type": "string"},
        "timestamp": {"type": "string"},
        "frames": {"type": "integer", "minimum": 1, "maximum": 12},
        "model": {"type": "string"},
    },
    "additionalProperties": False,
}

GET_SEARCH_CONTENT_SCHEMA: Mapping[str, JSONValue] = {
    "type": "object",
    "properties": {
        "responseId": {"type": "string"},
        "query": {"type": "string"},
        "queryIndex": {"type": "number"},
        "url": {"type": "string"},
        "urlIndex": {"type": "number"},
    },
    "required": ["responseId"],
    "additionalProperties": False,
}


def _cancelled(signal: ToolCancellationToken | None) -> None:
    if signal is not None and signal.is_cancelled():
        raise RuntimeError("Operation aborted")


def _error(message: str, **details: JSONValue) -> AgentToolResult:
    return AgentToolResult(
        content=[TextContent(text=f"Error: {message}")],
        details={"error": message, **details},
    )


def _format_search_summary(result: QueryResult) -> str:
    output = f"{result.answer}\n\n---\n\n**Sources:**\n" if result.answer else ""
    output += "\n\n".join(
        f"{index}. {item.title}\n   {item.url}"
        for index, item in enumerate(result.results, start=1)
    )
    return output


def _format_full_results(result: QueryResult) -> str:
    output = f'## Results for: "{result.query}"\n\n'
    if result.answer:
        output += f"{result.answer}\n\n---\n\n"
    for item in result.results:
        output += f"### {item.title}\n{item.url}\n\n"
    return output


def _as_string(value: JSONValue, *, name: str, required: bool = False) -> str | None:
    if value is None and not required:
        return None
    if not isinstance(value, str) or (required and not value.strip()):
        raise ValueError(f"{name} must be a non-empty string")
    return value


class WebToolSet:
    """Own shared HTTP, extraction, provider, and response-storage state."""

    def __init__(
        self,
        *,
        settings: WebAccessSettings,
        store: SearchResultStore,
        client: httpx.AsyncClient,
        cwd: Path,
    ) -> None:
        self.settings = settings
        self.store = store
        self.providers = SearchProviders(settings=settings, client=client)
        self.extractor = ContentExtractor(settings=settings, client=client, cwd=cwd)

    def tools(self) -> list[AgentTool]:
        return [
            self.web_search_tool(),
            self.code_search_tool(),
            self.fetch_content_tool(),
            self.get_search_content_tool(),
        ]

    def web_search_tool(self) -> AgentTool:
        async def execute(
            tool_call_id: str,
            arguments: Mapping[str, JSONValue],
            signal: ToolCancellationToken | None = None,
            on_update: ToolUpdateCallback | None = None,
        ) -> AgentToolResult:
            del tool_call_id, on_update
            _cancelled(signal)
            raw_queries = arguments.get("queries")
            if raw_queries is None:
                raw_query = arguments.get("query")
                raw_queries = [raw_query] if raw_query is not None else []
            if not isinstance(raw_queries, list):
                return _error("queries must be an array")
            queries = [
                item.strip() for item in raw_queries if isinstance(item, str) and item.strip()
            ]
            if not queries:
                return _error("No query provided", queryCount=0)

            workflow = arguments.get("workflow", "none")
            if workflow not in {"none", "summary-review"}:
                return _error("Invalid workflow")
            # Headless Tau keeps the search contract but does not open Pi's curator UI.
            num_results = arguments.get("numResults", 5)
            if (
                isinstance(num_results, bool)
                or not isinstance(num_results, (int, float))
                or not 1 <= num_results <= 20
            ):
                return _error("numResults must be between 1 and 20")
            include_content = arguments.get("includeContent", False)
            if not isinstance(include_content, bool):
                return _error("includeContent must be a boolean")
            provider = arguments.get("provider", self.settings.provider)
            if provider not in {"auto", "exa", "perplexity", "gemini"}:
                return _error("Invalid search provider")
            recency = arguments.get("recencyFilter")
            if recency not in {None, "day", "week", "month", "year"}:
                return _error("Invalid recencyFilter")
            domains = arguments.get("domainFilter", [])
            if not isinstance(domains, list) or not all(isinstance(item, str) for item in domains):
                return _error("domainFilter must be an array of strings")

            options = SearchOptions(
                num_results=int(num_results),
                include_content=include_content,
                recency_filter=recency,
                domain_filter=tuple(cast(list[str], domains)),
            )
            search_results: list[QueryResult] = []
            for query in queries:
                _cancelled(signal)
                try:
                    result = await self.providers.search(
                        query,
                        requested=cast(SearchProviderName, provider),
                        options=options,
                    )
                except Exception as error:
                    result = QueryResult(
                        query=query,
                        answer="",
                        error=str(error),
                        provider=str(provider),
                    )
                if include_content and result.results and not result.inline_content:
                    for item in result.results:
                        _cancelled(signal)
                        try:
                            outcome = await self.extractor.fetch(item.url)
                            result.inline_content.append(outcome.extracted)
                        except Exception as error:
                            result.inline_content.append(
                                ExtractedContent(
                                    url=item.url,
                                    title=item.title,
                                    error=str(error),
                                )
                            )
                search_results.append(result)

            response_id = uuid.uuid4().hex
            await self.store.put(
                StoredSearchData(
                    id=response_id,
                    timestamp_ms=int(time.time() * 1_000),
                    queries=search_results,
                )
            )
            output_sections: list[str] = []
            for result in search_results:
                if result.error:
                    output_sections.append(f'## Query: "{result.query}"\n\nError: {result.error}')
                else:
                    output_sections.append(
                        f'## Query: "{result.query}"\n\n{_format_search_summary(result)}'
                    )
            output = "\n\n".join(output_sections)
            output += (
                "\n\n---\n"
                f'Use get_search_content({{ responseId: "{response_id}", queryIndex: 0 }}) '
                "for stored full results."
            )
            successful = sum(result.error is None for result in search_results)
            return AgentToolResult(
                content=[TextContent(text=output)],
                details={
                    "responseId": response_id,
                    "queryCount": len(queries),
                    "successful": successful,
                    "workflow": workflow,
                    "provider": provider,
                },
            )

        return AgentTool(
            name="web_search",
            label="Web Search",
            description=(
                "Search the web using Perplexity AI, Exa, or Gemini. Returns a synthesized "
                "answer with source citations. Prefer queries with 2-4 varied angles."
            ),
            parameters=WEB_SEARCH_SCHEMA,
            execute_fn=execute,
            prompt_snippet=(
                "Use for web research. Prefer {queries:[...]} with varied angles for coverage."
            ),
        )

    def code_search_tool(self) -> AgentTool:
        async def execute(
            tool_call_id: str,
            arguments: Mapping[str, JSONValue],
            signal: ToolCancellationToken | None = None,
            on_update: ToolUpdateCallback | None = None,
        ) -> AgentToolResult:
            del tool_call_id, on_update
            _cancelled(signal)
            try:
                query = _as_string(arguments.get("query"), name="query", required=True)
                assert query is not None
                max_tokens = arguments.get("maxTokens", 5_000)
                if (
                    isinstance(max_tokens, bool)
                    or not isinstance(max_tokens, int)
                    or not 1_000 <= max_tokens <= 50_000
                ):
                    raise ValueError("maxTokens must be between 1000 and 50000")
                content = await self.providers.code_search(query.strip(), max_tokens=max_tokens)
                _cancelled(signal)
                return AgentToolResult(
                    content=[TextContent(text=content)],
                    details={"query": query, "maxTokens": max_tokens},
                )
            except Exception as error:
                return _error(
                    str(error),
                    query=arguments.get("query", ""),
                    maxTokens=arguments.get("maxTokens", 5_000),
                )

        return AgentTool(
            name="code_search",
            label="Code Search",
            description=(
                "Search for code examples, documentation, and API references from GitHub, "
                "Stack Overflow, and official documentation."
            ),
            parameters=CODE_SEARCH_SCHEMA,
            execute_fn=execute,
            prompt_snippet=(
                "Use for programming/API/library questions to retrieve concrete examples."
            ),
        )

    def fetch_content_tool(self) -> AgentTool:
        async def execute(
            tool_call_id: str,
            arguments: Mapping[str, JSONValue],
            signal: ToolCancellationToken | None = None,
            on_update: ToolUpdateCallback | None = None,
        ) -> AgentToolResult:
            del tool_call_id
            raw_urls = arguments.get("urls")
            if raw_urls is None:
                raw_url = arguments.get("url")
                raw_urls = [raw_url] if raw_url is not None else []
            if not isinstance(raw_urls, list):
                return _error("urls must be an array")
            urls = [item.strip() for item in raw_urls if isinstance(item, str) and item.strip()]
            if not urls:
                return _error("No URL provided")
            if on_update:
                on_update(
                    AgentToolResult(
                        content=[TextContent(text=f"Fetching {len(urls)} URL(s)...")],
                        details={"phase": "fetch", "progress": 0},
                    )
                )

            force_clone = arguments.get("forceClone", False)
            frames = arguments.get("frames")
            if not isinstance(force_clone, bool):
                return _error("forceClone must be a boolean")
            if frames is not None and (
                isinstance(frames, bool) or not isinstance(frames, int) or not 1 <= frames <= 12
            ):
                return _error("frames must be between 1 and 12")
            prompt = _as_string(arguments.get("prompt"), name="prompt")
            timestamp = _as_string(arguments.get("timestamp"), name="timestamp")
            model = _as_string(arguments.get("model"), name="model")

            outcomes: list[FetchOutcome] = []
            extracted: list[ExtractedContent] = []
            for index, url in enumerate(urls):
                _cancelled(signal)
                try:
                    outcome = await self.extractor.fetch(
                        url,
                        force_clone=force_clone,
                        prompt=prompt,
                        timestamp=timestamp,
                        frames=frames,
                        model=model,
                    )
                    outcomes.append(outcome)
                    extracted.append(outcome.extracted)
                except Exception as error:
                    extracted.append(ExtractedContent(url=url, error=str(error)))
                if on_update:
                    on_update(
                        AgentToolResult(
                            content=[TextContent(text=f"Fetched {index + 1}/{len(urls)} URL(s)")],
                            details={
                                "phase": "fetch",
                                "progress": (index + 1) / len(urls),
                            },
                        )
                    )

            response_id = uuid.uuid4().hex
            await self.store.put(
                StoredFetchData(
                    id=response_id,
                    timestamp_ms=int(time.time() * 1_000),
                    urls=extracted,
                )
            )
            successful = sum(item.error is None for item in extracted)
            total_chars = sum(len(item.content) for item in extracted)
            url_details: list[JSONValue] = list(urls)
            if len(urls) == 1:
                result = extracted[0]
                if result.error:
                    return _error(
                        result.error,
                        urls=url_details,
                        urlCount=1,
                        successful=0,
                        responseId=response_id,
                    )
                full_length = len(result.content)
                truncated = full_length > self.settings.max_inline_content
                output = result.content[: self.settings.max_inline_content]
                if truncated:
                    output += (
                        "\n\n[Content truncated...]\n\n---\n"
                        f"Showing {self.settings.max_inline_content} of {full_length} chars. "
                        f'Use get_search_content({{ responseId: "{response_id}", '
                        "urlIndex: 0 }) for full content."
                    )
                content: list[TextContent | ImageContent] = []
                first_outcome = outcomes[0] if outcomes else None
                if first_outcome:
                    for image in first_outcome.images:
                        content.append(ImageContent(data=image.data, mime_type=image.mime_type))
                        content.append(TextContent(text=image.label))
                content.append(TextContent(text=output))
                image_count = len(first_outcome.images) if first_outcome else 0
                return AgentToolResult(
                    content=content,
                    details={
                        "urls": url_details,
                        "urlCount": 1,
                        "successful": 1,
                        "totalChars": full_length,
                        "title": result.title,
                        "responseId": response_id,
                        "truncated": truncated,
                        "hasImage": image_count > 0,
                        "imageCount": image_count,
                        "prompt": prompt,
                        "timestamp": timestamp,
                        "frames": frames,
                        "duration": first_outcome.duration if first_outcome else None,
                    },
                )

            lines = ["## Fetched URLs", ""]
            for item in extracted:
                if item.error:
                    lines.append(f"- {item.url}: Error - {item.error}")
                else:
                    lines.append(f"- {item.title or item.url} ({len(item.content)} chars)")
            lines.extend(
                [
                    "",
                    "---",
                    f'Use get_search_content({{ responseId: "{response_id}", urlIndex: 0 }}) '
                    "to retrieve full content.",
                ]
            )
            return AgentToolResult(
                content=[TextContent(text="\n".join(lines))],
                details={
                    "urls": url_details,
                    "urlCount": len(urls),
                    "successful": successful,
                    "totalChars": total_chars,
                    "responseId": response_id,
                },
            )

        return AgentTool(
            name="fetch_content",
            label="Fetch Content",
            description=(
                "Fetch URL(s) and extract readable markdown. Supports YouTube transcripts, "
                "GitHub repositories, PDFs, and local or remote video frames. Content is stored "
                "and can be retrieved with get_search_content."
            ),
            parameters=FETCH_CONTENT_SCHEMA,
            execute_fn=execute,
            prompt_snippet=(
                "Use to extract readable URL, YouTube, repository, PDF, or video content."
            ),
        )

    def get_search_content_tool(self) -> AgentTool:
        async def execute(
            tool_call_id: str,
            arguments: Mapping[str, JSONValue],
            signal: ToolCancellationToken | None = None,
            on_update: ToolUpdateCallback | None = None,
        ) -> AgentToolResult:
            del tool_call_id, signal, on_update
            response_id = arguments.get("responseId")
            if not isinstance(response_id, str) or not response_id:
                return _error("responseId must be a non-empty string")
            data = await self.store.get(response_id)
            if data is None:
                return _error("Not found", responseId=response_id)

            if isinstance(data, StoredSearchData):
                query_data: QueryResult | None = None
                query = arguments.get("query")
                query_index = arguments.get("queryIndex")
                if query is not None:
                    query_data = next(
                        (item for item in data.queries if item.query == query),
                        None,
                    )
                    if query_data is None:
                        available = ", ".join(f'"{item.query}"' for item in data.queries)
                        return AgentToolResult(
                            content=[
                                TextContent(
                                    text=f'Query "{query}" not found. Available: {available}'
                                )
                            ],
                            details={"error": "Query not found"},
                        )
                elif isinstance(query_index, (int, float)) and not isinstance(query_index, bool):
                    index = int(query_index)
                    if index < 0 or index >= len(data.queries):
                        return AgentToolResult(
                            content=[
                                TextContent(
                                    text=f"Index {index} out of range (0-{len(data.queries) - 1})"
                                )
                            ],
                            details={"error": "Index out of range"},
                        )
                    query_data = data.queries[index]
                else:
                    available = ", ".join(
                        f'{index}: "{item.query}"' for index, item in enumerate(data.queries)
                    )
                    return AgentToolResult(
                        content=[
                            TextContent(text=f"Specify query or queryIndex. Available: {available}")
                        ],
                        details={"error": "No query specified"},
                    )
                if query_data.error:
                    return _error(query_data.error, query=query_data.query)
                return AgentToolResult(
                    content=[TextContent(text=_format_full_results(query_data))],
                    details={
                        "query": query_data.query,
                        "resultCount": len(query_data.results),
                    },
                )

            url_data: ExtractedContent | None = None
            url = arguments.get("url")
            url_index = arguments.get("urlIndex")
            if url is not None:
                url_data = next((item for item in data.urls if item.url == url), None)
                if url_data is None:
                    available = "\n  ".join(item.url for item in data.urls)
                    return AgentToolResult(
                        content=[TextContent(text=f"URL not found. Available:\n  {available}")],
                        details={"error": "URL not found"},
                    )
            elif isinstance(url_index, (int, float)) and not isinstance(url_index, bool):
                index = int(url_index)
                if index < 0 or index >= len(data.urls):
                    return AgentToolResult(
                        content=[
                            TextContent(text=f"Index {index} out of range (0-{len(data.urls) - 1})")
                        ],
                        details={"error": "Index out of range"},
                    )
                url_data = data.urls[index]
            else:
                available = "\n  ".join(
                    f"{index}: {item.url}" for index, item in enumerate(data.urls)
                )
                return AgentToolResult(
                    content=[
                        TextContent(text=f"Specify url or urlIndex. Available:\n  {available}")
                    ],
                    details={"error": "No URL specified"},
                )
            if url_data.error:
                return _error(url_data.error, url=url_data.url)
            return AgentToolResult(
                content=[TextContent(text=f"# {url_data.title}\n\n{url_data.content}")],
                details={
                    "url": url_data.url,
                    "title": url_data.title,
                    "contentLength": len(url_data.content),
                },
            )

        return AgentTool(
            name="get_search_content",
            label="Get Search Content",
            description="Retrieve full content from a previous web_search or fetch_content call.",
            parameters=GET_SEARCH_CONTENT_SCHEMA,
            execute_fn=execute,
            prompt_snippet=(
                "Use after search/fetch when full stored content is needed via responseId."
            ),
        )


def create_web_tools(
    *,
    settings: WebAccessSettings | None = None,
    store: SearchResultStore | None = None,
    client: httpx.AsyncClient | None = None,
    cwd: str | Path | None = None,
) -> list[AgentTool]:
    """Create the four independent web access tools."""
    resolved_settings = settings or WebAccessSettings()
    resolved_client = client or httpx.AsyncClient(
        timeout=httpx.Timeout(
            resolved_settings.request_timeout_seconds,
            connect=min(10.0, resolved_settings.request_timeout_seconds),
        ),
        headers={"User-Agent": "tau-web-access/0.1.0"},
    )
    tool_set = WebToolSet(
        settings=resolved_settings,
        store=store or MemorySearchResultStore(),
        client=resolved_client,
        cwd=(Path.cwd() if cwd is None else Path(cwd)),
    )
    return tool_set.tools()


__all__ = [
    "CODE_SEARCH_SCHEMA",
    "FETCH_CONTENT_SCHEMA",
    "GET_SEARCH_CONTENT_SCHEMA",
    "WEB_SEARCH_SCHEMA",
    "WebToolSet",
    "create_web_tools",
]
