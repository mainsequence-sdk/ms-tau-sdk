# ADR 43: Backend-Backed A2A Task Gap Analysis

Status: Proposed
Date: 2026-07-01
Implementation Status: Not implemented

## Context

Astro already exposes the public A2A boundary at `rpc_url`:

- REST: `/api/a2a/v1/message:send`, `/api/a2a/v1/message:stream`, `/api/a2a/v1/tasks`,
  `/api/a2a/v1/tasks/{id}`, `/api/a2a/v1/tasks/{id}:cancel`,
  `/api/a2a/v1/tasks/{id}:subscribe`, and push notification config routes.
- JSON-RPC: `SendMessage`, `SendStreamingMessage`, `GetTask`, `ListTasks`, and `CancelTask`
  through `/api/a2a/rpc`.

The A2A standard already defines the task boundary. `message:send` returns either a public A2A
`Task` or a direct public A2A `Message`. `message:stream` can stream either a message-only response
or task lifecycle updates. A `Task` has `id`, optional `contextId`, `status`, optional `artifacts`,
optional `history`, and optional `metadata`.

Astro's current task implementation is protocol-shaped but process-local:

- `A2AStandardTaskRecord` models task `id`, `contextId`, `status`, `artifacts`, and error metadata.
- `a2aStandardTasks` stores tasks in a process-local `Map`.
- `a2aStandardMessageSends` stores idempotent `message:send` responses in a process-local `Map`.
- `a2aStandardPushNotificationConfigs` stores push config records in process-local maps.
- `createA2AStandardTask()` generates `task-${randomUUID()}` and inserts it into the local map.
- `GET /tasks`, `GET /tasks/{id}`, cancel, subscribe, JSON-RPC task methods, and push config
  handlers all read from those local maps.

The backend now has a separate implementation track for durable A2A task persistence. The backend
owns platform records such as:

- `Agent`
- `AgentTask`
- `AgentTaskMessage`
- `AgentTaskOutput`
- `AgentTaskEvent`
- `BaseJob`
- `JobRun`
- `JobRunOutput`
- `Artifact`

The purpose of this ADR is only to identify what is missing to replace Astro's process-local A2A
task state with backend-backed task state.

## Non-Goals

- Do not change normal direct `Message` behavior.
- Do not add a new Astro decision about whether work is a task. The A2A response shape already
  says whether the response is a `Task` or a `Message`.
- Do not make Astro derive task ownership from `message.contextId`, `AgentSession.uid`, or
  `agent_uid`.
- Do not expose backend internal database `id`.
- Do not make Django/backend the live A2A transport.

## Identity Contract

The backend serializer contract is:

- `AgentTask.uid` is the Django public UID for the backend task row.
- `AgentTask.task_id` is the public A2A protocol `Task.id`.
- backend internal `id` is private and must not be exposed by Astro.

Astro's public A2A task routes receive A2A `Task.id`. Backend task mutation/read routes currently
use `{task_uid}`, meaning `AgentTask.uid`. Therefore the missing bridge is a reliable mapping from
public A2A `task_id` to backend `uid`.

## Gap Analysis

| A2A task surface | Astro today | Backend record/API | Missing |
| --- | --- | --- | --- |
| `message:send` returning a direct `Message` | Implemented. Normal non-task path returns `{ message }`. | Not task related. | No change. |
| `message:send` returning a `Task` | Implemented only with `createA2AStandardTask()` and `a2aStandardTasks`. | `AgentTask` with `task_id`, `context_id`, `status`, `status_message`, timestamps, metadata. | Replace process-local task creation/storage with backend-backed task record for the same standard A2A task response path. Astro must serialize the public `Task` from backend task data. |
| `message:stream` task lifecycle | Creates a local task, emits the current task snapshot over SSE, updates local state, then ends. | `AgentTask`, `AgentTaskEvent`, `AgentTaskOutput`. | Persist streamed task status/output updates in backend and serialize events from backend-backed state. Full live fanout can remain a later phase. |
| Public `Task.id` | Generated locally as `task-${randomUUID()}`. | `AgentTask.task_id`. | Stop using a process-only ID as durable truth. Public `Task.id` must be backed by backend `task_id`. |
| Backend task handle | None. Local task record has no backend UID. | `AgentTask.uid`. | Add adapter mapping/lookup from A2A `task_id` to `AgentTask.uid` because backend read/update/cancel/message/output/event endpoints use `task_uid`. |
| Task status | Stored in `A2AStandardTaskRecord.status`. | `AgentTask.status`, `status_message`, `status_timestamp`; `AgentTaskEvent` for status changes. | Map current A2A states to backend statuses and update backend instead of only mutating the local record. |
| `GET /tasks/{id}` and JSON-RPC `GetTask` | Reads `a2aStandardTasks.get(taskId)`. Lost after restart and invisible across replicas. | `GET /orm/api/agents/v1/tasks/{task_uid}/`. | Resolve public `task_id` to backend `uid`, fetch `AgentTask`, serialize A2A `Task`. |
| `GET /tasks` and JSON-RPC `ListTasks` | Lists every task in the local map. No durable pagination/filtering. | `GET /orm/api/agents/v1/tasks/` and agent-scoped list. | Implement A2A list parameters from backend: at least `contextId`, `status`, `pageSize`, `pageToken`, `historyLength`, `statusTimestampAfter`, `includeArtifacts` where supported. |
| `POST /tasks/{id}:cancel` and JSON-RPC `CancelTask` | Looks up local task, cancels active local runtime if present, marks local task canceled. | `POST /orm/api/agents/v1/tasks/{task_uid}/cancel/`. | Resolve `task_id` to backend `uid`, call backend cancel, still cancel active local runtime when present, serialize returned backend task. |
| `message.taskId` continuation | Not durably backed by task lookup. Terminal/context validation depends on local task availability. | Backend task read plus `AgentTask.context_id` and status. | Resolve `message.taskId` through backend, validate context/terminal state using backend task, then continue normal runtime flow. |
| Task history | Local task has no durable `history`; status message is transient. | `AgentTaskMessage`; list/add task messages endpoints. | Persist and read task messages through backend for `historyLength`. Do not use messages as task outputs. |
| Task artifacts/results | Completion artifacts are stored only in the local task record. | `AgentTaskOutput`, `JobRunOutput`, `Artifact`. | Persist task-facing artifacts/outputs through backend. Current backend gap: `AgentTaskOutput` has read API only, so output creation needs backend projection from `JobRunOutput` or a write/projection endpoint. |
| Task events/subscription | `:subscribe` returns the current local task once as SSE and closes. | `AgentTaskEvent` list endpoint. | Backend-backed subscribe can start by reading current backend task/event state. True live subscription requires backend event streaming or local fanout wired to backend updates. |
| Push notification configs | Stored in local maps; no webhook delivery. | No task push-config record listed in current backend task serializer contract. | Either keep as phase-limited process-local config, or add backend persistence before advertising durable push notification support. |

## Backend Serializer Contract To Use

Astro should use the current backend task serializers exactly:

| Operation | Endpoint | Serializer |
| --- | --- | --- |
| Retrieve task | `GET /orm/api/agents/v1/tasks/{task_uid}/` | `AgentTaskSerializer` |
| List tasks | `GET /orm/api/agents/v1/tasks/` and `GET /orm/api/agents/v1/agents/{agent_uid}/tasks/` | `AgentTaskSerializer(many=True)` |
| Cancel task | `POST /orm/api/agents/v1/tasks/{task_uid}/cancel/` | `AgentTaskSerializer` |
| Update status | `POST /orm/api/agents/v1/tasks/{task_uid}/status/` | request `AgentTaskStatusUpdateSerializer`, response `AgentTaskSerializer` |
| Add message | `POST /orm/api/agents/v1/tasks/{task_uid}/messages/` | request `AgentTaskMessageCreateSerializer`, response `AgentTaskMessageSerializer` |
| List messages | `GET /orm/api/agents/v1/tasks/{task_uid}/messages/` | `AgentTaskMessageSerializer(many=True)` |
| List outputs | `GET /orm/api/agents/v1/tasks/{task_uid}/outputs/` | `AgentTaskOutputSerializer(many=True)` |
| List events | `GET /orm/api/agents/v1/tasks/{task_uid}/events/` | `AgentTaskEventSerializer(many=True)` |
| Create/update job output | `POST /orm/api/pods/job-run/{job_run_uid}/outputs/` | `JobRunOutputSerializer` |

Task creation belongs to the backend/receiving-agent task creation path. The Astro gap is to stop
using process-local task records when the standard A2A boundary is already dealing with a `Task`.

## Implementation Tasks

### Phase 1: Adapter

- [ ] Add a typed `BackendAdapter.a2a` task capability.
- [ ] Add backend response types for `AgentTaskSerializer`, `AgentTaskMessageSerializer`,
      `AgentTaskOutputSerializer`, `AgentTaskEventSerializer`, and `JobRunOutputSerializer`.
- [ ] Add `resolveTaskByProtocolId(taskId)` or equivalent backend lookup support from
      `AgentTask.task_id` to `AgentTask.uid`.
- [ ] Add adapter methods for retrieve, list, cancel, update status, add/list messages,
      list outputs, list events, and job output creation.
- [ ] Convert backend task records to public A2A `Task` objects without exposing backend internal
      `id`.

### Phase 2: Replace Process-Local Task State

- [ ] Replace `a2aStandardTasks` as the durable task source of truth.
- [ ] Replace `createA2AStandardTask()` local ID generation with the backend-backed task creation
      path already owned by backend/receiving-agent task persistence.
- [ ] Keep direct `{ message }` responses unchanged.
- [ ] Keep the same existing public A2A task trigger points: current task-returning `message:send`,
      `message:stream`, and `message.taskId` continuation.
- [ ] Keep only minimal local state for an active runtime turn, cancellation of that active local
      turn, and transient SSE delivery.

### Phase 3: Task Operations

- [ ] Implement REST `GET /api/a2a/v1/tasks/{id}` and JSON-RPC `GetTask` through backend task read.
- [ ] Implement REST `GET /api/a2a/v1/tasks` and JSON-RPC `ListTasks` through backend task list.
- [ ] Implement REST `POST /api/a2a/v1/tasks/{id}:cancel` and JSON-RPC `CancelTask` through backend
      cancel plus active local runtime cancellation when present.
- [ ] Implement `message.taskId` continuation by resolving the backend task, validating context and
      terminal state, then continuing normal runtime execution.
- [ ] Implement `historyLength` through backend task messages.

### Phase 4: Outputs And Events

- [ ] Persist status transitions through backend status updates.
- [ ] Persist task messages through `AgentTaskMessage`.
- [ ] Persist task outputs through backend-supported output path.
- [ ] If only `JobRunOutput` is writable, require backend projection into `AgentTaskOutput`.
- [ ] Back `:subscribe` from backend task/event state instead of the local task map.

### Phase 5: Documentation And Tests

- [ ] Update `docs/a2a/README.md` so task routes are documented as backend-backed instead of
      in-memory phase 1.
- [ ] Add fake-backend tests for task response, get, list, cancel, `message.taskId` continuation,
      history, outputs, and restart/replica-safe task lookup.
- [ ] Add adapter contract tests for backend serializer shape and protocol serialization.

## Remaining Backend Contract Gaps

- Backend needs a clear lookup from public A2A `Task.id` / serializer `task_id` to `AgentTask.uid`,
  or task endpoints need to accept protocol `task_id` directly.
- `AgentTaskOutput` currently has read API only. If task results must be durable A2A artifacts,
  backend needs either projection from `JobRunOutput` or a task-output write/projection endpoint.
- Push notification config persistence is not represented in the provided backend task record list.
  Until that exists, push config support remains process-local or a documented phase limitation.

## References

- A2A protocol specification: https://a2a-protocol.org/latest/specification/
