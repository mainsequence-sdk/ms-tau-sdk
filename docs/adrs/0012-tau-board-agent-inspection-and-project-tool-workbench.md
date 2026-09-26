# ADR 0012: Tau Board Agent Inspection and Project Tool Workbench

Status: Accepted; implemented in 1.2.9

Date: 2026-09-25

Amends [ADR 0008](./0008-tau-board-local-development-companion.md) and
[ADR 0010](./0010-bundle-tau-board-in-sdk-distribution.md) by adding a local
Agent-inspection surface and an explicitly invoked project-tool workbench. It does
not change the separate-process boundary between Tau and Tau Board.

## Context

Tau Board currently makes local conversations, A2A Tasks, persisted state, and
structured logs easier to inspect. It does not present the effective Agent Card,
explain which executable tools an agent actually received, show which tools came
from the project, or let a developer inspect and exercise a project tool in
isolation.

This leaves an important development gap. A project author can register Python
tools under `.tau/extensions`, but must start a model turn or write an ad hoc
script to learn whether a tool loaded, whether its argument contract is usable,
and what it returns. The existing Connect view exposes only aggregate counts and
a catalog digest. The runtime already owns the effective tool objects and project
extension diagnostics, while the board intentionally does not import SDK
internals or load project extensions itself.

Several concepts must remain distinct in the UI and protocol:

- an Agent Card describes the A2A agent and its advertised skills;
- a Tau skill is instruction material discovered for the model;
- an executable `AgentTool` has a JSON Schema argument contract and an execution
  function;
- project extension source is implementation code and is not an Agent Card skill;
- Main Sequence MCP tools may operate on real platform resources even when Tau is
  in local mode.

Loading a Tau extension executes arbitrary project Python code. Executing a tool
can edit files, call the network, mutate platform resources, or perform any other
operation allowed to the Tau process. Consequently, neither inspection nor
schema-driven testing can be implemented as an unguarded filesystem scanner or
as an automatic background action.

## Decision

### 1. Add a first-class Agent view to Tau Board

Tau Board adds an **Agent** tab with four related views:

1. **Overview** shows the connected runtime, selected loaded session, effective
   Agent Card identity, capabilities, supported response kinds, catalog digest,
   and extension diagnostics.
2. **Agent Card** renders the effective served card in a readable layout and
   offers its bounded raw JSON. Name, description, version, URL, identifiers,
   capabilities, protocol extensions, and Agent Card skills are separate fields;
   executable tools are not presented as Agent Card skills.
3. **Tools** shows the effective executable catalog grouped by ownership:
   Tau coding tools, Main Sequence MCP tools, protocol Task controls, and project
   extension tools. Each entry displays its name, label, description, argument
   schema, execution mode, origin, and availability for testing.
4. **Extensions** shows each loaded project extension's name, origin, registered
   tools, entry source, and load diagnostics. It identifies the effective
   extension that supplied a tool without claiming that an entry file is always
   the exact function-definition file.

Project tools are visually prominent because they are the capabilities a
repository author can change. The UI still shows the other categories so the
developer can understand the complete catalog presented to the model. Excluded
base tools or MCP capabilities are shown as excluded only when the runtime has
authoritative evidence for that state; the board does not infer configuration
from absence alone.

The Agent tab operates on an **already loaded local session**. If no session is
loaded, the board reports that inspection is unavailable and links to the normal
Chat or A2A action that creates or resumes one. A read-only inspection request
must never create a session, hydrate a provider, connect MCP, import an extension,
or execute project code.

### 2. Expose a local runtime inspection contract

Tau owns the authoritative inspection data because it owns the loaded Agent Card,
effective `AgentTool` instances, extension runtime, workspace boundary, and
catalog digest. Tau Board remains a separate process and consumes an allowlisted
local HTTP contract; it does not import `ms_tau_sdk`, scan `.tau`, or reconstruct
the catalog from SQLite and logs.

Tau adds a versioned, local-mode-only contract with these conceptual operations:

```text
GET  /api/local/v1/sessions/{session_uid}/agent-inspection
GET  /api/local/v1/sessions/{session_uid}/extension-sources/{source_uid}
POST /api/local/v1/sessions/{session_uid}/tools/{tool_name}:validate
POST /api/local/v1/sessions/{session_uid}/tools/{tool_name}:test
POST /api/local/v1/sessions/{session_uid}/tool-tests/{test_uid}:cancel
```

The inspection response contains:

- the effective Agent Card and safe runtime/session identity;
- the effective catalog digest;
- a tool catalog with stable source categories and JSON Schema parameters;
- project extension metadata and safe diagnostics;
- opaque source identifiers for viewable project files;
- explicit availability and reason codes for source inspection and tool testing.

It contains no credentials, system prompt body, conversation body, MCP resource
contents, callable objects, environment values, or arbitrary filesystem paths.
Provider/model identity may be shown through the existing safe session selection
contract.

The runtime must retain explicit provenance while composing tools. At minimum,
each effective tool records its source category and, for project extensions, its
extension identity. Exact function file and line information is shown only when
Tau or the extension registration API supplies it explicitly. The implementation
must not use `inspect.getsource()` or stack heuristics as authoritative provenance:
decorators, wrappers, imports, generated callables, and factories make those
answers unreliable.

Every response is tied to a `catalog_digest`. Reloading extensions or rebuilding
a session can change the digest. The board discards stale detail views and forms
when the digest changes.

These routes exist only when `TAU_LOCAL_MODE=true`. Managed deployments do not
expose this development API. The API remains bound by Tau's existing local server
and identity rules; it is not a remote administration surface.

### 3. Limit source viewing to project-owned extension source

The board may display source only for files that Tau has explicitly attributed to
a loaded project extension. The first implementation guarantees the registered
extension entry file. A later registration contract may provide exact tool
implementation locations or an explicit extension file set.

The source endpoint accepts an opaque `source_uid`, never a browser-supplied path.
Tau resolves the registered file again, canonicalizes it, and verifies that it is
inside the canonical workspace and the owning extension boundary. It rejects
symlink escapes, missing or non-regular files, binary content, unsupported
encodings, and files over the documented size limit. Responses are bounded UTF-8
text with a display name and optional line metadata; raw absolute paths are not
required by the browser contract.

Core SDK tool source and Main Sequence MCP implementation source are not exposed.
The board does not become a generic repository file browser. Viewing source never
imports, reloads, or invokes the extension.

### 4. Generate tool-test inputs from the declared tool contract

For an `AgentTool`, the model-facing execution signature is its declared JSON
Schema `parameters`, not the uniform Python `execute(tool_call_id, arguments, ...)`
wrapper and not a best-effort `inspect.signature()` result. The workbench uses that
JSON Schema as the authoritative argument contract.

For a testable tool, the board generates a form for supported object-schema
properties, including required fields, strings, numbers, integers, booleans,
enums, arrays, nested objects, defaults, descriptions, and examples. A raw JSON
editor is always available as the fallback for valid schemas the form renderer
cannot fully represent. Defaults and examples may populate a draft but must never
trigger execution.

Before execution, the board:

1. validates JSON syntax and generated field types client-side for immediate feedback;
2. shows the exact canonical JSON arguments that will be sent;
3. offers a separate **Validate** action that performs no tool call; and
4. requires the developer to press **Run tool** and confirm that this executes
   real project code with the Tau process's filesystem, network, environment, and
   credential access.

The Tau runtime repeats schema validation authoritatively. A generated form is a
convenience, not a safety boundary, and a valid schema says nothing about a
tool's side effects. The `:validate` operation returns the canonical arguments
and, after the board has shown them with the execution warning, a short-lived
single-use confirmation value bound to the session, tool, catalog digest, and
canonical argument digest. It never calls the tool. The `:test` operation accepts
only that canonical payload and matching confirmation value.

The first implementation allows execution only when all of the following hold:

- Tau is in local mode;
- the selected session is already loaded and idle;
- the effective tool is owned by a loaded **project extension**;
- the tool has an object JSON Schema argument contract;
- the request supplies the current expected catalog digest; and
- the request carries a fresh explicit-confirmation value issued for the shown
  session, tool, digest, and canonical arguments.

Tau coding tools, Main Sequence MCP tools, and A2A Task-control tools are visible
but not runnable from the workbench. They are excluded because the purpose of
this surface is to develop repository-owned tools, not to provide a generic shell,
filesystem editor, platform console, or alternate Task protocol. “Python tool” in
this decision therefore means a project extension tool with declared Tau
provenance and schema, not merely any tool whose implementation happens to be
written in Python.

There is no inferred dry-run mode. If a project tool supports a dry-run option, it
must declare that option in its own schema and implement the behavior. Until the
tool contract gains explicit, enforceable side-effect metadata, every run requires
confirmation. The board does not auto-run, replay, schedule, or repeat a tool.
“Automatic testing” means automatic form generation, validation, invocation
plumbing, update capture, and result rendering after an explicit run request.

### 5. Execute the exact loaded tool without creating a model turn

The test endpoint looks up the exact effective `AgentTool` instance from the
selected loaded session. It does not rescan the repository, import a callable by
name, construct a shadow tool registry, or ask a model to choose or call the tool.
The expected catalog digest prevents a form built for an older implementation
from invoking a newly reloaded tool under the same name.

Tool tests use the same per-session execution exclusion as a normal agent turn.
An active turn, A2A Task attempt, extension reload, model change, session eviction,
or another test causes a deterministic conflict rather than a concurrent call.
Each accepted invocation receives a unique test ID and tool-call ID, a bounded
timeout, cancellation support, and a bounded update stream.

The dedicated runtime test executor applies the same argument preparation and
extension lifecycle semantics as normal tool execution where those semantics are
part of the effective tool contract. It emits the normal structured start,
update, completion, failure, timeout, and cancellation telemetry with a
`source=tool_workbench` marker. It does **not** call the model or append user,
assistant, or tool messages to durable conversation history. The test result is
not converted into an A2A Task, artifact, or platform AgentSession record.

The runtime cannot promise that a project tool or its extension hooks leave no
state behind. They execute as real code and may mutate files, local databases,
in-memory extension state, external systems, or platform resources reachable with
the process's credentials. The UI must state this immediately beside the Run
action. The workbench is an isolated conversation test, not an operating-system
sandbox or transaction rollback facility.

The response and update stream use Tau's normal tool content-block representation
and safe error taxonomy. The board renders bounded text, structured data, and
supported media; it truncates oversized output and clearly marks truncation.
Actual result bodies remain ephemeral in the page. They are not written to board
configuration, browser persistent storage, local session entries, or structured
SDK logs. Operational telemetry records identifiers, timing, status, and safe
error classifications, not arguments, result bodies, credential values, or raw
exception strings.

### 6. Keep the board proxy narrow and the mutation explicit

Tau Board allowlists only the new inspection, registered-source, validation/test,
and cancellation operations. It still rejects arbitrary Tau paths and never
forwards browser-supplied authorization or caller-identity headers. The board
accepts the feature only from a Tau endpoint whose health response proves local
mode.

The test and cancel operations are same-origin mutations. They require the
existing loopback Origin checks, JSON content type, bounded request bodies, and
non-following HTTP behavior. Confirmation tokens are single-use and short-lived;
they are defense-in-depth against stale or accidental form submission, not a
substitute for the local trust boundary.

The Agent view keeps ADR 0008's **800 KiB raw** and **120 KiB gzip-compressed**
total asset budgets. This ADR raises the board-authored JavaScript sub-limit from
50 KiB to **56 KiB raw** for the schema form, validation, and streamed result UI.
No CDN script, browser code editor dependency, Node build, frontend framework, or
dynamically downloaded UI asset is introduced by this decision. Raising any of
these three limits again requires another amendment.

## Implementation phases and gates

### Phase A — Contract and provenance

- Define versioned inspection, source, validation/test, cancellation, error, and
  content-block schemas.
- Retain explicit source category and extension ownership on every effective tool.
- Produce one deterministic catalog digest from the same effective catalog the
  model receives.

Gate: tests prove that effective-tool provenance survives initial session load and
extension reload, duplicate-name resolution is reported correctly, and inspection
of an absent or unloaded session performs no runtime construction or extension
execution.

### Phase B — Read-only inspection and source boundaries

- Implement the local inspection and opaque source APIs.
- Add the Agent Card, tool catalog, extension, and source views to Tau Board.

Gate: path traversal, absolute-path injection, symlink escape, binary file, size,
encoding, stale digest, managed-mode, and arbitrary-source tests all fail closed.
Inspection responses contain no secrets or prompt/conversation bodies. Core and
MCP source cannot be requested.

### Phase C — Project-tool validation and execution

- Implement schema validation, confirmation issuance, exact-tool lookup,
  per-session serialization, timeout, cancellation, bounded updates, and safe
  telemetry.
- Add the generated form, raw JSON fallback, explicit confirmation, live updates,
  and ephemeral result renderer.

Gate: tests prove that only project extension tools are runnable; validation alone
never calls a tool; stale digests and confirmation values fail; an active session
cannot race a test; cancellation and timeout terminate correctly; and a test adds
no conversation entry, Task, artifact, or platform session record.

### Phase D — Documentation and release verification

- Document the Agent concepts, workbench risk model, supported JSON Schema
  behavior, result limits, and extension-author workflow.
- Update the packaged local-development and project-customization skills so synced
  guidance matches the released SDK and board.
- Add end-to-end tests with one read-only example tool and one intentionally
  mutating fixture whose side effect occurs only after confirmation.

Gate: the SDK wheel and source distribution build with the bundled Board, the
board asset budgets pass, and a clean sample project can inspect its effective
card, view its extension entry source, validate arguments, run its project tool,
cancel a long-running invocation, and verify that chat and Task history were not
contaminated.

No phase may ship a Run control before the server-side ownership check,
serialization, digest check, confirmation, output bounds, and local-mode gate are
present.

## Consequences

- Repository authors get one place to understand the effective agent and exercise
  their tools without consuming a model turn.
- The runtime, rather than the board, remains authoritative for tool composition,
  provenance, and execution.
- Source inspection is intentionally incomplete when an extension has not supplied
  precise provenance; the UI reports that limitation instead of guessing.
- Tool execution is easier to invoke but is not made safer than the underlying
  project code. The explicit warning and confirmation are therefore permanent
  parts of the initial contract.
- Non-project tools remain visible for analysis but unavailable in the workbench.
  Supporting them later requires a separate decision with capability-specific
  authorization and side-effect policy.
- The board remains optional, loopback-only, separately running, and free of an
  import dependency on SDK internals.

## Rejected alternatives

### Let Tau Board discover and import `.tau` itself

Rejected because loading extensions executes code, duplicates Tau's composition
rules, can disagree with the actual session, and breaks the separate-process
contract.

### Derive forms from Python function signatures

Rejected because Tau invokes a uniform `AgentTool` wrapper and its JSON Schema is
the public argument contract. Python signatures behind decorators or factories
are neither stable nor necessarily available.

### Make every effective tool runnable

Rejected because coding tools would turn the board into a repository mutation and
shell surface, MCP tools can mutate live platform state, and Task controls require
protocol context. The initial use case is project extension development.

### Run a default/example case automatically when a tool is selected

Rejected because examples do not imply safety. Selecting or inspecting a tool must
remain read-only.

### Call the tool through a synthetic model conversation

Rejected because it introduces provider cost and nondeterminism, lets the model
change arguments, and contaminates conversation history. The workbench calls the
exact selected tool directly after explicit validation and confirmation.

### Promise rollback or sandboxing

Rejected because the SDK cannot generically undo filesystem, network, platform,
or arbitrary Python side effects. A future sandbox is a separate execution and
security architecture, not a label on this workbench.
