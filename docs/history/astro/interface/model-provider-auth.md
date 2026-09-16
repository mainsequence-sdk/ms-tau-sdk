# Provider Control

Astro exposes no model catalog, provider status, sign-in, attempt, cancel,
manual exchange, signoff, or credential-revoke routes. Django owns those
durable control-plane operations and callers use its canonical APIs directly.

Astro receives only execution-scoped evidence:

- session execution gets the selected provider/model, a typed
  `provider_control` projection, and the exact credential in one Tau bootstrap
  v2 response; and
- agent-targeted sessionless execution gets the same projection and exact
  credential in one existing `agent_uid` hydration response.

For built-ins, Astro intersects that evidence with the pinned Tau execution
registry before constructing an in-memory provider. For Organization custom
providers, the same schema-version-1 evidence and hydration endpoint feed the
existing OpenAI-compatible constructor. Astro requires an explicit base URL,
an `organization_custom` credential kind, and either `openai-completions` or
`openai-responses`; it permits API-key, header-only, combined, or intentionally
unauthenticated configuration. It adds no custom registry, provider endpoint,
schema version, or second hydration call. Astro never fetches the general
catalog while executing and never persists or logs provider secrets.

See [ADR 51](../adrs/adr-51-consume-django-provider-control-for-tau-execution.md).
