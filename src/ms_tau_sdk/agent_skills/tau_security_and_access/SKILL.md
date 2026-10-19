---
name: tau-security-and-access
description: Build and review project tools that call the platform or other applications for the person a turn serves, including tools that use a person's Secret without exposing it, and explain how delegation and policies decide what those tools can reach.
---

# TAU Security and Access

Use this skill when a project tool under `.tau/extensions/` calls the platform or another
platform application, uses a Secret, or must answer each person with their own data. Extension
mechanics are in `tau_project_customization`. The full model, with the administrator setup, is in
the SDK's Security and access guide (`docs/reference/security-model.md`). Managing policies is in
`.agents/skills/mainsequence/platform_operations/access_control_and_sharing/SKILL.md`.

## Who a call acts as

Every call a tool makes through `platform_client()` follows one rule:

| The call carries | Permissions used |
| --- | --- |
| No delegation | The caller's own: the Agent's workload grants, or in local mode your own login. |
| A valid delegation | The person the turn serves, with their ordinary permissions, including administrative ones. |
| A delegation that ended or is invalid | None. The call is refused with a `PermissionError` and is never retried as the Agent. |

- The platform names the person when the turn starts, and `current_requester()` returns them. It
  returns `None` when the Agent is not enabled to act for people (only an Organization admin can
  enable it, with `acts_for_requester`), for work no person asked for, in local mode and outside a
  turn.
- The operation or application that receives the call decides what it may do. No list says which
  operations accept a delegation. On the platform, each operation needs its own action: reading an
  object never lets the call change, run or delete it, or decide who has access.
- The call stays the Agent's call, made for the person. It is not impersonation: the platform
  checks it on every call, only while the turn or Task serving the person runs, for at most 24
  hours after their request, and only in the Agent's Environment.

## Choose the delegation for each tool

| The tool | `delegation` |
| --- | --- |
| Answers each person with their own data, or uses their credentials | `"required"` |
| Works for anyone, for the person when the turn serves one | `"auto"` (the default) |
| Uses only the Agent's own resources, the same for everyone | `"none"` |

With `"required"`, a turn that serves nobody raises a `PermissionError` before anything is sent.
With `"auto"`, the same tool reaches different things for different people, and the Agent's own
grants when nobody is served: make sure both are what you intend. Local turns serve nobody, so a
`"required"` tool refuses in local mode; test it with fakes (see [Testing](#testing)).

## Example: a tool that uses a person's own Secret

A person stores an API key for an outside data provider, and the Agent uses it for them without
ever seeing it:

1. The person creates the Secret themselves. The Agent never asks for the value or accepts it.
2. The tool that calls the provider reads the Secret as the person, inside that same call.
3. The tool uses the value and returns only the result.

```python
"""Quotes from an outside provider, with each person's own API key."""

import json

import httpx
from tau_agent.messages import TextContent
from tau_agent.tools import AgentTool, AgentToolResult

from ms_tau_sdk import current_requester, platform_client

PROVIDER_URL = "https://quotes.example.test/v1/latest"


def _text(message):
    return AgentToolResult(content=[TextContent(text=message)])


def _secret_name():
    # The name comes from the person the platform named, never from the model.
    person = current_requester()
    return None if person is None else f"QUOTES_API_KEY__{person.uid}"


async def _secret_uid(client, name):
    # Listing returns names and UIDs, never values. Names are unique in the Agent's Environment.
    answer = await client.request("GET", "/api/v1/secrets/", params={"name": name})
    if answer.status_code != 200:
        return None
    found = answer.json()["results"]
    return found[0]["uid"] if found else None


async def connect_quotes(tool_call_id, arguments, signal=None, on_update=None):
    name = _secret_name()
    if name is None:
        return _text("I can set up your key only while answering your own request.")
    try:
        uid = await _secret_uid(platform_client(delegation="required"), name)
    except PermissionError:
        return _text("Your access for this request ended. Please ask again.")
    if uid is not None:
        return _text(f"Your key is stored as the Secret {name}.")
    return _text(
        f"Create a Secret named {name} with your API key, in the Environment this Agent runs in: "
        f"on the Secrets page in Command Center, or with `mainsequence secrets create {name}`, "
        "which asks for the value without showing it. Never paste the key into this chat."
    )


async def latest_quote(tool_call_id, arguments, signal=None, on_update=None):
    name = _secret_name()
    if name is None:
        return _text("I can use your key only while answering your own request.")
    client = platform_client(delegation="required")
    try:
        uid = await _secret_uid(client, name)
        if uid is None:
            return _text(f"First create the Secret {name}. Ask me how.")
        secret = await client.request("GET", f"/api/v1/secrets/{uid}/")
    except PermissionError:
        return _text("Your access for this request ended. Please ask again.")
    if secret.status_code != 200:
        return _text("I could not read your key.")
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            quote = await http.get(
                PROVIDER_URL,
                params={"symbol": arguments["symbol"]},
                headers={"Authorization": f"Bearer {secret.json()['value']}"},
            )
    except httpx.HTTPError:
        # The error can carry the request with its headers: never show it.
        return _text("The quotes provider did not answer.")
    if quote.status_code != 200:
        return _text(f"The quotes provider answered {quote.status_code}.")
    data = quote.json()
    return _text(json.dumps({"symbol": data["symbol"], "price": data["price"]}))


def setup(tau):
    tau.register_tool(
        AgentTool(
            name="connect_quotes",
            label="Connect Quotes",
            description="Check or explain how to store your quotes API key as your own Secret.",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            execute_fn=connect_quotes,
        )
    )
    tau.register_tool(
        AgentTool(
            name="latest_quote",
            label="Latest Quote",
            description="Get the latest quote for a symbol with your own quotes API key.",
            parameters={
                "type": "object",
                "properties": {"symbol": {"type": "string"}},
                "required": ["symbol"],
                "additionalProperties": False,
            },
            execute_fn=latest_quote,
        )
    )
```

What makes it safe:

- No tool takes a Secret value or a Secret name from the model. The name is built from
  `current_requester().uid`, so prompt injection cannot point the tool at another Secret.
- `delegation="required"` reads the Secret as the person. The platform returns the value only if
  the person holds `value_reader`, `editor` or `owner` on it; the name is a lookup key, not the
  protection.
- The value exists only inside one call. It never goes into the result, `details`, a log, an
  exception message, a file or a cache for the next turn. It travels in a header, never in a URL,
  because URLs reach logs. Errors from the outside call are caught, and the provider's raw body is
  never returned.
- There is no tool that returns a Secret value, and no tool that calls any platform path the model
  chooses. With the delegation, such a tool would let the model read every Secret the person can.
- The Agent's own workload holds no grant on these Secrets, so a call without the person reaches
  none of them.

## Policies decide what a delegated call can reach

A delegated call reaches what the person's policies allow in the Agent's Environment: the policies
they hold directly, such as `owner` on what they created, and those given to a team they belong to.
This holds for Secrets and every other object the platform decides by policies. A team's members
receive the policies given to it, and `in_teams()` answers membership. Who can see and manage a
team is decided separately, by the `viewer`, `membership_manager`, `editor` and `owner` policies on
it: any person can create a team and becomes its owner and first member, members can see the team
and leave it, and Organization admins manage every team from their role, without being members.
See the SDK Security and access guide's **Teams and membership** section. The platform enforces
the policies; which of those objects a tool uses is part of the tool's design. For Secrets, the
naming convention states that design:

- **A personal credential:** a name tied to the person, such as `QUOTES_API_KEY__<person uid>`.
  The person owns it and decides whether anyone else gets a policy on it.
- **A team credential:** a name the team agrees on, with `value_reader` attached to the team. The
  tool reads it for any member.
- **Someone else's Secret whose value the person may read:** valid whenever the tool is meant to
  use it.

Whoever holds `editor` or `owner` on a Secret sets its value, so the tool uses whatever value they
set. Giving someone either policy on a Secret, or using a Secret whose editors or owners are other
people, means trusting them with what the tool does with it. That is a decision people make when
they give policies, not one the platform makes for them.

How Secret policies work:

- Policies on a Secret decide access: `reader` sees that it exists and its name, never its value;
  `value_reader` also reads the current value; `editor` also reads earlier values, sets a new value,
  deletes it and sees who has access; `owner` also decides who has access. The creator becomes its
  owner, and Organization admins hold every policy on their Organization's Secrets from their role.
- Owners attach and detach these policies for people, teams and workloads; whoever manages a
  workload can also give it a policy they hold, and anyone can remove their own. Nobody gives a
  policy with more than they hold, and people other than Organization admins give access only to
  people who share an active team with them, to teams they belong to, and to workloads they manage
  or whose Job or release they can see (`jobs.read` or `resource-releases.read`).
- `GET /api/v1/secrets/<uid>/access/` lists who holds which policy, for editors and owners, and
  `GET /api/v1/secrets/<uid>/access/me/` answers what the caller may do with it. A tool can use
  them when its design needs to, for example to refuse a team key where it expects a personal one.
  That is a choice of the tool, not a platform rule.

## Model providers

The model a session runs on uses a provider credential or custom endpoint that the session owner
may use; a delegated child session uses its parent's owner. A policy that only the Agent's workload
holds does not count in a person's session. Policies on the record decide who may use it, held
directly or through a team:

- `user` uses it: a credential is selected and delivered to sessions and workloads, including local
  Tau, and a custom endpoint receives their requests.
- `editor` also refreshes, reconnects or revokes a credential, or changes or deletes an endpoint,
  and sees who has access; `owner` also decides who has access.
- The credential's person and the endpoint's creator hold `owner`, and Organization admins hold
  every action on their Organization's records. Owners give and remove the policies in Command
  Center's provider settings or through `/api/v1/model-provider-credentials/<uid>/access/` and
  `/api/v1/custom-model-providers/<uid>/access/`; a refused change answers `403` `missing_action`
  with the `granting_policies` that would allow it.

Holding `user` delivers the credential, or the endpoint's stored authentication, to the holder's own
runtime, including local Tau, where it can be copied; removing the policy stops later deliveries,
not copies already made. To let a team run an Agent on one credential, its owner gives the team
`user`.

## Agents

Policies on an Agent decide who may talk to it and manage it, held directly or through a team:

- `user` talks to it and gives it Tasks, seeing only the Tasks they asked for; `viewer` also sees
  every Task of the Agent with its requests and results; `operator` also stops any Task or
  conversation run, other people's included; `editor` also configures it, moves it to a new runtime
  build, deletes it, manages what its workload can reach and sees who has access; `owner` also
  decides who has access.
- A conversation stays its owner's: no Agent policy, `owner` included, reads another person's
  conversation (Organization admins can read it, never change it), and only a Task's requesters
  continue it. On an Agent that acts for its requester, a Task is visible only to its requesters
  and Organization admins.
- Whoever creates an Agent holds `owner`, and Organization admins hold every action on their
  Organization's Agents. Owners give and remove the policies through
  `/api/v1/agents/<uid>/access/`; a refused change answers `403` `missing_action` with the
  `granting_policies` that would allow it, and an Agent the caller holds no policy on answers `404`.
- The release an Agent runs on takes no policy of its own: `viewer` and `operator` on the Agent see
  it, and `editor` and `owner` change it.

A tool reaches Agents and their Tasks with the caller's policies: the person's in a delegated call,
the Agent's workload's otherwise. To send another Agent work without a delegation, the workload
needs its own policy on that Agent, declared under `access.agents` in its workflow file, usually
`policy: user`. Calling another application with `call_release()` needs a policy on its release
that includes calling it (`resource-releases.invoke`, which every release policy has), and the
platform checks it again on every call.

## Checking the person's teams

A delegated call already reaches exactly what the person can, including what the policies given to
their teams allow, so a tool rarely checks teams itself. When the tool's own policy names teams,
ask the platform with `in_teams()`:

```python
person = current_requester()
if person is None or REPORTS_TEAM_UID not in await person.in_teams([REPORTS_TEAM_UID]):
    return AgentToolResult(content=[TextContent(text="These reports are for the team's members.")])
```

- `await in_teams(team_uids, *, max_age_seconds=None)` returns the subset of `team_uids` the
  person is an active member of, as a `frozenset`. Unknown and inactive teams are left out, and so
  is a team the person only manages: managing a team, also as an Organization admin, does not make
  anyone a member.
- On the turn's requester it answers only while that person is the verified caller of the request
  the turn runs in, such as a chat or A2A Message they sent. A turn with no such request raises
  `TauSDKError`: a Task attempt the platform dispatches, a turn that resumes a caller delivery, or
  work another Agent asked for. Refuse the action then; never read the error as a membership
  answer. An HTTP handler asks for its caller with `await request.state.user.in_teams(...)`.
- The process reuses an answer across requests for `TAU_TEAM_MEMBERSHIP_CACHE_SECONDS` (default
  `0`: only within its request); set it in `harness_agent.spec.env_vars`. `max_age_seconds=0`
  always asks the platform: use it before a write that must see a removal at once.
- A check the platform cannot answer raises `BackendError`; it never answers "not a member".
- The requester carries no team list; `in_teams()` is the only membership check.

## What never reaches the model

Tool results, their `details`, and errors can reach the model provider and the session history.
Never return a Secret value, a token, a proof, an assertion, a response object, its headers or a
raw error body. Return business results: rows, numbers, names, and plain messages. The SDK already
keeps its own credentials out of that path and never lets a tool set `Authorization` or an
`X-MainSequence-*` header.

## Limits

- This works in the Agent's own project tools. An application that receives a delegated call
  learns the person with `User.get_requester()`, but it cannot call the platform as that person.
- The delegation ends with the turn. A task the tool leaves running acts as the Agent afterwards,
  and nothing read for one person may be kept for another turn or another person.
- When the person's access ends (the turn is over, the access was removed, 24 hours passed, or
  the Agent is not enabled), calls raise a `PermissionError` whose `code` is
  `requester_binding_invalid` or starts with `runtime_lease_`. Say in plain words that the request
  cannot be served, and never retry it as the Agent.
- Every other answer, such as `403` or `404` for an object the person cannot see, is returned as
  it is: check the status before using the body.

## Testing

Patch the extension module's `current_requester` and `platform_client` with fakes, and test:

- with a person and the Secret: the result holds only business fields, and the value appears in no
  result text, `details`, log record or exception;
- with a person and no Secret: the tool explains how to create it and sends no provider call;
- with nobody: the tool refuses and sends nothing;
- a `PermissionError` from the client: a plain message, and no second call;
- a failing or erroring provider: a plain message without the provider's body or the request.

## Review checklist

- Each tool has a deliberate `delegation`: `"required"` for a person's own data or credentials.
- No person's UID, Secret name or Secret value comes from tool arguments, the prompt or history.
- No tool returns a credential or calls a platform path the model chooses.
- Secret values stay inside one call, in headers, never in results, logs, errors, URLs or caches.
- The naming convention says whose Secrets the tool uses, and the owners of those Secrets know it.
- A refused delegated call is reported, never retried as the Agent.
- The Agent's workload holds no grants it does not need, and none on people's Secrets; `user` is
  enough on an Agent it only sends work to.
