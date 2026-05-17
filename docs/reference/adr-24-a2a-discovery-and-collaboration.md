# ADR: A2A Discovery And Collaboration

## Status

Proposed

## Note

This ADR defines the prompt-layer and tooling-layer collaboration rules for A2A.

The concrete non-debug production discovery and runtime-access flow is now specified in:

- [`adr-25-production-a2a-discovery-and-runtime-access.md`](./adr-25-production-a2a-discovery-and-runtime-access.md)

The follow-up decision to retire `mainsequence-project-coder` and keep
`mainsequence-project-executor` as the only project implementation runtime is specified in:

- [`adr-26-retire-project-coder-for-project-executor.md`](./adr-26-retire-project-coder-for-project-executor.md)

## Context

Astro now needs a general agent-to-agent collaboration model that applies across:

- `astro-orchestrator`
- `mainsequence-project-coder`
- `mainsequence-project-executor`

This is separate from session switching.

Today, Astro already has two existing collaboration mechanisms:

- legacy project-session handoff
  - changed the active session
  - was intended for real project-session handoff
- repo-local specialist delegation
  - bounded child-process work inside the current runtime topology

Neither of those is the same thing as A2A communication.

The new requirement is:

- an agent may answer directly
- or it may ask another agent for bounded help through A2A
- without changing the active session

At the same time, Astro should not clutter the shared Astro prompt with transport details or
rewrite all agent roles around A2A.

## Problem

Without an explicit discovery rule, the current prompt contracts have a binary behavior:

- answer directly if the request is in role
- refuse if the request is out of role

That is too rigid once A2A exists.

We need a small routing rule that says:

- before refusing, check whether another known agent is better suited
- if so, use A2A instead of pretending the active agent must do everything itself

But that behavior must stay disciplined:

- it must not become general scope expansion
- it must not silently replace legacy project-session handoff behavior
- it must not force the orchestrator to delegate without the user's consent

## Decision

Astro will treat A2A as a shared collaboration modality, not as a new business role.

The shared Astro prompt will keep the existing five user-facing capabilities and add a sixth,
routing-only directive:

1. Help the user interact with the Main Sequence platform.
2. Help the user build new intelligence via Main Sequence projects.
3. Answer questions about `mainsequence-sdk`.
4. Analyze a Main Sequence workspace.
5. Tell which LLM model is powering you and details about the model.
6. Follow A2A discovery guidelines when another agent may be better suited to answer or assist with
   the request.

Directive 6 is intentionally not a user-facing business capability. It is a discovery and routing
rule that applies before refusal when the active agent is not the best fit.

## A2A Discovery Guidelines

The shared discovery rule should stay small and explicit.

At minimum, Astro agents should follow these guidelines:

1. First decide whether you should answer directly within your current role and scope.
2. If another known agent appears better suited, you may use A2A instead of changing session.
3. A2A does not imply any project-session handoff.
4. A2A does not expand the active agent's allowed scope or role.
5. If a request is marked as A2A, respond as agent-to-agent rather than user-to-agent.
6. If the request specifies a response format or output schema, follow it exactly.
7. If no suitable agent is discoverable, refuse briefly or redirect according to the current role.

These guidelines belong in the prompt layer. They should not depend on the caller remembering to
encode all of that behavior manually in each prompt body.

## Confirmation Rule

The confirmation behavior is intentionally asymmetric.

### `astro-orchestrator`

For user-originated requests, `astro-orchestrator` must confirm with the user before initiating A2A
communication.

This preserves the orchestrator's role as the user-facing control plane and prevents silent hidden
delegation from the main conversation.

### `mainsequence-project-coder` and `mainsequence-project-executor`

Project-scoped agents do not require separate user confirmation before bounded A2A requests, as
long as:

- the A2A request stays within the current project scope
- the A2A request stays within the active task or delegated work
- the runtime supports A2A

This keeps project-local execution practical and avoids unnecessary user round-trips for internal
coordination.

## Prompt-Layer Placement

To keep the implementation minimally intrusive, the rule should be split across the smallest useful
set of prompt files.

### Shared Astro prompt

`.pi/APPEND_SYSTEM.md` should:

- add directive 6
- soften the current refuse-immediately behavior so A2A discovery happens first when appropriate
- define a short `A2A discovery guidelines` section
- make the orchestrator confirmation rule explicit

### Project-scoped prompt contract

The shared `.pi/APPEND_SYSTEM.md` prompt contract should include a short A2A section that:

- allows bounded A2A collaboration
- states that A2A does not imply a session switch
- states that the response format is a hard contract
- states that project-attached runtimes do not need separate user confirmation for bounded A2A

### Child-specialist runtime policy

`pi/extensions/hooks/project-policy/child-policy.md` should be relaxed carefully.

It should still block unrestricted recursive repo-local specialist delegation by default, but it
should not block bounded A2A when the runtime explicitly supports it.

It should also state that child specialists do not need separate user confirmation for bounded A2A
inside their active task scope.

## Relationship To Streamer A2A Endpoints

This ADR is about the prompt-layer discovery and collaboration rules.

The Astro streamer route design for:

- `POST /api/a2a/chat`
- `POST /api/a2a/cancel`

is covered by the executor-runtime ADR:

- [`adr-23-backend-mediated-project-executor-runtimes.md`](./adr-23-backend-mediated-project-executor-runtimes.md)

That ADR defines the transport surface. This ADR defines how agents should reason about using that
surface.

## A2A Discovery And Communication Tooling

Prompt rules alone are not enough. Astro also needs concrete runtime tooling that agents can use
for discovery and communication.

This ADR defines one logical A2A capability with two exposed tool entry points:

1. discovery-only
2. discovery plus communication

The important design point is that the agent should not have to know transport details. The A2A
tooling owns the routing behavior.

### Tool purpose

The tooling exists so an Astro agent can:

- summarize the user's intent into a discovery prompt
- ask the backend for candidate agents
- inspect available A2A candidates when discovery alone is the user-facing goal
- choose the best candidate from the backend response when actual communication is needed
- send an A2A request using the selected candidate
- receive the structured A2A response

This is intentionally different from:

- legacy project-session handoff
- repo-local specialist delegation
- direct pod-to-pod communication

### Discovery phase

For discovery, Astro should always make a backend request using a search prompt that summarizes the
user's intent.

The detailed non-debug production discovery path is defined in:

- [`adr-25-production-a2a-discovery-and-runtime-access.md`](./adr-25-production-a2a-discovery-and-runtime-access.md)

This ADR keeps the higher-level discovery rule and candidate shape, while ADR 25 defines the
backend-facing implementation steps.

The backend discovery response is expected to return candidates of this shape:

```json
{
  "agent_id": "string-or-number",
  "agent_description": "string",
  "a2a_card": {}
}
```

Where:

- `agent_id`
  - backend-owned agent identity used for routing
- `agent_description`
  - short natural-language description used by Astro to choose the best candidate
- `a2a_card`
  - machine-readable capability card or contract used to understand how the candidate expects A2A
    requests

Astro should use the returned candidates to select the best target before sending any request.

### Communication phase

After selecting a candidate, Astro should send the request through the backend, which acts as the
router for A2A communication.

The backend remains the transport control plane:

- Astro does not call arbitrary agent URLs directly in the normal production path
- Astro sends the A2A request through the backend
- the backend routes that request to the selected agent deployment

The concrete non-debug runtime-access and routing path is defined in:

- [`adr-25-production-a2a-discovery-and-runtime-access.md`](./adr-25-production-a2a-discovery-and-runtime-access.md)

The request body sent through the backend should match the JSON contract expected by
`POST /api/a2a/chat` on the target Astro runtime.

That A2A request contract is also intentionally `TBD` here, but it must be designed so the target
runtime receives:

- the caller identity or caller metadata needed for trust and observability
- the selected `agent_id`
- the task or message payload
- the required response format or output schema
- any session or conversation correlation fields needed by the backend

### Why this is one logical capability

Even though discovery and communication are separate phases, they should still be treated as one
logical A2A capability.

That keeps the agent contract simple:

- discover A2A-capable agents when needed
- ask for bounded A2A help when needed
- let the tooling handle discovery and routing

The agent should decide **whether** to use A2A. The tooling should decide **how** to perform A2A.

## Local Debug Override With `A2A_DEV_PROJECT`

We need a local debug path that does not depend on the backend A2A router before that backend
contract exists.

When `A2A_DEV_PROJECT` is set, the tool should bypass the backend path and enter a local debug
mode.

### Discovery in local debug mode

In local debug mode, discovery should be mocked.

It should always return one candidate of the normal discovery shape:

```json
{
  "agent_id": "local-dev-agent",
  "agent_description": "constructed description",
  "a2a_card": {}
}
```

The description and card should be constructed from:

```text
${A2A_DEV_PROJECT}/.agents/agent_card.json
```

In containerized local development, `A2A_DEV_PROJECT` is the host-side selector for the mounted
project, while Astro reads the mounted project path inside the container.

That file becomes the local-debug source of truth for the candidate's advertised A2A behavior.

### Communication in local debug mode

In local debug mode, the communication path should not go through the backend router.

Instead:

- discovery is mocked from `A2A_DEV_PROJECT/.agents/agent_card.json`
- communication is routed directly to the local dev executor container
- the target is the dev `astro-project-executor` runtime rather than a backend-routed project
  executor deployment

This gives us a practical way to debug A2A end-to-end against the mounted local executor before the
backend router is implemented.

### Production safety rule

The `A2A_DEV_PROJECT` override is for local debugging only.

If `A2A_DEV_PROJECT` is not set:

- Astro should use the production discovery-and-routing path
- and if the backend route is still missing, the tool should raise `not implemented`

Astro must not silently invent a fallback production routing path.

## Consequences

### Positive

- A2A becomes a first-class collaboration path without redefining agent roles
- the prompt change stays compact
- orchestrator behavior remains user-controlled
- project-scoped agents can collaborate without unnecessary confirmation loops
- session switching and A2A stay clearly separated
- local A2A development can proceed before the backend router is finished

### Negative

- the shared Astro prompt becomes slightly more complex
- refusal behavior becomes conditional on discovery instead of purely static
- child-policy wording must be careful not to reopen uncontrolled recursive delegation
- the A2A tool now needs explicit prod and local-debug modes
- production implementation remains intentionally incomplete until the backend contract is defined

## Implementation Plan

1. Add directive 6 to `.pi/APPEND_SYSTEM.md`.
2. Add a short `A2A discovery guidelines` section to `.pi/APPEND_SYSTEM.md`.
3. Update the parent out-of-scope behavior so it applies A2A discovery before refusal when
   appropriate.
4. Add minimal A2A-collaboration guidance to `.pi/APPEND_SYSTEM.md`.
5. Update `pi/extensions/hooks/project-policy/child-policy.md` so bounded A2A is allowed without
   reopening unrestricted recursive specialist delegation.
6. Add A2A discovery tooling that:
   - supports a discovery-only path for listing available A2A-capable agents
   - supports a discovery-plus-communication path for sending a bounded request
   - summarizes user intent into a discovery prompt
   - calls the backend discovery route
   - selects the best returned candidate
   - sends the A2A request through the backend router
7. Define the candidate response contract as:
   - `agent_id`
   - `agent_description`
   - `a2a_card`
8. Leave production backend paths as `TBD` and raise `not implemented` until the backend contract
   exists.
9. Add a local debug override using `A2A_DEV_PROJECT`.
10. In local debug mode:
    - mock discovery from `${A2A_DEV_PROJECT}/.agents/agent_card.json`
    - route A2A communication directly to the local dev executor container
11. Keep the actual A2A transport and deterministic machine-context injection in the Astro streamer
    route layer defined by ADR 23.

## Verification Plan

- confirm `.pi/APPEND_SYSTEM.md` contains directive 6 and a compact shared discovery section
- confirm the orchestrator prompt now checks A2A discovery before direct refusal when appropriate
- confirm the orchestrator prompt requires user confirmation before A2A initiation
- confirm project-attached runtimes can use bounded A2A without separate user confirmation
- confirm child-specialist runtime policy still blocks unrestricted recursive specialist delegation
  while allowing bounded A2A
- confirm prompt wording keeps A2A distinct from project-session handoff behavior
- confirm the discovery-only A2A tool can list available A2A-capable agents without sending a
  request
- confirm the A2A request tool always performs discovery before communication
- confirm production mode raises `not implemented` when the backend A2A route is still undefined
- confirm `A2A_DEV_PROJECT` causes discovery to be mocked from `.agents/agent_card.json`
- confirm `A2A_DEV_PROJECT` causes communication to route to the local dev executor container
- confirm the candidate-selection logic uses `agent_description` and `a2a_card`

## Tasks

- [x] Add directive 6 to `.pi/APPEND_SYSTEM.md`.
- [x] Add a compact `A2A discovery guidelines` section to `.pi/APPEND_SYSTEM.md`.
- [x] Update the parent out-of-scope rule so A2A discovery happens before refusal when
  appropriate.
- [x] Add bounded A2A guidance to the shared prompt contract.
- [x] Update child-specialist runtime policy for bounded A2A without reopening unrestricted
  recursive delegation.
- [x] Add A2A discovery tooling for both discovery-only and discovery-plus-communication flows.
- [x] Summarize user intent into a discovery prompt before A2A candidate lookup.
- [x] Add the backend discovery contract with candidate fields:
  - `agent_id`
  - `agent_description`
  - `a2a_card`
- [x] Replace the temporary production `not implemented` plan with the CLI-backed backend discovery
  and runtime-access contract defined by ADR 25.
- [x] Add `A2A_DEV_PROJECT` local debug mode.
- [x] In local debug mode, mock discovery from `${A2A_DEV_PROJECT}/.agents/agent_card.json`.
- [x] In local debug mode, route communication directly to the local dev executor container.
