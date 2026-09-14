# ADR 47: Explicit A2A Response Kind

Status: Accepted
Date: 2026-08-23
Implementation Status: Implemented across Astro Tau, `tdag-django`, and `mainsequence-sdk`

The Main Sequence A2A profile uses `ROLE_REQUESTER` for request Messages and
`ROLE_RESPONDER` for response Messages. These values describe direction directly and are not
aliases for identity-oriented role names.

## Context

Astro's non-streaming A2A message-send contract currently accepts
`configuration.returnImmediately`. The Boolean chooses between two materially different protocol
results:

- `false` or omission waits for execution and returns a `Message`;
- `true` creates asynchronous work and immediately returns a `Task`.

The name describes request timing rather than the result the caller wants. It hides the difference
between direct execution and the durable task lifecycle behind an indirect Boolean, and gives
discovery no useful vocabulary for advertising the supported behavior.

The ambiguity propagates across the platform. Astro interprets the raw Boolean, `tdag-django`
persists Agent Cards without validating this communication profile, MCP agent discovery does not
expose it, and `mainsequence-sdk` exposes `return_immediately: bool` without a typed task-result
workflow.

Main Sequence needs a field that states the requested result. This ADR defines it as a versioned
Main Sequence A2A extension rather than presenting it as an unmodified core A2A field.

## Decision

Remove `configuration.returnImmediately` from the Main Sequence A2A profile and replace it with:

```json
{
  "configuration": {
    "responseKind": "message"
  }
}
```

`responseKind` has exactly two values:

| Value | Contract |
| --- | --- |
| `message` | Execute to completion and return a successful A2A `Message`. |
| `task` | Create asynchronous work and immediately return the persisted A2A `Task`. |

Omission defaults to `message`. The field is not a latency or scheduling hint: it selects the
successful response type and lifecycle contract.

No Boolean alias or permanent translation will be retained. A request containing
`returnImmediately` fails validation with an error naming `configuration.responseKind` as its
replacement.

## Extension And Agent Card

The stable extension URI is:

```text
https://mainsequence.ai/a2a/extensions/response-kind/v1
```

An Agent Card advertises it as follows:

```json
{
  "capabilities": {
    "extensions": [
      {
        "uri": "https://mainsequence.ai/a2a/extensions/response-kind/v1",
        "description": "Select whether message:send returns a completed message or an asynchronous task.",
        "required": false,
        "params": {
          "supportedResponseKinds": ["message", "task"],
          "defaultResponseKind": "message"
        }
      }
    ]
  }
}
```

`required` is `false` because a normal request may omit the extension field and receive the default
direct `Message`. A client explicitly sending `configuration.responseKind` activates the extension
with:

```http
A2A-Extensions: https://mainsequence.ai/a2a/extensions/response-kind/v1
```

`supportedResponseKinds` is a non-empty subset of `message` and `task`.
`defaultResponseKind` must be included in that set. An Agent Card without the extension is treated
as Message-only. Clients must never infer Task support from the existence of task endpoints.

## Execution Contracts

For `responseKind: "message"`, Astro waits for execution and returns:

```json
{
  "message": {
    "role": "ROLE_RESPONDER",
    "messageId": "01JRESULT",
    "parts": [{"text": "The result ..."}]
  }
}
```

A direct execution that reaches an internal `COMPLETED` state without producing a valid agent
message is not successful. Astro returns an explicit execution/output-contract error instead of an
empty answer.

For `responseKind: "task"`, Astro creates durable asynchronous work and returns:

```json
{
  "task": {
    "id": "01JTASK",
    "contextId": "01JCONTEXT",
    "status": {"state": "TASK_STATE_SUBMITTED"}
  }
}
```

The caller then uses the advertised task get, list, cancel, or subscribe operations. Selecting
`task` is valid only when the effective Agent Card advertises it.

REST and JSON-RPC entrypoints use the same parser, validation, dispatch, and serialization.

`message:stream` rejects `configuration.responseKind`: streaming already selects a distinct result
contract. The agent-targeted sessionless endpoints in ADR 44 remain Message-only, never create an
`AgentTask`, and reject `task`.

## Validation And Replay Semantics

Astro rejects:

- a value other than `message` or `task`;
- `responseKind` without the matching `A2A-Extensions` header;
- an extension URI not advertised by the receiving Agent Card;
- `task` when the effective card does not advertise it;
- any occurrence of `returnImmediately`; and
- any `responseKind` on the streaming operation.

Failures use the existing A2A invalid-request or unsupported-operation mappings. The legacy-field
error names both the rejected field and its replacement and never silently executes the request.

The two response kinds intentionally have different persistence boundaries:

- `task` uses the existing durable `AgentTask` and `AgentTaskMessage` idempotency mechanism;
- `message` creates no `AgentTask` or `AgentTaskMessage` merely to obtain an idempotency record.

Consequently, this ADR does not claim durable protocol-level replay protection for direct
`message` execution. Clients may reuse the same `messageId` for an exact retry, but must not
assume that a lost direct response can be replayed without executing another turn. Adding that
guarantee requires a separate non-task message-send receipt/result store; it must not be emulated
by creating a hidden Agent Task.

## Astro Impact

Astro must:

1. Replace raw Boolean interpretation with a typed request configuration and `ResponseKind` enum.
2. Resolve omission to `message` before dispatch.
3. Keep direct-message and asynchronous-task execution paths explicit.
4. Create and return an `AgentTask` only for `task`.
5. Validate extension activation against the locally served effective Agent Card.
6. Preserve the selected response kind across authentication retry and internal dispatch.
7. Fail direct execution that does not produce a valid output `Message`.
8. Normalize REST and JSON-RPC through one implementation.
9. Update fixtures, tests, active examples, ADR 37, and ADR 44.

Task support does not imply push-notification support. ADR 46 remains in force.

## `tdag-django` Impact

The backend is the persisted-card and discovery authority. It must not leave the communication
contract as an unvalidated JSON blob interpreted only by Astro.

### CodeRepository Agent Card template

The generated `.agents/agent_card.json` template includes the response-kind extension under
`capabilities.extensions`.

The current template correctly prohibits projects from declaring deployment URLs, security, and
runtime-derived flags. This ADR narrows that boundary: the source card may declare a stable,
runtime-independent communication profile, but it still must not declare:

- `supportedInterfaces` or deployment URLs;
- runtime security schemes;
- push-notification support;
- harness-derived streaming availability; or
- Task support the selected harness/backend cannot implement.

Runtime materialization intersects the source declaration with the deployed harness and backend
capabilities. It must never add `task` merely because the template mentions it.

### Card validation and persistence

`CodeRepositoryExecutor` currently reads and persists the registered Agent Card as raw JSON. A shared
normalizer/validator must run before persistence and publication. It must:

- validate `capabilities.extensions` and unique extension URIs;
- validate the exact response-kind parameters;
- require a non-empty supported set and a supported default;
- preserve unrelated A2A extensions;
- keep protocol extensions separate from Markdown capability-path synchronization; and
- report actionable project-registration errors.

The retired backend `AgentCapability` kind named `extension` was never an A2A protocol extension.
A2A `capabilities.extensions` remains protocol metadata and is unaffected by ADR 53.

### Agent discoverability

Backend discovery exposes a normalized projection so consumers do not parse raw Agent Cards:

```json
{
  "a2aProfile": {
    "responseKindExtensionUri": "https://mainsequence.ai/a2a/extensions/response-kind/v1",
    "supportedResponseKinds": ["message", "task"],
    "defaultResponseKind": "message"
  }
}
```

This projection is added to full Agent serialization and lightweight semantic-search/list results
used for agent selection. Field casing follows each endpoint's established wire convention.

The projection is derived from the validated effective card. It does not expose the entire card in
lightweight results, accept caller overrides, or affect semantic embedding text/ranking. Missing
extension data normalizes to Message-only support.

### MCP schemas and skills

Backend serialization alone is insufficient because the MCP `agent.search` result has a
closed-world schema. The implementation updates together:

- agent search row/output schemas;
- the `agents/a2a_communication` skill;
- the `agents/code_repository_to_agent` skill and its Agent Card template;
- platform ontology descriptions;
- manifest hashes for changed packaged resources; and
- MCP schema, packaging, resource, and end-to-end discovery tests.

The A2A communication skill discovers the profile, defaults to `message`, requests `task` only when
advertised, sends the activation header, branches on the discriminated result, and follows the Task
when selected. It never infers Task mode from an empty answer.

The code-repository-to-agent skill explains that the stable response profile belongs in the source Agent
Card while runtime URLs, security, push notifications, and harness flags remain runtime-owned.

## `mainsequence-sdk` Impact

The SDK adds:

```python
class A2AResponseKind(str, Enum):
    MESSAGE = "message"
    TASK = "task"
```

It also adds an `AgentA2AProfile` model to both the full `Agent` model and
`AgentSemanticSearchResult`. A missing profile from an older backend normalizes to Message-only and
never invents Task support.

The public send helper replaces:

```python
return_immediately: bool = False
```

with:

```python
response_kind: A2AResponseKind = A2AResponseKind.MESSAGE
```

The SDK must:

- emit `configuration.responseKind` and the activation header;
- reject a response kind not advertised by discovery;
- preserve both across credential refresh/retry;
- deserialize a discriminated Message-or-Task result; and
- provide task get, wait/poll, and cancel helpers before promoting Task mode.

The CLI replaces `--return-immediately` with `--response-kind message|task` and handles both result
types. The generated `a2a_sdk_execution` skill, SDK ADR 29, fixtures, examples, and tests must be
updated.

## Coordinated Rollout

This is an intentional contract correction, not an indefinite compatibility alias:

1. Release SDK support for the new enum, header, Message-only fallback, and discriminated results.
2. Deploy Astro parsing, validation, direct-output failure behavior, and Task dispatch without yet
   advertising Task.
3. Deploy backend card validation, normalized discovery, MCP schemas/skills, and SDK discovery.
4. Regenerate/migrate cards and verify each runtime's durable Task lifecycle.
5. Advertise `task` only for agents passing contract and end-to-end tests.
6. Enforce the minimum client version and reject `returnImmediately`.

During rollback, discovery removes `task` before Astro is rolled back. Otherwise a new client could
request a Task from an older runtime that ignores the field and returns a Message.

## Consequences

- Requests state the result callers actually want.
- Discovery, MCP skills, SDK types, and runtime behavior share one vocabulary.
- Direct execution cannot report empty success.
- Task support becomes an explicitly advertised end-to-end capability.
- Existing Boolean callers must migrate.
- Main Sequence owns validation/documentation for this profile on top of core A2A.

## Non-Goals

This ADR does not implement push notifications, change session persistence or compaction, change
`message.contextId`, make direct or sessionless responses create tasks, define durable replay
semantics for direct Message execution, define scheduling guarantees, or add Task support to a
runtime that cannot persist and retrieve Tasks correctly.

## Acceptance Criteria

- No public model, SDK helper, CLI flag, fixture, or active example uses `returnImmediately` after
  cutover.
- Direct requests return a valid `Message` or explicit error, never empty success.
- Direct requests never create or mutate an `AgentTask` or `AgentTaskMessage`.
- Task requests return a persisted and retrievable `Task`.
- REST and JSON-RPC behave equivalently.
- Effective Agent Cards advertise the exact supported response kinds and default.
- Full-agent and lightweight discovery expose the normalized profile without changing ranking.
- MCP schemas, both affected skills, ontology, hashes, and packaging tests agree.
- SDK discovery, request construction, retry, parsing, CLI output, and Task helpers have contract
  tests.
- Missing/old card data resolves to Message-only, never implicit Task support.

## References

- A2A Protocol Specification: https://a2a-protocol.org/v1.0.0/specification/
- A2A Protocol Extensions: https://a2a-protocol.org/latest/topics/extensions/
- ADR 37: A2A Standard Wire Protocol
- ADR 43: Backend-Backed A2A Task Persistence
- ADR 44: Agent-Targeted Sessionless Responses
- ADR 46: Disable A2A Push Notifications Until Backend Support Exists
