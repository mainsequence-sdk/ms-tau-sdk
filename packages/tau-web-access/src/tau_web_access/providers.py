"""Search provider implementations and fallback selection."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import httpx

from .models import ExtractedContent, QueryResult, SearchResult
from .settings import WebAccessSettings

SearchProviderName = Literal["auto", "exa", "perplexity", "gemini"]

EXA_ANSWER_URL = "https://api.exa.ai/answer"
EXA_SEARCH_URL = "https://api.exa.ai/search"
EXA_MCP_URL = "https://mcp.exa.ai/mcp"
PERPLEXITY_URL = "https://api.perplexity.ai/chat/completions"
GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
)


@dataclass(frozen=True, slots=True)
class SearchOptions:
    num_results: int = 5
    include_content: bool = False
    recency_filter: str | None = None
    domain_filter: tuple[str, ...] = ()


def _error_text(response: httpx.Response) -> str:
    return response.text[:300].strip()


def _parse_exa_mcp_text(body: str) -> str:
    candidates: list[dict[str, Any]] = []
    for line in body.splitlines():
        if not line.startswith("data:"):
            continue
        try:
            candidates.append(json.loads(line[5:].strip()))
        except json.JSONDecodeError:
            continue
    if not candidates:
        try:
            candidates.append(json.loads(body))
        except json.JSONDecodeError as error:
            raise RuntimeError("Exa MCP returned an invalid response") from error

    for payload in candidates:
        if payload.get("error"):
            message = payload["error"].get("message", "Unknown error")
            raise RuntimeError(f"Exa MCP error: {message}")
        result = payload.get("result", {})
        for content in result.get("content", []):
            text = content.get("text")
            if content.get("type") == "text" and isinstance(text, str) and text.strip():
                if result.get("isError"):
                    raise RuntimeError(text.strip())
                return text
    raise RuntimeError("Exa MCP returned empty content")


def _parse_exa_mcp_results(text: str) -> list[SearchResult]:
    results: list[SearchResult] = []
    for block in text.split("Title: ")[1:]:
        title, _, rest = block.partition("\n")
        url_marker = "URL: "
        url_start = rest.find(url_marker)
        if url_start < 0:
            continue
        url, _, content = rest[url_start + len(url_marker) :].partition("\n")
        snippet = content.removeprefix("Text: ").strip().removesuffix("---").strip()
        results.append(SearchResult(title=title.strip(), url=url.strip(), snippet=snippet[:1_000]))
    return results


class SearchProviders:
    """Provider facade used by the Tau tool executors."""

    def __init__(self, *, settings: WebAccessSettings, client: httpx.AsyncClient) -> None:
        self.settings = settings
        self.client = client

    def provider_order(self, requested: SearchProviderName) -> tuple[str, ...]:
        if requested != "auto":
            return (requested,)
        order: list[str] = []
        if self.settings.exa_api_key:
            order.append("exa")
        if self.settings.perplexity_api_key:
            order.append("perplexity")
        if self.settings.gemini_api_key:
            order.append("gemini")
        if "exa" not in order:
            order.append("exa")
        return tuple(order)

    async def search(
        self,
        query: str,
        *,
        requested: SearchProviderName,
        options: SearchOptions,
    ) -> QueryResult:
        errors: list[str] = []
        for provider in self.provider_order(requested):
            try:
                if provider == "exa":
                    result = await self._search_exa(query, options)
                elif provider == "perplexity":
                    result = await self._search_perplexity(query, options)
                elif provider == "gemini":
                    result = await self._search_gemini(query, options)
                else:
                    raise RuntimeError(f"Unsupported provider: {provider}")
                result.provider = provider
                return result
            except Exception as error:
                errors.append(f"{provider}: {error}")
                if requested != "auto":
                    break
        raise RuntimeError("; ".join(errors) or "No search provider is available")

    async def code_search(self, query: str, *, max_tokens: int) -> str:
        return await self.call_exa_mcp(
            "get_code_context_exa",
            {"query": query, "tokensNum": max_tokens},
        )

    async def call_exa_mcp(self, tool_name: str, arguments: dict[str, Any]) -> str:
        response = await self.client.post(
            EXA_MCP_URL,
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
            },
        )
        if not response.is_success:
            raise RuntimeError(f"Exa MCP error {response.status_code}: {_error_text(response)}")
        return _parse_exa_mcp_text(response.text)

    async def _search_exa(self, query: str, options: SearchOptions) -> QueryResult:
        api_key = self.settings.exa_api_key
        if not api_key:
            enriched = [query]
            for domain in options.domain_filter:
                qualifier = f"-site:{domain[1:]}" if domain.startswith("-") else f"site:{domain}"
                enriched.append(qualifier)
            if options.recency_filter:
                enriched.append(f"past {options.recency_filter}")
            text = await self.call_exa_mcp(
                "web_search_exa",
                {
                    "query": " ".join(enriched),
                    "numResults": options.num_results,
                    "livecrawl": "fallback",
                    "type": "auto",
                    "contextMaxCharacters": 50_000 if options.include_content else 3_000,
                },
            )
            mcp_results = _parse_exa_mcp_results(text)
            answer_parts = [
                f"{item.snippet}\nSource: {item.title} ({item.url})"
                for item in mcp_results
                if item.snippet
            ]
            mcp_inline = [
                ExtractedContent(url=item.url, title=item.title, content=item.snippet)
                for item in mcp_results
                if options.include_content and item.snippet
            ]
            return QueryResult(
                query=query,
                answer="\n\n".join(answer_parts) or text,
                results=mcp_results,
                inline_content=mcp_inline,
            )

        use_search = bool(
            options.include_content
            or options.recency_filter
            or options.domain_filter
            or options.num_results != 5
        )
        headers = {"x-api-key": api_key, "Content-Type": "application/json"}
        if not use_search:
            response = await self.client.post(
                EXA_ANSWER_URL,
                headers=headers,
                json={"query": query, "text": True},
            )
            if not response.is_success:
                raise RuntimeError(f"Exa API error {response.status_code}: {_error_text(response)}")
            data = response.json()
            answer_results = [
                SearchResult(
                    title=item.get("title") or f"Source {index + 1}",
                    url=item["url"],
                )
                for index, item in enumerate(data.get("citations", []))
                if item.get("url")
            ]
            return QueryResult(
                query=query,
                answer=data.get("answer", ""),
                results=answer_results,
            )

        included = [domain for domain in options.domain_filter if not domain.startswith("-")]
        excluded = [domain[1:] for domain in options.domain_filter if domain.startswith("-")]
        payload: dict[str, Any] = {
            "query": query,
            "type": "auto",
            "numResults": options.num_results,
            "contents": {
                "text": True if options.include_content else {"maxCharacters": 3_000},
                "highlights": True,
            },
        }
        if included:
            payload["includeDomains"] = included
        if excluded:
            payload["excludeDomains"] = excluded
        if options.recency_filter:
            days = {"day": 1, "week": 7, "month": 30, "year": 365}[options.recency_filter]
            payload["startPublishedDate"] = (datetime.now(UTC) - timedelta(days=days)).isoformat()

        response = await self.client.post(EXA_SEARCH_URL, headers=headers, json=payload)
        if not response.is_success:
            raise RuntimeError(f"Exa API error {response.status_code}: {_error_text(response)}")
        data = response.json()
        results: list[SearchResult] = []
        inline: list[ExtractedContent] = []
        answer: list[str] = []
        for index, item in enumerate(data.get("results", [])):
            url = item.get("url")
            if not url:
                continue
            content = " ".join(item.get("highlights") or []) or str(item.get("text") or "")
            title = item.get("title") or f"Source {index + 1}"
            results.append(SearchResult(title=title, url=url, snippet=content[:1_000]))
            if content:
                answer.append(f"{content[:1_000]}\nSource: {title} ({url})")
                if options.include_content:
                    inline.append(ExtractedContent(url=url, title=title, content=content))
        return QueryResult(
            query=query,
            answer="\n\n".join(answer),
            results=results,
            inline_content=inline,
        )

    async def _search_perplexity(self, query: str, options: SearchOptions) -> QueryResult:
        key = self.settings.perplexity_api_key
        if not key:
            raise RuntimeError("PERPLEXITY_API_KEY is not configured")
        payload: dict[str, Any] = {
            "model": "sonar",
            "messages": [{"role": "user", "content": query}],
            "return_related_questions": False,
        }
        if options.recency_filter:
            payload["search_recency_filter"] = options.recency_filter
        domains = [domain for domain in options.domain_filter if domain]
        if domains:
            payload["search_domain_filter"] = domains
        response = await self.client.post(
            PERPLEXITY_URL,
            headers={"Authorization": f"Bearer {key}"},
            json=payload,
        )
        if not response.is_success:
            raise RuntimeError(
                f"Perplexity API error {response.status_code}: {_error_text(response)}"
            )
        data = response.json()
        answer = data.get("choices", [{}])[0].get("message", {}).get("content", "")
        citations = data.get("citations", [])
        results = [
            SearchResult(title=f"Source {index + 1}", url=url)
            for index, url in enumerate(citations[: options.num_results])
            if isinstance(url, str)
        ]
        return QueryResult(query=query, answer=answer, results=results)

    async def _search_gemini(self, query: str, options: SearchOptions) -> QueryResult:
        key = self.settings.gemini_api_key
        if not key:
            raise RuntimeError("GEMINI_API_KEY is not configured")
        response = await self.client.post(
            GEMINI_URL,
            params={"key": key},
            json={
                "contents": [{"parts": [{"text": query}]}],
                "tools": [{"google_search": {}}],
            },
        )
        if not response.is_success:
            raise RuntimeError(f"Gemini API error {response.status_code}: {_error_text(response)}")
        data = response.json()
        candidate = data.get("candidates", [{}])[0]
        answer = "".join(
            part.get("text", "")
            for part in candidate.get("content", {}).get("parts", [])
            if isinstance(part, dict)
        )
        chunks = candidate.get("groundingMetadata", {}).get("groundingChunks", []) or candidate.get(
            "grounding_metadata", {}
        ).get("grounding_chunks", [])
        results: list[SearchResult] = []
        for index, chunk in enumerate(chunks[: options.num_results]):
            web = chunk.get("web", {})
            url = web.get("uri")
            if url:
                results.append(
                    SearchResult(title=web.get("title") or f"Source {index + 1}", url=url)
                )
        return QueryResult(query=query, answer=answer, results=results)


__all__ = ["SearchOptions", "SearchProviderName", "SearchProviders"]
