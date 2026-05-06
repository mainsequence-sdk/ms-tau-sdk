# Shared backend session helpers

Source: [`pi/extensions/shared/agent-registration.ts`](../../../pi/extensions/shared/agent-registration.ts)

## Purpose

Provide the Main Sequence backend session-start, session-fetch, auth-header, and user-id helpers used by Astro and the stream runtime.

## Main responsibilities

- decide whether backend-backed session features are enabled
- resolve the Main Sequence user id
- resolve backend auth headers from runtime credentials
- call backend `start_new_session`
- fetch backend `AgentSession` records for hydration

## Credentials

The helper can read credentials from:

- explicit user id passed by the caller
- `ASTRO_MAINSEQUENCE_USER_ID`

Backend API authentication is resolved through Main Sequence runtime credentials. Astro does
not use user token env vars for backend session start or backend session hydration.

## Main exports

- `shouldRegisterAgents(...)`
- `resolveMainsequenceUserId(...)`
- `startBackendAgentSession(...)`
- `fetchBackendAgentSession(...)`

## Related files

- [`../hooks/agent-registration.md`](../hooks/agent-registration.md)
- [`../../../interface/stream/server.ts`](../../../interface/stream/server.ts)
