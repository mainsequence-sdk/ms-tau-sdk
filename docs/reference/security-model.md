# Agent Security Model

This page explains who an Agent built on this SDK acts as, what it can reach, and what the people
who build and use such an Agent need to know. The [public API](./public-api.md) and the
[runtime contract](./runtime-contract.md) define the exact behavior.

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

An Agent can be enabled to read with the access of its requester instead of its own. Project tools
then read who the person is with `current_requester()`, and read the platform or ask another
platform application for them with `requester_client()`. See the
[public API](./public-api.md#current_requester-and-requester_client).

This access is narrow:

- it is read-only, at the requester's member level, never with an Organization admin's powers, even
  when the requester is an admin;
- it reaches only the reads the platform opened for it, and never Secret values;
- it stays in the Agent's Environment;
- it lasts at most 24 hours after the request, and only while the turn or Task serving it runs;
- it is never passed on to another Agent or application.

The platform checks it again on every call. It ends at once when the person is deactivated, leaves
the Organization or loses access to the Agent, and when someone else continues the Task. A turn
with no person behind it, such as a Task sent by another Agent, background work or local mode, has
no requester, and `requester_client()` refuses.

### Only an Organization admin can turn it on

Declare it on the Agent's resource in the workflow file:

```yaml
- key: analyst
  kind: harness_agent
  spec: { ... }
  acts_for_requester: true
```

The declaration takes effect only when the person behind the push is an Organization admin; any
other push fails the resource before it deploys. An Organization admin can also turn it on after
the Agent is created. The Agent's managers can turn it off, because that only narrows access.
Removing the line turns off what the declaration turned on. The runtime itself can never turn it
on.

### Why only an admin

Every grant is capped at the granter's own access, and a push grants with the pusher's authority
(see [what an Agent can reach on its own](#what-an-agent-can-reach-on-its-own)).
`acts_for_requester` is different: once it is on, the Agent reads with the access of whoever talks
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
Organization's data, can accept that risk.

### What people are told

People who use such an Agent are told:

> **This Agent works with your identity, securely.** It reads only what you can already read, only
> to answer your own requests, and for at most 24 hours after you ask. It cannot act as anyone else,
> cannot change, share or delete anything, never sees your secret values, and stops the moment your
> access ends. Your Organization's administrator approved it to work this way.

The limit is plain: while it works on your request, the Agent's code can read what you can read.

## Secrets

- **A hosted Agent never reads or writes Secret values, even with a grant.** The platform refuses
  before it touches the secret store. The Agent may see the names of Secrets its workload was
  granted.
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

Everything an Agent reads with its tools enters the model's context: file contents, command output,
tool results, and answers from the platform and from other applications. It is also stored in the
session history, streamed to chat clients and kept in Task records. Tool result details are not
private either.

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
and use those provider credentials; they still cannot read Secret values. `TAU_EXCLUDE_BASE_TOOLS`
removes the coding tools from the model, but it does not sandbox extensions. Review extension code
as code that holds these credentials. See
[ownership and trust](../guides/project-configuration.md#ownership-and-trust).

## Local mode

Local mode signs in with your own Main Sequence login (`mainsequence login`) or a user token pair.
The Agent then acts as you: its tools and extensions can do anything you can do on the platform,
including reading the Secret values you can view. The hosted rules above, such as no Secret values
and no access beyond the Agent's grants, do not apply to your own login. Local mode refuses to start
in a process that carries the platform's hosting settings. See
[authenticated local development](./settings.md#authenticated-local-development).

## Calling other applications for the person

`requester_client().call_release()` lets a project tool call another platform application for its
requester. The platform first checks that the person can view that application in the Agent's
Environment, then issues an access token tied to the request. On every call the application
receives a signed assertion that names the Agent as the caller and the person as the requester.

- The application decides what to return. It should answer only with data the verified person may
  see.
- Knowing the person does not give the application the person's authority. It works with its own
  grants: acting for a person is never passed on, so the application cannot read as that person
  on the Agent's behalf.
- Every route of the application works this way, including an MCP endpoint it serves.
- The SDK's Main Sequence MCP tools are different: they run as the Agent's workload, never as the
  person.

## Checklist for project authors

- Give an Agent that serves people no grants of its own, or only what everyone who uses it may see.
- When the Agent must answer each person with their own data, ask an Organization admin to enable
  acting for the requester, and tell people what that means.
- Never ask for a Secret value in chat. Ask the person to create the Secret, and declare it under
  `access.secrets` on the workload that uses it.
- Keep Secrets away from workloads the Agent can edit.
- In an application that serves people, check the verified requester before returning their data.
- Return business results to the model, never tokens, proofs or raw responses.
- Review extensions: they run with the Agent's credentials.
