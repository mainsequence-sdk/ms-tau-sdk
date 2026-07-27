# tau-web-access

A Python/Tau port of the headless tool contract from `pi-web-access`.

This distribution lives at `packages/tau-web-access` inside the Astro
monorepo. It is independently packageable but is not a separate repository.

The package exports four provider-neutral Tau tools:

- `web_search`
- `code_search`
- `fetch_content`
- `get_search_content`

It does not import Astro and does not require Node. Search providers are selected
from explicit settings or `EXA_API_KEY`, `PERPLEXITY_API_KEY`, and
`GEMINI_API_KEY`. Exa's public MCP endpoint is the zero-configuration fallback.

```python
from tau_web_access import WebAccessSettings, create_web_tools

tools = create_web_tools(
    settings=WebAccessSettings(),
    cwd="/workspace",
)
```

The default result store is process-local. Long-lived services should inject a
`SearchResultStore` implementation backed by their session storage.
