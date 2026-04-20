# Shared agent registration

Source: [`pi/extensions/shared/agent-registration.ts`](../../../pi/extensions/shared/agent-registration.ts)

## Purpose

Provide the deterministic Main Sequence backend registration and backend session-start helpers used by Astro parents, specialists, and the stream runtime.

## Main responsibilities

- decide whether backend registration is enabled
- resolve the Main Sequence user id
- build deterministic `agent_unique_id` values
- resolve backend auth headers from runtime credentials
- call backend `get_or_create`
- call backend `start_new_session`

## Deterministic identity

`buildAgentUniqueId(...)` creates stable backend identities:

- normal agents: `{agent_name}_{user_id}`
- `mainsequence-project-coder`: `{agent_name}_{user_id}_{project_id}`

## Credentials

The helper can read credentials from:

- explicit user id passed by the caller
- `ASTRO_MAINSEQUENCE_USER_ID`

Backend API authentication is resolved through Main Sequence runtime credentials. Astro does
not use user token env vars for backend registration.

## Main exports

- `shouldRegisterAgents(...)`
- `resolveMainsequenceUserId(...)`
- `buildAgentUniqueId(...)`
- `registerMainsequenceAgent(...)`
- `startBackendAgentSession(...)`

## Failure model

The shared helper returns structured failures. The caller decides whether those failures are fatal. Astro's registration hook currently treats them as fatal on session start.

## Related files

- [`../hooks/agent-registration.md`](../hooks/agent-registration.md)
- [`../../../interface/stream/server.ts`](../../../interface/stream/server.ts)
