# ADR: Backend-Owned AgentSession Allocation

## Note

This ADR predates the retirement of `mainsequence-project-coder`.

It also predates the stricter backend-only session-initiation rule introduced in
[`adr-27-backend-only-session-initiation.md`](./adr-27-backend-only-session-initiation.md).
That newer ADR supersedes the stream-runtime session-creation assumptions in this document. The
backend still owns `AgentSession` creation, but Astro chat/A2A requests should no longer initiate
that creation themselves.

The current project-implementation architecture is defined by:

- [`adr-23-backend-mediated-project-executor-runtimes.md`](./adr-23-backend-mediated-project-executor-runtimes.md)
- [`adr-26-retire-project-coder-for-project-executor.md`](./adr-26-retire-project-coder-for-project-executor.md)

The backend-owned allocation contract remains valid, but the current runtime examples use
`mainsequence-project-executor` instead of the retired coder session-switch flow.

## Status

Accepted. Implemented and verified in Astro.

## Context

Before this change, Astro created backend `AgentSession` records from inside the stream runtime. It
resolved or created the backend Agent, built session metadata, chose runtime ids, started the backend
session, then mirrored enough of that state into local files for Pi and the frontend.

That split made the backend only partially authoritative. It owned an `AgentSession` row after Astro
created it, but Astro still owned important creation decisions:

- Agent lookup and registration
- `agent_unique_id` derivation
- workflow selection
- frontend/runtime resume key shape
- session metadata shape
- initial local history/metadata materialization
- project-scoped workflow state

The emptyDir checkpoint design requires a stronger backend contract. If session files are restored
from backend checkpoints and protected by backend leases, the backend must first be the authority
that allocates the session identity and initial session state.

## Decision

The backend is the authoritative creator of Astro `AgentSession` records, using the existing backend
route that already includes the Agent id.

Astro keeps the existing Agent resolution step for this implementation:

```http
POST /orm/api/agents/v1/agents/get_or_create/
```

Astro starts sessions through the existing Agent-scoped session route:

```http
POST /orm/api/agents/v1/agents/{agent_id}/start_new_session/
```

This is the correct integration point because `agent_id` is part of the URL. This implementation
does not add a separate `agent_sessions/allocate_astro_session` endpoint.

The backend session-start route creates the `AgentSession`, persists Astro-provided metadata as
opaque JSON, and returns the runtime contract Astro needs to launch Pi.

Astro remains the runtime executor. In the later checkpoint rollout, that means Astro will:

- restore local files from backend checkpoint state
- launch Pi with a local `--session` file
- stream events to the frontend
- write local files that the sidecar checkpoints

But Astro should not be the source of truth for creating the backend session identity.

## Implementation Order

This ADR is implemented before the full emptyDir checkpoint rollout.

Astro implementation order:

1. Consume the backend-owned `start_new_session` response.
2. Derive `runtime_session_id` from the backend `AgentSession.id`.
3. Remove local Docker PVC emulation for session files.
4. Track restore-before-Pi-launch and sidecar checkpoint flushing in the storage ADR.

This keeps AgentSession allocation separate from the later checkpoint restore/flush work.

## Current Backend Model

This ADR does not add or rename `AgentSession` model fields. The current backend model already has
the fields Astro needs for session allocation.

```python
class AgentSession(CreatedByMixin, models.Model):
    agent = models.ForeignKey(
        Agent,
        on_delete=models.CASCADE,
        related_name="sessions",
    )
    parent_session = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="child_sessions",
    )
    root_session = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="descendant_sessions",
    )
    status = models.CharField(
        max_length=32,
        choices=AgentSessionStatus.choices,
        default=AgentSessionStatus.PENDING,
        help_text="Lifecycle status of this concrete agent session.",
    )
    started_at = models.DateTimeField(
        default=timezone.now,
        db_index=True,
        help_text="Timestamp when the session started or was created.",
    )
    ended_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Timestamp when the session finished, if it has finished.",
    )
    llm_provider = models.CharField(max_length=64, blank=True, default="")
    llm_model = models.CharField(max_length=128, blank=True, default="")
    engine_name = models.CharField(max_length=128, blank=True, default="")
    runtime_config_snapshot = models.JSONField(default=dict, blank=True)
    error_detail = models.TextField(blank=True, default="")
    external_session_id = models.CharField(max_length=255, blank=True, default="")
    runtime_session_id = models.CharField(max_length=255, blank=True, default="")
    thread_id = models.CharField(max_length=255, blank=True, default="")
    session_metadata = models.JSONField(default=dict, blank=True)
    spawned_by_step = models.ForeignKey(
        "AgentSessionStep",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="spawned_sessions",
    )
```

The backend already provides the behavior Astro needs without an `AgentSession` schema change:

- return the required `start_new_session_response` shape defined below
- return `agent_session_id`; Astro derives the frontend/runtime `runtime_session_id` as
  `str(agent_session_id)`
- treat `thread_id` as optional frontend correlation only, not as a unique session identity
- store `session_metadata` unchanged as opaque JSON
- keep Astro workflow semantics in Astro

`created_by_user` is request/input data. It must map to the existing `CreatedByMixin` convention.
Do not add a new `created_by_user` field to `AgentSession`.

## Frontend Contract

Frontend calls Astro, not the backend allocation endpoint:

```http
POST /api/chat
```

This ADR does not require a frontend endpoint shape change. Astro keeps the existing `/api/chat`
contract documented in `docs/interface/request.md` and maps those values into the backend allocation
contract.

Relevant existing request values:

```python
chat_request_values = {
    "threadId": str,
    "agentType": "astro-orchestrator | mainsequence-project-executor",
    "userId": str,
    "messages": list,
    "newChat": bool,
    "runtime_session_id": str | None,
    "model": dict | None,
    "projectId": str | None,
    "cwd": str | None,
    "sessionMetadata": dict,
}
```

Astro owns validation of frontend intent:

- `agentType`
- `newChat` versus `runtime_session_id`
- `projectId` and `cwd` requirements for `mainsequence-project-executor` when the runtime is not already pinned
- model binding
- session metadata semantics

## Astro To Backend Contract

Astro keeps the existing Agent resolution call:

```http
POST /orm/api/agents/v1/agents/get_or_create/
```

```python
agent_get_or_create_payload = {
    "agent_type": str,
    "agent_unique_id": str,
}

agent_get_or_create_response = {
    "id": int,
    "agent_type": str,
    "agent_unique_id": str,
}
```

Deterministic ids remain Astro-owned:

```python
astro_orchestrator_agent_unique_id = f"astro-orchestrator_{created_by_user}"
project_executor_agent_unique_id = f"mainsequence-project-executor_{created_by_user}_{project_id}"
```

Astro then calls the existing Agent-scoped session route:

```http
POST /orm/api/agents/v1/agents/{agent_id}/start_new_session/
```

Exact request body Astro sends to `start_new_session`:

```python
start_new_session_request = {
    "status": "running",
    "created_by_user": created_by_user,
    "thread_id": frontend_thread_id,
    "llm_provider": llm_provider,
    "llm_model": llm_model,
    "engine_name": "astro",
    "runtime_config_snapshot": runtime_config_snapshot,
    "session_metadata": session_metadata,
}
```

Request value rules:

- `created_by_user` is Astro's normalized user id and maps through `CreatedByMixin`.
- `frontend_thread_id` comes from the frontend request when present. Astro passes it through as
  backend `thread_id` for UI correlation, but it is not unique and must not be used as the session
  identity.
- `llm_provider`, `llm_model`, `runtime_config_snapshot`, and `session_metadata` are computed by
  Astro.
- `engine_name` is the Astro runtime identity for this integration and is always `"astro"`.
- Astro derives the frontend/local `runtime_session_id` as `str(agent_session_id)` after allocation.
- `error_detail` uses the existing model default on successful allocation.

The current backend `AgentStartSessionRequest` schema does not expose `parent_session_id`.
The current creation-first flow does not require project-coder-style session-switch provenance.
Project-scoped executor lineage, when needed later, should be represented through backend-native
session relationships or future executor-specific metadata rather than a revived session-switch
contract.

Astro-owned session metadata is sent inside `session_metadata` and stored unchanged:

```python
orchestrator_session_metadata = {
    "source": "frontend",
    "agent_type": "astro-orchestrator",
    "agent_type": str,
    "created_by_user": str,
    "session_model_binding": dict | None,
    "session_config_overrides": dict | None,
}

project_executor_session_metadata = {
    "source": "frontend",
    "agent_type": "mainsequence-project-executor",
    "agent_type": str,
    "created_by_user": str,
    "project_id": str,
    "project_cwd": str,
    "project_repo_root": str | None,
    "project_runtime_snapshot": dict | None,
    "project_image_ref": str | None,
    "session_model_binding": dict | None,
    "session_config_overrides": dict | None,
}
```

Exact response body backend returns from `start_new_session`.

The backend must return this shape to Astro after creating the existing `AgentSession` row:

```python
start_new_session_response = {
    # Existing AgentSession identity.
    "id": agent_session.id,
    "agent_session_id": agent_session.id,

    # Existing AgentSession lifecycle fields.
    "status": agent_session.status,
    "started_at": agent_session.started_at.isoformat(),
    "ended_at": agent_session.ended_at.isoformat() if agent_session.ended_at else None,

    # Existing AgentSession runtime fields.
    "thread_id": agent_session.thread_id,
    "llm_provider": agent_session.llm_provider,
    "llm_model": agent_session.llm_model,
    "engine_name": agent_session.engine_name,
    "runtime_config_snapshot": agent_session.runtime_config_snapshot,
    "error_detail": agent_session.error_detail,
    "session_metadata": agent_session.session_metadata,

    # Existing Agent identity required by Astro stream chunks.
    "agent": {
        "id": agent_session.agent_id,
        "agent_type": agent_session.agent.agent_type,
        "agent_unique_id": agent_session.agent.agent_unique_id,
    },

}
```

Required value rules:

- `id` and `agent_session_id` are exactly `agent_session.id`.
- `thread_id` is the optional frontend correlation value when one was sent. Empty string is allowed.
- `error_detail` is the current `AgentSession.error_detail`; on successful allocation it is the model
  default empty string.
- `session_metadata` is exactly the object Astro sent.
- `agent.id`, `agent.agent_type`, and `agent.agent_unique_id` come from the resolved Agent.

Astro maps the backend response to frontend stream chunks:

```python
new_session = {
    "agent_session_id": response["agent_session_id"],
    "session_key": str(response["agent_session_id"]),
    "runtime_session_id": str(response["agent_session_id"]),
    "agent_id": response["agent"]["id"],
    "agent_type": response["session_metadata"]["agent_type"],
    "agent_type": response["agent"]["agent_type"],
    "agent_unique_id": response["agent"]["agent_unique_id"],
    "thread_id": response["thread_id"] or str(response["agent_session_id"]),
}
```

Astro no longer emits a dedicated project-implementation session-handoff chunk as part of the current
creation-first flow. New project creation stays on the orchestrator session, and direct executor
sessions use normal `new_session` semantics when they are started explicitly.

## Backend Contract Consumed By Astro

The existing `start_new_session` route owns generic persistence:

- create the `AgentSession` row using the existing model
- map `created_by_user` through `CreatedByMixin`
- return `agent_session_id`; Astro derives `runtime_session_id = str(agent_session_id)` for the
  frontend and local runtime
- persist first-class fields Astro sends: `engine_name`, `llm_provider`, `llm_model`,
  `runtime_config_snapshot`, `session_metadata`
- persist `thread_id` if provided, but never use it for uniqueness or resume identity
- leave `error_detail` as the existing model default on successful allocation
- return the exact `start_new_session_response` shape defined above

Astro owns workflow semantics:

- project-scoped session metadata fields
- `session_metadata` shape

## Project-Scoped Executor Sessions

Project-scoped executor allocation is Astro workflow logic.

For the backend allocation route, it is just another `start_new_session` call against the already
resolved `mainsequence-project-executor` Agent. Astro includes:

- `thread_id`
- `session_metadata`

The backend stores those fields exactly like any other session allocation. It does not inspect or
validate the project-scoped keys inside `session_metadata`.

## Existing Session Attach

For existing sessions, Astro should not allocate a new `AgentSession`. It should call backend
attach/restore operations using the existing `runtime_session_id`.

The `start_new_session` route is only for new sessions and direct executor session starts.

## Consequences

### Positive

- backend owns the complete `AgentSession` creation contract
- Astro no longer duplicates backend session identity logic
- emptyDir checkpoint restore has an authoritative session id from the AgentSession allocation
- project-scoped workflow state becomes backend-visible from creation time
- future non-Astro session creators can reuse the same contract

### Negative

- backend stores Astro-owned metadata opaquely, so workflow-specific invariants remain Astro's
  responsibility
- workflow metadata is not queryable by the backend unless explicit indexes are added later
- checkpoint restore/flush remains separate storage ADR work

## Non-Goals

- moving Pi execution into the backend
- making the backend stream assistant responses
- storing per-token or per-chunk stream deltas during allocation
- replacing the sidecar checkpoint process

## Astro Implementation Tasks

- [x] Keep Agent lookup/creation on the existing `agents/get_or_create/` endpoint.
- [x] Make Astro derive canonical `runtime_session_id = str(agent_session_id)`.
- [x] Implement the `AgentSession` row mapping exactly as defined in this ADR.
- [x] Map backend `AgentSession` response fields into `new_session` stream chunks exactly as
      defined in this ADR.
- [x] Keep `session_metadata` Astro-owned and opaque to backend workflow validation.
- [x] Reject or strip frontend `session_metadata` keys reserved for Astro-owned metadata.
- [x] Ensure project-scoped executor metadata passes through `session_metadata` unchanged.
- [x] Update Astro to consume the richer `start_new_session` response for `new_chat: true`.
- [x] Keep existing attach/resume flow for existing `runtime_session_id` sessions.
- [x] Remove Astro-side pending runtime id generation after Astro derives canonical
      `runtime_session_id = str(agent_session_id)`.
- [x] Update `docs/interface/request.md` to match the frontend contract after code enforcement lands.
- [x] Verify the implementation with `npm run check`, `git diff --check`, and Docker Compose config.
