# Initial ADR Disposition Matrix

Status: Phase A0 complete

Date: 2026-09-16

These are proposed dispositions, not silent adoption. Final re-adoption or amendment occurs in the
new Main Sequence TAU SDK ADR index.

Owner for every row: Main Sequence TAU SDK. Records outside this repository are not in scope.

| Prior record | Current subject | Proposed disposition | Required new-project action |
| --- | --- | --- | --- |
| ADR 25 | Production A2A discovery and runtime access | Amend/reissue | Preserve SDK discovery metadata and authenticated transport behavior; remove deployment assumptions. |
| ADR 27 | Backend-only session initiation | Re-adopt client contract | Preserve rejection of client-created durable sessions and test it at the transport boundary. |
| ADR 28 | Durable A2A session envelope | Amend/reissue | Preserve durable message/task envelope semantics through SDK protocol and persistence clients. |
| ADR 29 | Agent type identity | Eliminate from SDK | The SDK has no runtime-role discriminator or agent-type execution switch. |
| ADR 30 | Backend agent type as runtime profile | Eliminate from SDK | Replace profile selection with one required workspace and explicit SDK settings. |
| ADR 31 | UID identity | Re-adopt | Preserve UID-based external identity in SDK wire models. |
| ADR 32 | Agent/session capability bindings | Supersede for SDK | Keep only capabilities required by concrete protocols; do not restore a duplicated registry. |
| ADR 33 | Warm A2A session runtime | Amend/reissue | Preserve lazy cached durable sessions, lease correctness, and safe eviction. |
| ADR 34 | A2A output, reasoning suppression, strict JSON | Amend/reissue | Preserve the tested wire output and strict-JSON behavior without Astro terminology. |
| ADR 35 | Superseded attachment protocol | Historical/eliminate | Do not import an already superseded protocol into the active SDK set. |
| ADR 36 | Superseded stateless passthrough | Historical/eliminate | Sessionless behavior is governed by ADR 44 and the current agent-response contract. |
| ADR 37 | Standard A2A wire protocol | Re-adopt | Preserve protocol-compatible request, response, streaming, and error shapes. |
| ADR 38 | Dual-sink logging | Amend/reissue | Preserve structured and optional human sinks under SDK names. |
| ADR 39 | Unified runtime context with optional project attachment and retained backend role names | Eliminate from active set; superseded by ADR 56 | Preserve only the general lesson that local behavior derives from concrete context. Require a workspace and remove both role identities. |
| ADR 40 | General Pi deployment runtime/adapter boundary | Eliminate from active set | It is based on the retired Astro/Pi deployment ontology. Re-evaluate any adapter seams during C1 rather than importing this proposal. |
| ADR 41 | Remove `agent_unique_id` | Re-adopt | Keep UID-only identity and no legacy alias in SDK models. |
| ADR 42 | A2A file parts and PDF intake | Amend/reissue | Preserve bounded file/PDF transport handling as an SDK protocol capability. |
| ADR 43 | Backend-backed A2A task gap analysis | Supersede | ADR 54 and current task contracts replace the gap analysis. |
| ADR 44 | Agent-targeted sessionless responses | Re-adopt | Preserve direct `AgentHarness` execution independently from durable sessions. |
| ADR 45 | Nonblocking Tau runtime I/O | Re-adopt | Preserve nonblocking request paths and bounded background work. |
| ADR 46 | Disable A2A push notifications | Supersede/amend | Follow the currently implemented task notification contract verified by route tests. |
| ADR 47 | Explicit A2A response kind | Amend/reissue | Preserve the wire-level response-kind behavior if contract tests confirm it; rewrite Astro ownership and verification for the SDK service. |
| ADR 48 | Batched Tau persistence and two-phase settlement | Amend/reissue | Preserve durability semantics, backend contracts, failure handling, and limits through SDK storage primitives. Remove image/runtime-role assumptions. |
| ADR 49 | Decoupled conversational critical path | Amend/reissue | Preserve latency and dependency-decoupling requirements; express them through SDK startup/session primitives and direct project deployment. |
| ADR 50 | Lean Python runtime ABI owned by standalone/bundle/overlay images | Supersede and eliminate image-specific decision | Extract only project-image prerequisites such as compatible Python, non-root operation, workspace, Git, and `rg` into a new project-runtime contract. Delete bundle/overlay ownership. |
| ADR 51 | Consume provider control through the existing external API | Amend/reissue | Preserve provider/model/credential evidence and validation in the SDK client primitive without prescribing external implementation changes. |
| ADR 52 | Repository Tau extensions in CodeRepository Executors behind a deployment flag | Supersede | Reissue Tau-native project extension semantics under the single `.tau` configuration model. Remove executor terminology and the enable flag. |
| ADR 53 | Retire the agent capability registry | Amend/reissue | Preserve the absence of a duplicated capability registry and the separation of protocol metadata from project resources. Rewrite ownership for the SDK. |
| ADR 54 | Durable asynchronous A2A lifecycle | Amend/reissue | Preserve accepted wire, task, persistence, replay, cancellation, and streaming behavior after contract verification. Rewrite runtime/deployment ownership. |
| ADR 55 | Minimize bundled tool catalog | Amend/reissue | Preserve the lean base catalog and project ownership of optional tools. Replace Astro terminology and make it a founding SDK tool-boundary decision. |
| Runtime credential authentication | Scoped runtime credential exchange | Re-adopt | Keep `MAINSEQUENCE_RUNTIME_CREDENTIAL_ID` and `MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET` as the SDK client contract. |
| Editable session configuration | Durable session metadata editing | Amend/reissue | Preserve only behavior exercised through current SDK client and transport contracts. |
| Checkpoint reasoning annotations | Snapshot reasoning metadata | Amend/reissue | Preserve compatible snapshot serialization without old package identity. |
| Compaction checkpoint retention | Durable compaction state | Amend/reissue | Preserve replay/retention invariants through SDK persistence tests. |
| Interactive provider sign-in | Interactive provider credential acquisition | Eliminate from SDK | The SDK consumes hydrated provider evidence; it does not own interactive sign-in UX. |
| Workspace analysis from orchestrator | Role-specific workspace analysis | Eliminate | The SDK always receives one explicit workspace and has no orchestrator role. |

The matrix covers every active ADR, the numbered historical records referenced by active decisions,
and the unnumbered runtime decisions used by the current Python implementation.

## Decision Rules

- A wire or persistence contract is not re-adopted until a black-box contract test exists.
- An image, package-injection, optional-workspace, or legacy-role assumption is eliminated even when
  embedded inside an otherwise useful ADR.
- A mixed ADR is amended/reissued; it is not copied wholesale.
- Historical records remain available for provenance but do not appear as accepted in the new
  active index.
