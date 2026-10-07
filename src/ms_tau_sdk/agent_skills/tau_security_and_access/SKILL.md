---
name: tau-security-and-access
description: Build and review project tools that call the platform or other applications for the person a turn serves, including tools that use a person's Secret without exposing it, and explain how delegation and sharing decide what those tools can reach.
---

# TAU Security and Access

Use this skill when a project tool under `.tau/extensions/` calls the platform or another
platform application, uses a Secret, or must answer each person with their own data. Extension
mechanics are in `tau_project_customization`. The full model, with the administrator setup, is in
the SDK's Security and access guide (`docs/reference/security-model.md`). Sharing itself is in
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
  operations accept a delegation. Read access never implies edit, run, share or delete.
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
- `delegation="required"` reads the Secret as the person. The platform returns it only if the
  person can view it; the name is a lookup key, not the protection.
- The value exists only inside one call. It never goes into the result, `details`, a log, an
  exception message, a file or a cache for the next turn. It travels in a header, never in a URL,
  because URLs reach logs. Errors from the outside call are caught, and the provider's raw body is
  never returned.
- There is no tool that returns a Secret value, and no tool that calls any platform path the model
  chooses. With the delegation, such a tool would let the model read every Secret the person can.
- The Agent's own workload holds no grant on these Secrets, so a call without the person reaches
  none of them.

## Sharing decides what a delegated call can reach

A delegated read returns anything the person can view in the Agent's Environment: what they
created and what others shared with them, directly or through a team. This holds for Secrets and
every other object. The platform enforces sharing; which of those objects a tool uses is part of
the tool's design. For Secrets, the naming convention states that design:

- **A personal credential:** a name tied to the person, such as `QUOTES_API_KEY__<person uid>`.
  The person decides whether to share it.
- **A team credential:** a name the team agrees on, shared with the team for view. The tool reads
  it for any member.
- **Someone else's Secret shared with the person:** valid whenever the tool is meant to use it.

Whoever can edit a Secret decides its value, so the tool uses whatever value they set. Granting or
accepting edit on a Secret means trusting that person with what the tool does with it. That is a
decision people make through sharing, not one the platform makes for them.

How sharing works:

- The creator of a Secret can always view and edit it, and cannot be removed from it. People who
  can edit a Secret can share it, for view or for edit, with people and teams.
- Most people can share only within the teams they belong to; Organization admins can share
  across their Organization.
- `GET /api/v1/secrets/<uid>/can-view/` and `can-edit/` list the people and teams with access. A
  tool can use them when its design needs to, for example to refuse a team key where it expects a
  personal one. That is a choice of the tool, not a platform rule.

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
- The naming convention says whose Secrets the tool uses, and the people sharing them know it.
- A refused delegated call is reported, never retried as the Agent.
- The Agent's workload holds no grants it does not need, and none on people's Secrets.
