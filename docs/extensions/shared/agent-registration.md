# Shared backend session helpers

Source: [`pi/extensions/shared/agent-registration.ts`](../../../pi/extensions/shared/agent-registration.ts)

## Purpose

Provide the Main Sequence backend session-fetch, auth-header, and user-id helpers used by Astro and
the stream runtime. The file still contains a legacy session-start helper, but chat/A2A runtime
paths should no longer depend on Astro-initiated session creation.

## Main responsibilities

- decide whether backend-backed session features are enabled
- resolve the Main Sequence user id
- resolve backend auth headers from runtime credentials
- fetch backend `AgentSession` records for hydration

Legacy helper still present in code:

- `startBackendAgentSession(...)`

## Credentials

The helper can read credentials from:

- explicit user id passed by the caller
- `ASTRO_MAINSEQUENCE_USER_ID`

Backend API authentication is resolved through Main Sequence runtime credentials. Astro does
not use user token env vars for backend session start or backend session hydration.

## Main exports

- `shouldRegisterAgents(...)`
- `resolveMainsequenceUserId(...)`
- `fetchBackendAgentSession(...)`

## Related files

- [`../hooks/agent-registration.md`](../hooks/agent-registration.md)
- [`../../../interface/stream/server.ts`](../../../interface/stream/server.ts)
