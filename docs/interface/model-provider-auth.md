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

Astro intersects that evidence with the pinned Tau execution registry before
constructing an in-memory provider. It never fetches the general catalog while
executing and never persists or logs provider secrets.

See [ADR 51](../adrs/adr-51-consume-django-provider-control-for-tau-execution.md).
