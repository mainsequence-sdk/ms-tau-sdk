# ADR 0023: Skills and Resources of Declared Applications

Status: Accepted — implemented

Date: 2026-10-13

Issue: [#98](https://github.com/mainsequence-sdk/ms-tau-sdk/issues/98)

Amends:

- [ADR 0021: MCP connections on the person's identity](./0021-mcp-connections-on-the-persons-identity.md),
  section 4, by giving each declared application two more tools, for the skills and resources it
  publishes, and by opening an application's session on the newer MCP protocol revision when the
  application supports it.

## Context

An Agent reaches a declared application through `<name>__list_tools` and `<name>__call_tool`. An
application can also publish Agent Skills, instructions for using its tools, through the MCP Skills
extension, `io.modelcontextprotocol/skills`
([SEP-2640](https://modelcontextprotocol.io/seps/2640-skills-extension)). The mainsequence SDK
publishes a folder of skills this way (its ADR 0039). `skills/list` returns each skill: the URI of
its `SKILL.md`, its frontmatter, and the list of its files with each file's size and SHA-256
digest. Every file is an ordinary resource, read with `resources/read`.

In 2.0.13 the Agent could use none of it. Opening an application's session already read the
application's resource list, but neither tool showed it, and nothing could read a resource. Issue
#98 reproduced this with a deployed application that published five skills in eight files: the
Agent called the application's tools but could not discover or read any of its skills.

The extension is declared only on protocol revision `2026-07-28`, in the answer to
`server/discover`. The SDK opened every session with the `initialize` handshake, on `2025-11-25`,
whose capabilities have no field for extensions. So the declaration was invisible, and the
extension lets a client call `skills/list` only after it has seen the declaration.

A hosted Agent usually keeps the coding tools, `bash` among them. Text that reaches the model can
lead it to run commands, and a skill is text written to be followed.

## Decision

### 1. Two more tools for each application

- `<name>__list_resources` lists what the application publishes: each skill, with the URI of its
  `SKILL.md`, its name, its description and its files with their sizes; and every other resource
  the application advertises, with its URI, name, description and type. A skill's files appear
  once, under the skill.
- `<name>__read_resource` takes a `uri` and reads one of them.

`<name>__list_tools` says when the application also publishes skills or resources and names
`<name>__list_resources`. The two tools run inside the turn like the other two: each opens its own
session with the turn's token, opening is retried once (ADR 0022, section 4), failures read as in
ADR 0022, section 3, and nothing is shared between turns or people. Each request waits
`MAINSEQUENCE_TAU_BACKEND_READ_TIMEOUT_SECONDS`. The names follow MCP, where a skill's files are
resources.

### 2. Application sessions ask the newer revision first

An application's session opens with MCP's own `auto` policy, the MCP client's default: it asks
`server/discover` on `2026-07-28`, and an application that answers with its capabilities is used on
that revision. An application that answers with an error, an HTTP client error status (4xx) or
something that is not a discovery result, or does not answer within MCP's ten seconds for this
question, is opened with `initialize`, as before. An HTTP server error status (5xx) or a refused
token fails the opening as any other request does, and opening is retried once.

`skills/list` is asked only when the application declares the extension. Every page is read; a
repeated cursor or an entry without a name or description is a failure. The Main Sequence
platform's own MCP keeps the `initialize` handshake: it publishes no skills, and its connection is
opened once per process.

### 3. Only what the application lists is read, and a skill file must match its list

- A URI in the directory of a listed skill is read only when the skill's file list names it. The
  bytes the application returns must have the listed size and SHA-256 digest, or none of them
  reaches the model: the result says the content differs from the skill's list (`failure:
  unverified`, logged as `runtime.mcp_application.failed`).
- A skill whose content the application generates (`resources: "dynamic"`) lists no files and no
  digests. Only its `SKILL.md` is read, unchecked.
- Any other URI is read only when the application advertises it in its resource list. Without the
  extension, skill files are such ordinary resources, as the extension specifies.
- Text is returned as text. Binary content is not shown; the result names its type.

The list used for the check is the `skills/list` answer in the read's own session; nothing is kept
between calls. A skill outside the listing is readable only when advertised as a resource.

### 4. A published skill stays the application's content

Every read starts with its origin, for example "From the orders application:
skill://order-review/references/checklist.md, a file of its skill order-review." The SDK never adds a
published skill to the project's skills (`.tau/skills`, the system prompt, the skill-scoped
`read`), never applies a frontmatter field such as `allowed-tools`, and runs nothing a skill
contains.

Declaring an application in the workflow file is the Agent author's decision to let the Agent read
what the application publishes, as it already reads the answers of the application's tools. A
hosted runtime has no person to ask before each skill, so the extension's per-skill approval for
host-side code execution is not available: with the coding tools, a model that follows a skill can
run commands. `TAU_EXCLUDE_BASE_TOOLS` removes them. The Security and access guide says so.

## Rejected alternatives

- **Reading skill files as plain resources, on the `initialize` handshake.** No list of skills and
  no digests to check them against; #98 asks for both.
- **Asking `skills/list` on the `initialize` handshake.** The extension allows it only after the
  declaration is seen, and that handshake cannot carry the declaration.
- **Listing published skills in the system prompt, as project skills are.** Nothing may be read
  when the session loads, because no turn is running and there is no token; and they would look
  like the project's own skills.
- **A separate `<name>__list_skills`.** One more tool for every application; the one listing
  already names the skills as skills.
- **Returning binary files as base64 text.** Large, and of no use to the model.
- **Asking the platform's own MCP the newer question too.** Untested against the platform, which
  publishes no skills.

## Consequences

- Opening an application session starts with `server/discover`. An older application costs one more
  request, before `initialize`.
- With the extension declared, a listing or a read also asks `skills/list`.
- An application that changes a skill file after its listing is refused until the model lists again.
- What an application publishes reaches the model: the same exposure to prompt injection as its
  tools' answers, for text written as instructions.
- `skills/get`, `resources/directory/read`, resource templates and subscriptions are not used.

## Implementation and acceptance

1. Section 1. Tests: each application gets four tools; the listing shows each skill with its files
   and the other resources once; nothing published says so; `<name>__list_tools` names the listing
   when the application publishes; failures while opening, listing or reading read as in ADR 0022
   and carry no token.
2. Section 2. Tests, against an application on MCP 2.x over HTTP: the session uses `2026-07-28`,
   lists every page of skills and calls a tool; an older application, and one that answers the
   question with 400, 405 or 406, is opened with `initialize` and asked for no skills; a 502 on the
   question is retried once; a repeated cursor fails plainly.
3. Section 3. Tests: a matching skill file is returned; other bytes, another size, another URI, no
   content or two contents are refused; an unlisted file of a listed skill and an unadvertised URI
   are refused without a read; a generated skill's `SKILL.md` is read unchecked; a binary skill
   file is checked and not shown.
4. Section 4. Tests: every read names the application and, for a skill file, the skill.
5. In the same change, update the [runtime contract](../reference/runtime-contract.md), the
   [Security and access guide](../reference/security-model.md), the packaged project customization
   skill and the changelog.
