# Security and access guide

This page explains who an Agent built on this SDK acts as, what it can reach, and what the people
who build and use such an Agent need to know. The [public API](./public-api.md) and the
[runtime contract](./runtime-contract.md) define the exact behavior. These are platform and SDK
access rules, not a guarantee that arbitrary project code is isolated from the runtime's credentials.

## Availability

This guide covers the current SDK development documentation. The delegation envelope and the
application MCP connections in [ADR 0021](../adrs/0021-mcp-connections-on-the-persons-identity.md)
are listed under **Unreleased** in the [changelog](../../CHANGELOG.md#unreleased). They take effect
together with the matching platform change, deployed in one cutover: this SDK works only with that
platform, and earlier SDK releases stop acting for people once it is deployed. Check the deployed
platform and your installed SDK before relying on them.

## Who is who

| Term | Meaning |
| --- | --- |
| Person | A Main Sequence user. |
| Session owner | The person a conversation (AgentSession) belongs to. |
| The Agent's workload | The identity a deployed Agent runs as. Each Agent has its own. It is never the person who pushed or deployed the Agent, never an Organization admin, and it starts with no access. |
| Requester | The person whose own request a turn is serving. Always a person, never an Agent or another workload. |

## Who can talk to an Agent's session

When Main Sequence hosts the runtime, every request carries an assertion that the platform signs;
the runtime never trusts gateway identity headers. A request that addresses a session is accepted
only from:

- the session owner;
- an Organization admin; or
- for a delegated child session, the Agent that delegated to it.

Anyone else gets `403` before the runtime acts. An Organization admin who writes into another
person's session is not its requester: the answer goes to the owner's history, and that turn
cannot act for anyone. See [session ownership](./runtime-contract.md#session-ownership).

## Conversation and Task permissions

Sharing an Agent lets another person use it; it does not share your conversation. The following
rules describe platform API and Command Center access for people who can view the Agent in the
selected Environment. Platform operators with superuser access have additional operational access.

| Operation | Who may perform it |
| --- | --- |
| Read a conversation, its history, logs, or artifacts | Its owner or an Organization admin. Agent viewers and editors do not gain conversation access from that grant alone. |
| Update, archive, delete, or change a conversation's configuration | Its owner. An Organization admin's oversight reads do not confer ordinary mutation rights. |
| Request that a conversation stop | Its owner or an Agent editor; Organization admins can edit Agents in their Organization. |
| Read an ordinary Agent's Tasks and outputs | People who can view the Agent. |
| Read Tasks and outputs of an Agent enabled to act for requesters | A requester of that Task or an Organization admin. Agent visibility alone is insufficient. |
| Cancel a visible Task | A requester of the Task or an Agent editor. |
| Continue a visible Task awaiting input or authorization | A requester of the Task. There is no Organization-admin override for continuation. |

A Task requester includes the person who created it or a verified person who requested its creation
or a continuation. Losing access to the Agent removes access to its Tasks. Hidden conversations and
Tasks are omitted from lists and normally return `404`; a visible Task that you cannot cancel or
continue returns `403`. See [permission troubleshooting](./troubleshooting.md#conversation-or-task-access-is-denied).

The SDK runtime's own chat and A2A routes additionally apply the
[session ownership check](#who-can-talk-to-an-agents-session). An Agent editor's permission to
request cancellation through the platform does not let that editor address another person's
session directly through the runtime.

## What an Agent can reach on its own

The Agent's workload starts with no access. It reaches only what it created, what its workflow
declaration grants, and what a person grants it by hand, and only inside its Environment.

Two rules cap every grant:

- **A grant never exceeds the granter's own access.** The Agent's managers, the people with edit on
  the Agent or its branch and Organization admins, can give it access only up to their own level.
  If Alice manages an Agent, she can give it what Alice can see, never more.
- **A push grants with the authority of the person behind it.** The `access` section of a workflow
  file is applied when the branch is pushed, with the pusher's own access. An entry the pusher
  cannot grant stops the resource: nothing is created, changed or deployed
  (`declared_access_not_granted`). A push with no person behind it, such as one from a GitHub
  account that is not linked to a member, grants only the branch, what is declared on it, and what
  its workloads created.

Everyone who can use the Agent benefits from its grants. Give an Agent that serves many people only
access that all of them may have.

## Acting for the person (`acts_for_requester`)

An Agent can be enabled to act with the ordinary permissions of its requester. Every call the Agent
makes for the work, its Main Sequence MCP tools, its application tools and project code's calls
through `platform_client()`, carries the person's delegation while the turn serves a person, and
none otherwise. The platform operation or the application decides what each call may do. Project
tools identify the person with `current_requester()`. This authority is separate from the Agent's
own grants. See the [public API](./public-api.md#current_requester-and-platform_client).

- A delegated call uses the requester's ordinary permissions, including administrative ones when
  the requester is an Organization admin. Read access does not grant edit, run, sharing, or
  deletion rights.
- It stays in the Agent's Environment.
- It lasts at most 24 hours after the request, and only while the turn or Task serving it runs.
- Delegating to another Agent passes the person on only when the delegating work serves that
  person and the receiving Agent is enabled too. The platform records the person from its own
  records, never from anything an Agent states.

The platform checks the delegation again on every call. It ends at once when the person is
deactivated, leaves the Organization or loses access to the Agent, and when someone else continues
the Task. A turn with no person behind it, background work outside a turn, and local mode carry no
delegation: their calls are the Agent's own (in local mode, your own). A refused delegated call is
reported; the SDK never retries it as the Agent, and project code should not either. A Task
continued by a different person no longer serves a single person. Resuming work after a delegated
result does not restart the original request's 24-hour allowance.

### What each call may do

Each platform operation applies the person's ordinary permissions to a delegated call, exactly as
to the person's own request, and the Agent's own permissions to any other call. No list decides
which operations accept a delegation. An operation that needs a person returns its own error when
the call carries none. Applications decide the same way; see
[calling other applications](#calling-other-applications) and
[delegation troubleshooting](./troubleshooting.md#requester-access-is-refused).

### Only an Organization admin can turn it on

In a workflow using API version `2.3.0`, add it beside `key`, `kind`, `spec`, and `access` on the
Agent's resource (keep the existing `spec`):

```yaml
- key: analyst
  kind: harness_agent
  spec: { ... }
  acts_for_requester: true
```

The declaration takes effect only when the person behind the push is an Organization admin; any
other push fails the resource before it deploys with `declared_access_not_granted`. An Organization
admin can also turn it on after the Agent is created. The Agent's managers can turn it off, because that only narrows access.
Removing the line turns off what the declaration turned on. The runtime itself can never turn it
on.

### Why only an admin

Every grant is capped at the granter's own access, and a push grants with the pusher's authority
(see [what an Agent can reach on its own](#what-an-agent-can-reach-on-its-own)).
`acts_for_requester` is different: once it is on, the Agent acts with the access of whoever talks
to it, not of its managers.

Example:

1. Alice is a developer who manages the "Analyst" Agent.
2. Bob works in finance and can see payroll data that Alice cannot.
3. Bob asks the Analyst a question. With the switch on, the Analyst reads payroll as Bob.
4. Alice controls the Analyst's code. She could change it to store or forward what it reads for
   Bob.

If Alice could turn the switch on herself, she would gain indirect access to data she was never
granted. The switch lets code that the managers control read every requester's data, which no
manager holds, so no manager can grant it. Only an Organization admin, who answers for the whole
Organization's data, can accept that risk. The same trust decision covers writes and
administrative operations: Agent code can also change, share or delete data, and use administrative
permissions, as the requester.

### What people are told

People who use such an Agent are told:

> **This Agent works with your identity.** It can read, create, change, run,
> share or delete only what your permissions allow through supported operations,
> only while serving your request, and for at most 24 hours after you ask. It
> uses your ordinary permissions, including administrative permissions, and
> access is checked on every call. Your Organization's administrator approved it
> to work this way.

This statement describes delegated calls. The Agent's own grants, local human login, and trusted
extension code are separate sources of authority; enabling requester access does not remove
them. Prompt injection can cause unintended changes, sharing, or deletion within the
person's permitted operations. Completed changes can persist after access ends. Only an
Organization admin can accept that risk by enabling requester access.

## Secrets

- **Secret values follow ordinary permissions.** A hosted Agent can read a Secret value its
  workload was granted and, in a delegated call, a value the person may read. Anything the Agent
  reads can reach the model provider and the session history, so grant an Agent no Secret it does
  not need.
- **People enter Secret values themselves,** on the Secrets page in Command Center or with
  `mainsequence secrets create <NAME>`, which asks for the value without showing it. An Agent must
  never ask for a value in chat. Do not paste one into a conversation: everything in it reaches the
  model provider and the session history.
- **A workload that needs a value reads it when it runs.** A Job or FastAPI application declares
  the Secret's name under `access.secrets` in its workflow file, or a person grants the Secret to
  that workload. The declaration works when the person behind the push can view the Secret.
- **Whoever can change a workload's code can reach the Secrets granted to it.** An Agent that can
  edit a workload's repository can make that workload reveal those values in a log, an output or a
  response. Keep a Secret away from every workload an Agent can edit.

## What reaches the model

Treat file contents, command output, tool results, and application responses returned to the
Agent as information the model can see. Conversation history and chat streams may expose that
information to people who can read the session. Task outputs and deliberately persisted Task
Messages have their own visibility rules; `Task.history` is not a copy of every tool call or Tau
entry. See [A2A Task history](./public-api.md#a2a-task-history). Do not put credentials in result
details or rely on a hidden UI field to keep them outside the model or persisted conversation.

The SDK keeps its own credentials out of that path. The runtime credential, access tokens, lease
and session proofs, application tokens and caller assertions are never tool arguments or results,
never written to session history, and are redacted from logs. The platform operation that returns
runtime-access tokens is not offered to the model. Project tools must follow the same rule: return
business results, never tokens, response headers or proofs.

## What the Agent's code can reach

The SDK does not sandbox code that runs in the Agent's process:

- the `bash`, `read`, `write` and `edit` tools run with the runtime's own operating-system access
  and environment;
- project extensions run as Python in the same process; and
- the process holds the Agent's platform identity and the model-provider credentials the platform
  delivered to it.

Commands the model chooses and every extension can therefore act with the Agent's workload identity
and use those provider credentials, including the Secret values the workload may read.
`TAU_EXCLUDE_BASE_TOOLS` removes the coding tools from the model, but it does not sandbox extensions. Review extension code
as code that holds these credentials. See
[ownership and trust](../guides/project-configuration.md#ownership-and-trust).

## Local mode

Local mode signs in with your own Main Sequence login (`mainsequence login`) or a user token pair.
The Agent then acts as you: its tools and extensions can do anything you can do on the platform,
including reading the Secret values you can view. The hosted rules above, such as no access
beyond the Agent's grants, do not apply to your own login. Local mode refuses to start
in a process that carries the platform's hosting settings. See
[authenticated local development](./settings.md#authenticated-local-development).

## Calling other applications

`platform_client().call_release()` lets a project tool call another platform application. The
platform first checks that the caller can view that application in the Agent's Environment, the
person for a delegated call and the Agent's workload otherwise, then issues an access token for the
call. On every call the application receives a signed assertion that names the Agent as the caller
and, for a delegated call, the person as the requester.

- The application decides what each call may do. It should authorize against the verified person
  when the call carries one, and against the caller otherwise.
- Knowing the person does not let the application act as the person elsewhere: it cannot reuse the
  assertion against the platform or forward it.
- Every route of the application works this way, including an MCP endpoint it serves.
- Each declared application supplies `<name>__list_tools` and `<name>__call_tool`. They carry the
  person's delegation while the turn serves a person, and run as the Agent otherwise. Declaring an
  application grants no access: without a person, the Agent's workload needs its own grants on it.
- The SDK's Main Sequence MCP tools follow the same rule: every call names the turn's session, and
  carries the person's delegation while the turn serves a person.

## Checklist for project authors

- Give an Agent that serves people no grants of its own, or only what everyone who uses it may see.
- When the Agent must answer each person with their own data, ask an Organization admin to enable
  acting for the requester, and tell people what that means.
- Never ask for a Secret value in chat. Ask the person to create the Secret, and declare it under
  `access.secrets` on the workload that uses it.
- Keep Secrets away from workloads the Agent can edit.
- In an application that serves people, authorize against the verified requester when the call
  carries one, and against the caller otherwise.
- Do not retry a refused delegated call as the Agent.
- Return business results to the model, never tokens, proofs or raw responses.
- Review extensions: they run with the Agent's credentials.
