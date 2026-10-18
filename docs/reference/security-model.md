# Security and access guide

This page explains who an Agent built on this SDK acts as, what it can reach, and what the people
who build and use such an Agent need to know. The [public API](./public-api.md) and the
[runtime contract](./runtime-contract.md) define the exact behavior. These are platform and SDK
access rules, not a guarantee that arbitrary project code is isolated from the runtime's credentials.

## Availability

This guide covers the current SDK development documentation. The application MCP connections in
[ADR 0021](../adrs/0021-mcp-connections-on-the-persons-identity.md) first shipped in
[2.0.6](../../CHANGELOG.md#206--2026-10-07). The delegation envelope shipped in
[2.0.7](../../CHANGELOG.md#207--2026-10-08), in one cutover with the matching platform change:
2.0.7 and later SDK releases work only with that platform, and earlier SDK releases no longer act
for people. Check your installed SDK before relying on these features.

## Who is who

| Term | Meaning |
| --- | --- |
| Person | A Main Sequence user. |
| Session owner | The person a conversation (AgentSession) belongs to. |
| The Agent's workload | The identity a deployed Agent runs as. Each Agent has its own. It is never the person who pushed or deployed the Agent, never an Organization admin, and it starts with no access. |
| Requester | The person whose own request a turn is serving. Always a person, never an Agent or another workload. |

## Agent policies

- **Policies on each Agent decide who may use and manage it,** held directly or through a Team:
  `user` (talk to it and give it Tasks, seeing only the Tasks you asked for), `viewer` (also see
  every Task of the Agent with its requests and results, and read the Agent's log lines that
  belong to you), `operator` (also stop any Task or conversation run of the Agent, other people's
  included), `editor` (also configure it, move it to a new runtime build, delete it, manage what
  its workload can reach and see who has access) and `owner` (also decide who has access). `user`
  is the right policy for most people. No policy, `owner` included, opens another person's
  conversation, and none turns on
  [acting for the person](#acting-for-the-person-acts_for_requester), which stays with
  Organization admins.
- **Who holds them.** Whoever creates an Agent holds `owner`. A repository workflow creates an Agent
  with no owner; Organization admins hold every action on their Organization's Agents and give the
  first policies. Existing shares are kept: a share for view became `viewer` and a share for edit
  `editor`, including a share that was given on the Agent's release.
- **Owners give and remove policies** for people, Teams and workloads through
  `/api/v1/agents/<uid>/access/`; the view and edit sharing routes are gone. Anyone can remove their
  own policy, `access/me/` answers what you may do with an Agent, and editors and owners see who
  holds which policy.
- **A workload needs its own policy to send an Agent work.** Another Agent or application that sends
  it messages or Tasks without a person's delegation needs a policy on it: its workflow file names
  the Agent under `access.agents`, usually with `policy: user`, or a person who manages that
  workload gives it one. A delegated call uses the person's own policy instead. An Agent's own
  runtime needs no policy on its own Agent to receive and record the messages and Tasks it is sent.
- **The Agent's release follows the Agent.** The release an Agent runs on takes no policy of its
  own: `viewer` and `operator` on the Agent see the release, and `editor` and `owner` change it.
  Access to its repository or branch does not reach it, and a policy given on the release itself
  answers `400` `sharing_managed_by_owner`: give it on the Agent instead.
- **Two changes need a second policy.** A subagent binding is part of its parent Agent's
  configuration: creating, changing or deleting one needs `editor` or `owner` on the parent, and
  creating or changing one also needs a policy on the child Agent it names. Moving an Agent to a
  new runtime build needs `editor` or `owner` on the Agent and on its branch.
- **A refusal names the policies to ask for.** A change your policies do not allow answers `403`
  `missing_action` with the `required_action`, for example `agents.update` when you hold only
  `operator`, and the `granting_policies` that include it: ask an owner for one of them. An Agent
  you hold no policy on answers `404`.

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

Every [Agent policy](#agent-policies) lets its holder open their own conversations with the Agent,
give it Tasks and send it messages; it does not open anyone else's conversation. The following
rules describe platform API and Command Center access in the selected Environment. Platform
operators with superuser access have additional operational access.

| Operation | Who may perform it |
| --- | --- |
| Open a conversation, give the Agent a Task in it, or send it a message | Anyone who holds a policy on the Agent, in a conversation they own. |
| Read a conversation, its history, logs, or artifacts | Its owner or an Organization admin. No Agent policy, `owner` included, gives access to another person's conversation. |
| Update, archive, delete, or change a conversation's configuration | Its owner. An Organization admin's oversight reads do not confer ordinary mutation rights. |
| Request that a conversation stop | Its owner, or a holder of `operator`, `editor` or `owner` on the Agent. Organization admins can do so on their Organization's Agents. |
| Read an ordinary Agent's Tasks and outputs | Holders of `viewer`, `operator`, `editor` or `owner` on the Agent. A holder of `user` reads only the Tasks they asked for. |
| Read Tasks and outputs of an Agent enabled to act for requesters | A requester of that Task or an Organization admin. No Agent policy is sufficient on its own. |
| Cancel a visible Task | A requester of the Task, or a holder of `operator`, `editor` or `owner` on the Agent. |
| Continue a visible Task awaiting input or authorization | A requester of the Task. There is no Organization-admin override for continuation. |

A Task requester includes the person who created it or a verified person who requested its creation
or a continuation. Losing every policy on the Agent removes access to its Tasks and to your own
conversations with it. Hidden conversations and Tasks are omitted from lists and normally return
`404`; a visible Task that you cannot cancel or continue returns `403`. See
[permission troubleshooting](./troubleshooting.md#conversation-or-task-access-is-denied).

The SDK runtime's own chat and A2A routes additionally apply the
[session ownership check](#who-can-talk-to-an-agents-session). Permission to stop other people's
work through the platform does not let its holder address another person's session directly
through the runtime.

## What an Agent can reach on its own

The Agent's workload starts with no access. It reaches only what it created, what its workflow
declaration grants, and what a person grants it by hand, and only inside its Environment.

Two rules cap every grant:

- **A grant never exceeds the granter's own access.** The Agent's managers, the people who hold
  `editor` or `owner` on the Agent and Organization admins, can give its workload only a policy
  whose actions they hold themselves on that object. If Alice manages an Agent, she can give it
  what Alice may do, never more.
- **A push grants with the authority of the person behind it.** The `access` section of a workflow
  file is applied when the branch is pushed, with the pusher's own access. An entry the pusher
  cannot grant stops the resource: nothing is created, changed or deployed
  (`declared_access_not_granted`). A push with no person behind it, such as one from a GitHub
  account that is not linked to a member, grants only the branch, what is declared on it, and what
  its workloads created.

Everyone who holds a policy on the Agent benefits from its grants. Give an Agent that serves many
people only access that all of them may have.

## Teams and membership

A Team groups people and workloads of an Organization. Who is in a Team and who manages it are
decided separately:

- **Membership** decides who receives the policies given to the Team, and what `in_teams()`
  answers for a caller. A workload can be a member: an Agent whose workload is in a Team receives
  the policies given to that Team.
- **Policies on the Team** decide who can see and manage it: `viewer` (see the Team and its
  members), `membership_manager` (add and remove members), `editor` (rename it, change its
  description, activate or deactivate it) and `owner` (everything, including deleting it and
  deciding who manages it). Every member can see the Team and its members, and leave it.

Any person can create a Team and becomes its owner and first member. Organization admins manage
every Team from their role, without being members. No membership is protected: an admin can be
removed from a Team like anyone else, and losing the admin role ends that authority at once.

Managing a Team does not make anyone a member, so `in_teams()` does not say who manages it: an
owner, a membership manager or an Organization admin who is not a member is not in its answer. The
built-in full-directory Team stays computed and read-only, and `in_teams()` leaves it out.

This is platform behavior: it adds no SDK method, `in_teams()` is unchanged, and existing
memberships are kept. When access through a Team is missing, ask someone who manages the Team to
check that it is active and lists the person or workload. Platform operators retain their separate
operational access.

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
  person, the receiving Agent is enabled too, and the person holds a policy on it. The platform
  records the person from its own records, never from anything an Agent states.

The platform checks the delegation again on every call. It ends at once when the person is
deactivated, leaves the Organization or no longer holds a policy on the Agent, or on an Agent that
passed the work on, and when someone else continues the Task. A turn with no person behind it,
background work outside a turn, and local mode carry no delegation: their calls are the Agent's own
(in local mode, your own). A refused delegated call is reported; the SDK never retries it as the
Agent, and project code should not either. A Task continued by a different person no longer serves
a single person. Resuming work after a delegated result does not restart the original request's
24-hour allowance.

### What each call may do

Each platform operation applies the person's ordinary permissions to a delegated call, exactly as
to the person's own request, and the Agent's own permissions to any other call. No list decides
which operations accept a delegation. An operation that needs a person returns its own error when
the call carries none. Applications decide the same way; see
[calling other applications](#calling-other-applications) and
[delegation troubleshooting](./troubleshooting.md#requester-access-is-refused).

### Only an Organization admin can turn it on

In a workflow file (API version `3.0.0`), add it beside `key`, `kind`, `spec`, and `access` on the
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

- **Policies on a Secret decide who can see it and who can read its value:** `reader` (see that
  the Secret exists and refer to it by name, never its value), `value_reader` (also read the
  current value), `editor` (also read earlier values, set a new value, delete the Secret and see
  who has access) and `owner` (also decide who has access). Whoever creates a Secret becomes its
  owner, and Organization admins hold every policy on their Organization's Secrets from their
  role. Owners give and remove policies through `/api/v1/secrets/<uid>/access/`, and anyone can
  remove their own; no policy is protected, so an owner can remove the creator's. Existing shares
  are kept: a share for view became `value_reader`, a share for edit `editor`.
- **Reading a value needs a policy that includes it.** A hosted Agent reads a value its workload
  holds `value_reader`, `editor` or `owner` on and, in a delegated call, a value the person may
  read. With only `reader`, `GET /api/v1/secrets/<uid>/` answers `403` with `required_action`
  `secrets.read_value`, and an earlier value (`?version=`) needs `secrets.read_value_history`.
  Anything the Agent reads can reach the model provider and the session history, so give an
  Agent's workload no Secret it does not need.
- **People enter Secret values themselves,** on the Secrets page in Command Center or with
  `mainsequence secrets create <NAME>`, which asks for the value without showing it. An Agent must
  never ask for a value in chat. Do not paste one into a conversation: everything in it reaches the
  model provider and the session history.
- **A workload that needs a value reads it when it runs.** A Job or FastAPI application declares
  the Secret's name under `access.secrets` in its workflow file, with the policy its workload gets:
  `policy: value_reader` to read the current value (`reader` gives no value). A Secret's owner can
  also attach a policy to the workload, and so can whoever manages the workload, for a policy they
  hold. The declaration works when the person behind the push can do everything that policy allows
  on the Secret.
- **Whoever can change a workload's code can reach the Secrets granted to it.** An Agent that can
  edit a workload's repository can make that workload reveal those values in a log, an output or a
  response. Keep a Secret away from every workload an Agent can edit.

## Model providers

- **Policies on each credential and custom endpoint decide who may use and manage it:** `user`
  (use it in sessions and workloads, including local Tau), `editor` (also refresh, reconnect or
  revoke a credential, or change or delete an endpoint, and see who has access) and `owner` (also
  decide who has access). The credential's person and the endpoint's creator hold `owner`, and
  Organization admins hold every action on their Organization's records.
  [Named providers and sharing](../guides/project-configuration.md#named-providers-and-sharing)
  explains selection and how policies are given.
- **A session uses what its owner may use.** The platform checks the credential or endpoint a
  session uses against the session owner, never the Agent's workload, and for a delegated child
  session against its parent's owner. A policy given only to an Agent's workload does not reach
  people's sessions.
- **`user` delivers the credential.** Its holder receives the credential, or the endpoint's stored
  authentication, in their own runtime, including local Tau, and can copy it there. Removing the
  policy stops future delivery; it cannot recall what was already received. Give it only to people
  and workload operators you trust.
- **A refusal names the policies to ask for.** A change the caller's policies do not allow answers
  `403` `missing_action` with the `required_action`, for example
  `model-provider-credentials.revoke`, and the `granting_policies` that include it. A record the
  caller cannot see answers `404`.

## What reaches the model

Treat file contents, command output, tool results, and application responses returned to the
Agent as information the model can see. Conversation history and chat streams may expose that
information to people who can read the session. Task outputs and deliberately persisted Task
Messages have their own visibility rules; `Task.history` is not a copy of every tool call or Tau
entry. See [Task history](./runtime-contract.md#task-conversation-result-and-execution-correlation).
Do not put credentials in result details or rely on a hidden UI field to keep them outside the
model or persisted conversation.

The SDK keeps its own credentials out of that path. The runtime credential, access tokens, lease
and session proofs, application tokens and caller assertions are never tool arguments or results,
never written to session history, and are redacted from logs. The platform operation that returns
runtime-access tokens is not offered to the model. Project tools must follow the same rule: return
business results, never tokens, response headers or proofs.

## What the Agent's code can reach

Hosted execution isolation depends on the deployment provider. AWS Auto Mode hosting uses
standard managed containers without gVisor or a separate userspace kernel. Do not
assume that custom code has a separate kernel boundary. Platform permissions,
restricted container settings and network restrictions still apply. This hosting
choice does not change the SDK API, Agent grants or session ownership rules.

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
including reading the Secret values you can read. For an Organization admin, that includes
receiving and using any member's configured model provider without a policy on it; see
[named providers and sharing](../guides/project-configuration.md#named-providers-and-sharing). The hosted rules above, such as no access
beyond the Agent's grants, do not apply to your own login. Local mode refuses to start
in a process that carries the platform's hosting settings. See
[authenticated local development](./settings.md#authenticated-local-development).

## Calling other applications

`platform_client().call_release()` lets a project tool call another platform application. The
platform first checks that the caller may call that application in the Agent's Environment, the
person for a delegated call and the Agent's workload otherwise: the caller needs a policy on the
application's release that includes calling it (`resource-releases.invoke`). Every release policy
does (`viewer`, `user`, `editor` and `owner`), held directly, through a Team, or passed down from
a policy on the branch that declares the release, such as `reader`. The platform then issues an
access token for the call. It repeats that check on every call, so a token issued earlier does not
keep access the caller has lost:

- when the person can no longer call the application, the next delegated call answers `403`;
- when the Agent's workload can no longer call it, the Agent's own calls answer `403` within at
  most 60 seconds.

On every call the application receives a signed assertion that names the Agent as the caller and,
for a delegated call, the person as the requester.

- The application decides what each call may do. It should authorize against the verified person
  when the call carries one, and against the caller otherwise.
- Knowing the person does not let the application act as the person elsewhere: it cannot reuse the
  assertion against the platform or forward it.
- Every route of the application works this way, including an MCP endpoint it serves.
- Each declared application supplies `<name>__list_tools`, `<name>__call_tool`,
  `<name>__list_resources` and `<name>__read_resource`. They carry the person's delegation while
  the turn serves a person, and run as the Agent otherwise. Declaring an application grants no
  access: without a person, the Agent's workload needs its own policy on the application's release.
- Skills an application publishes are instructions the application wrote, and the Agent reads
  them as the application's content, labelled with its name. They never become the project's
  skills and grant nothing: fields such as `allowed-tools` are ignored. Declaring the application
  is your decision to let the Agent read them, as it reads the application's answers. With the
  coding tools (`bash` among them), a model that follows a skill can run commands on the runtime;
  set `TAU_EXCLUDE_BASE_TOOLS` when that must not happen. A skill file is returned only when its
  bytes match the skill's list of files.
- The SDK's Main Sequence MCP tools follow the same rule: every call names the turn's session, and
  carries the person's delegation while the turn serves a person.

## Checklist for project authors

- Give an Agent that serves people no grants of its own, or only what everyone who uses it may see.
- Give each person the smallest Agent policy they need: `user` to talk to it, `viewer` only to read
  everyone's Tasks, `operator` only to stop other people's work.
- When the Agent must answer each person with their own data, ask an Organization admin to enable
  acting for the requester, and tell people what that means.
- Never ask for a Secret value in chat. Ask the person to create the Secret, and declare it under
  `access.secrets` on the workload that uses it.
- Keep Secrets away from workloads the Agent can edit.
- In an application that serves people, authorize against the verified requester when the call
  carries one, and against the caller otherwise.
- Do not retry a refused delegated call as the Agent.
- Return business results to the model, never tokens, proofs or raw responses.
- Declare only applications whose published skills you would let the Agent follow.
- Review extensions: they run with the Agent's credentials.
