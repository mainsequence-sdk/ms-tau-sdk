# ADR 46: Disable A2A Push Notifications Until Backend Support Exists

Status: Accepted
Date: 2026-08-21
Implementation Status: Implemented

## Context

Astro exposes the A2A push-notification configuration routes and accepts both current and legacy
JSON-RPC method names. These operations configure webhook delivery for asynchronous task updates.

The canonical backend `AgentTaskViewSet` does not provide persistence or CRUD operations for task
push-notification configurations. Astro therefore cannot make these configurations durable across
runtime restarts and replicas, and it cannot truthfully advertise push-notification support.
Inventing a canonical route or continuing to call the removed legacy route would hide the missing
contract behind runtime failures.

## Decision

Push notifications are unsupported until the backend capability is implemented.

Astro will:

- report `capabilities.pushNotifications: false` in every Agent Card it serves, even if an
  upstream stored card still advertises the capability;
- keep the standard REST route shapes so clients receive a deterministic protocol response;
- return HTTP `400` with the A2A `PushNotificationNotSupportedError` representation and
  `PUSH_NOTIFICATION_NOT_SUPPORTED` error detail for REST create, get, list, and delete requests;
- recognize current and legacy JSON-RPC push-configuration methods and return A2A error code
  `-32003` with the same error detail;
- make no backend request for a push-notification configuration operation; and
- remove the legacy backend client methods instead of silently mapping them to an unverified
  endpoint.

Normal A2A message send, streaming, task read/list/cancel, and task subscription are unaffected.

## Re-enabling The Capability

Astro may advertise and execute push notifications only after the platform provides:

- canonical backend create, get, list, and delete contracts scoped to an authorized `AgentTask`;
- durable configuration storage that works across runtime restarts and replicas;
- webhook delivery with authentication, URL validation, retry, and terminal-task cleanup rules;
- contract and end-to-end tests covering persistence and delivery; and
- coordinated Agent Card and runtime deployment changes.

Until all of those conditions are met, `pushNotifications` remains false and every related
operation remains explicitly unsupported.

## Consequences

- Clients discover the actual supported capability surface before attempting an operation.
- Clients that call a push route anyway receive a stable A2A error rather than a backend 404 or
  generic JSON-RPC method-not-found response.
- Astro has no process-local or guessed backend push-configuration state.
- Adding push notifications later requires an explicit backend and runtime contract change.

## References

- A2A capability validation and push-notification errors:
  https://a2a-protocol.org/latest/specification/
- ADR 37: A2A Standard Wire Protocol
- ADR 43: Backend-Backed A2A Task Gap Analysis
