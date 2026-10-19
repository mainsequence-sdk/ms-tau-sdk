# ADR 0024: Chat Attachments and Authorized Content Delivery

Status: Proposed — requirements recorded; not implemented

Date: 2026-10-19

Owners: MS Tau SDK maintainers, who own the attachment contract; Command Center AI implements its
browser side

Companion: [Command Center AI SDK ADR 018: File attachments in the shared chat interface](https://github.com/mainsequence-sdk/command-center-sdk/blob/development/command-center-ai/docs/adr/adr-sdk-018-chat-file-attachments.md)

Related:

- [ADR 0002: Runtime and protocol contracts](./0002-runtime-and-protocol-contracts.md)
- [ADR 0006: SDK-owned agent development skills](./0006-sdk-owned-agent-development-skills.md)
- [ADR 0011: Independent base-tool and Main Sequence MCP exclusion](./0011-independent-base-tool-and-main-sequence-mcp-exclusion.md)
- [ADR 0018: Reload-safe local chat sessions](./0018-reload-safe-local-chat-sessions.md)
- [ADR 0019: Verified request identity and session ownership](./0019-verified-request-identity-and-session-ownership.md)
- [ADR 0021: MCP connections on the person's identity](./0021-mcp-connections-on-the-persons-identity.md)
- [ADR 0023: Skills and resources of declared applications](./0023-skills-and-resources-of-declared-applications.md)

## Context

A person using an application's assistant needs to attach a file in the shared Command Center AI
composer and ask the assistant about it. The installed SDK 2.0.18 and the inspected 2.0.19 source
do not support this end to end: `ChatRequest.prompt_text()` extracts only text from user content,
and `/api/chat` starts a turn with that string. Mixed text/file input loses the file content. A
file-only request has no usable prompt and may fail or, when older user messages are supplied,
select older text.

The separate A2A input path accepts inline PDFs, validates and materializes them, and appends a
file manifest to the prompt. Its current route allowlist is PDF only; configurable defaults allow
8 files, 20 MiB per file and 40 MiB total. This is not the chat contract. Materializing bytes and
naming a local path do not establish that a particular provider or a restricted helper can read
them. The chat must retain its existing streaming, session and cancellation contract.

The pinned tau-ai 0.4.2 accepts only text and image parts in a user message, with no part for PDFs
or other documents, and the Tau coding session's `prompt` takes a string. A PDF cannot reach the
model as a document through Tau today, and an image cannot reach it through the current turn entry
point.

The first applications to need this already store their documents in their own backends, with
their own access checks, extraction, retention and revisions, and their assistants read that
content through read-only application tools. They need common document and image types such as
PDF, PNG, JPEG and plain text, with per-file limits. These needs shape the first version; they are
not limits for every application.

## Proposed decision and requirements

### 1. Attachment references in chat

Tau owns a versioned attachment contract as part of its
[runtime contract](../reference/runtime-contract.md): capability discovery, attachment references
in the newest user message, processing status, structured errors, history and answer source
references. Its routes are frozen with the rest of the HTTP surface in
`tests/contract/test_http_surface.py`. Command Center AI SDK ADR 018 implements the browser side
and follows this contract. Choose exact endpoints, schemas and version negotiation before
implementation; this record does not declare those APIs shipped.

A chat message carries stable references issued by the application that owns each document, with
display metadata such as filename, media type and byte count. Integrity, identity, scope and
provenance are server-owned. A reference identifies the immutable document revision a turn uses.
Raw browser object URLs, filesystem paths and arbitrary remote URLs must not become trusted
references.

Accept attachment-only messages and text plus attachments. Validate the newest submitted user
message as a unit; never discard file parts, silently fall back to an older user message, or
admit a partial text-only turn after attachment validation fails. Preserve existing text-only
clients and the existing `ui-message-stream` transport and session/task controls.

### 2. First version: documents the application stores

In the first version every attachment is a document that an application stores. The application
receives the upload, checks access, extracts content, keeps revisions and decides retention,
expiry and deletion; the browser uploads to that application's own service (Command Center AI SDK
ADR 018, section 3). Tau stores no uploaded file and runs no upload lifecycle of its own. It must
not implement an application's storage or business authorization.

Tau resolves each reference through an adapter for the owning application, bound to the turn's
verified requester. The adapter returns verified content and provenance, or an explicit
unavailable or denied state.

Files that Tau receives and keeps itself, for applications without a document store, are out of
scope. A later ADR may add them when an application needs them.

### 3. Negotiated capabilities and bounded admission

Advertise supported media types, count and per-file/aggregate byte limits and processing modes.
Compute them from the selected provider/model, the owning application and applicable application
policy; they do not come from a new Tau setting. Revalidate at turn admission, including queued
sends and model changes. Unsupported processing must return an explicit failure rather than a
success with unread bytes.

Before content reaches the model, check that the reference belongs to the caller and that the
content matches its media type, size and digest and stays within the capability. Reject
unsupported or malformed input before model execution, with bounded resource use. An encrypted or
unreadable document, or a failed extraction, reported by the application is an explicit failure.
Do not fetch arbitrary URLs or accept caller-selected filesystem locations.

A2A limits must not be assumed to apply to chat. Reuse suitable validation primitives from A2A
where their semantics match, while preserving its public contract. Adding chat attachments must
not silently broaden A2A's PDF-only route allowlist.

### 4. Verified identity and application-owned documents

Authorize attaching, reading and reusing a reference, and following a citation, against the
authenticated caller and the session's verified requester under ADRs 0019 and 0021. Browser fields
do not establish identity. The application enforces its document access independently of access
to an agent or conversation, including when history is replayed. A reference must not grant
cross-person, cross-session or cross-application access by possession alone.

An Agent that runs with both Tau tool exclusions of ADR 0011 must be able to use attachments
through its application's read-only contract alone. Enabling attachments never requires shell,
direct filesystem, HTTP, database or storage access, or lifting either exclusion. Reading binary
resources under ADR 0023 alone is not evidence of a usable document-content path.

### 5. Deliver usable content, not merely a filename

Define and test how each advertised media type becomes usable model input. With the pinned
tau-ai, a PDF or text document reaches the model as text supplied by the owning application,
never as a native document part. An image needs Tau to pass image content into a turn, which the
current text-only entry point cannot do. Tau closes that gap in its own code or by moving to a
tau-ai release that supports it, never by patching or forking the pinned tau-ai; until then it
does not advertise image types. Do not rely on a local path manifest when the agent has no
file-reading capability. Fail explicitly when no permitted path can process the attachment.

Treat content extracted from files as untrusted source data. File content must not become system
instructions, a tool grant, a requester identity or an authorization decision. Preserve source
identifiers and immutable revisions, plus page/section locations where available, so answers can
cite the content actually used. Distinguish a document the application has accepted, one it has
finished processing and content usable as model input in status.

### 6. History and turn lifecycle

Retain attachment references and provenance with the user message in canonical history. Local
history and hosted projections must agree and survive their documented runtime restart behavior.
Any hosted platform projection change is an explicit dependency, not a change silently delegated
to the browser.

Retries must not duplicate admitted turns. Cancelling an upload belongs to the application and is
distinct from stopping an agent turn. Preserve the existing local and hosted disconnect semantics
instead of introducing a new cancellation policy incidentally.

Specify how history renders expired, deleted, unavailable or access-revoked documents, and whether
content passed to the model stays in session history after the application deletes the document
or revokes access. Historical metadata must not leak to an unauthorized reader. Keep raw bytes,
signed URLs and credentials out of logs and browser persistence; the application authorizes each
preview or download afresh.

### 7. A packaged skill for agents and applications

The implementation adds a skill to the SDK bundle of ADR 0006, proposed name
`tau_chat_attachments`, and amends that ADR's list. For the released contract it explains:

- how an attachment travels: the browser uploads the file to the application, the application
  returns a reference to one version of the document, the chat message carries the reference, and
  Tau asks the application for the content for the person the turn serves;
- what an application or its MCP tools must do: say which file types and sizes it accepts, store
  each document and its versions, issue references, check that person's access on every read,
  including history replay and citations, and return the text with its document, version and page
  or section, or an explicit denied, unavailable or failed state;
- what an application must never return: raw storage URLs, filesystem paths or credentials; and
  that file content is data, never instructions;
- how an Agent with both exclusions of ADR 0011 uses attachments, and how to test the whole path
  locally.

The skill follows the contract and, as ADR 0006 requires, does not redefine platform identity or
authorization.

## Acceptance gates for the future implementation

1. Contract fixtures cover capabilities, references, errors and history across Command Center AI,
   local Tau and hosted projections, and the HTTP surface test freezes the new routes. Existing
   text clients remain compatible.
2. File-only, text-plus-file and multiple-file turns consume the intended newest message. Each
   advertised media type produces usable content and an answer citing the correct document and
   revision.
3. A mismatched media type, digest or size, count/aggregate overflow, unsupported/encrypted
   content, missing references and failed extraction cannot silently become successful text-only
   turns.
4. Another person's/session's/application's reference, a forged requester, revoked access and
   replay attempts fail without leaking content. Local owner-scope isolation is covered too.
5. Retry, interruption, queue delay, expiry, model changes and concurrent sends have deterministic
   outcomes without duplicate turns.
6. Reload and runtime restart preserve committed message attachment associations; deletion and
   revoked access are visible; hosted and local history carry the same agreed semantics.
7. An Agent with both exclusions obtains content only through its application's read-only
   contract. Instructions embedded in the file do not widen access or tools.
8. Real local integration with the packaged Command Center AI composer and a real application
   document service verifies upload through answer and citation before hosted verification.
   Stand-in fixtures alone do not close this gate.
9. The SDK bundle ships the attachment skill of section 7, and the application used in gate 8
   follows it.

## Compatibility, dependencies and implementation gate

This ADR changes documentation only. It introduces no runtime routes, Python API, settings,
storage schema, tool access, pinned contracts or package version changes. The eventual change
must update runtime/security references, packaged skills, including the new skill of section 7
and ADR 0006's list, the changelog, frozen HTTP contracts and any history migrations, with
backward-compatibility coverage for text clients and explicit release ordering.

Before coding, resolve the precise wire schemas, the reference format, the capability discovery
location, the adapter for application-owned documents, image delivery with the pinned tau-ai,
and what history keeps of content passed to the model. The adapter must work in hosted mode, for
example through a declared application (ADR 0021), and in local mode, which has no declared
applications. Identify any platform history/proxy/forwarder work and its owner. These decisions
must satisfy the requirements above and the companion ADR; they are not permission to bypass
existing policy.

An application starts its own attachment integration only after this ADR and Command Center AI
SDK ADR 018 are implemented, compatible releases are available and their local end-to-end gate
passes. An application's existing document upload is not evidence that chat attachments work.
