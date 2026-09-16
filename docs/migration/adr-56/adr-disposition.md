# Initial ADR Disposition Matrix

Status: Phase A0 working ledger

Date: 2026-09-16

These are proposed dispositions, not silent adoption. Final re-adoption or amendment occurs in the
new Main Sequence TAU SDK ADR index.

| Prior record | Current subject | Proposed disposition | Required new-project action |
| --- | --- | --- | --- |
| ADR 39 | Unified runtime context with optional project attachment and retained backend role names | Eliminate from active set; superseded by ADR 56 | Preserve only the general lesson that local behavior derives from concrete context. Require a workspace and remove both role identities. |
| ADR 40 | General Pi deployment runtime/adapter boundary | Eliminate from active set | It is based on the retired Astro/Pi deployment ontology. Re-evaluate any adapter seams during C1 rather than importing this proposal. |
| ADR 47 | Explicit A2A response kind | Amend/reissue | Preserve the wire-level response-kind behavior if contract tests confirm it; rewrite Astro ownership and verification for the SDK service. |
| ADR 48 | Batched Tau persistence and two-phase settlement | Amend/reissue | Preserve durability semantics, backend contracts, failure handling, and limits through SDK storage primitives. Remove image/runtime-role assumptions. |
| ADR 49 | Decoupled conversational critical path | Amend/reissue | Preserve latency and dependency-decoupling requirements; express them through SDK startup/session primitives and direct project deployment. |
| ADR 50 | Lean Python runtime ABI owned by standalone/bundle/overlay images | Supersede and eliminate image-specific decision | Extract only project-image prerequisites such as compatible Python, non-root operation, workspace, Git, and `rg` into a new project-runtime contract. Delete bundle/overlay ownership. |
| ADR 51 | Consume provider control through the existing external API | Amend/reissue | Preserve provider/model/credential evidence and validation in the SDK client primitive without prescribing external implementation changes. |
| ADR 52 | Repository Tau extensions in CodeRepository Executors behind a deployment flag | Supersede | Reissue Tau-native project extension semantics under the single `.tau` configuration model. Remove executor terminology and the enable flag. |
| ADR 53 | Retire the agent capability registry | Amend/reissue | Preserve the absence of a duplicated capability registry and the separation of protocol metadata from project resources. Rewrite ownership for the SDK. |
| ADR 54 | Durable asynchronous A2A lifecycle | Amend/reissue | Preserve accepted wire, task, persistence, replay, cancellation, and streaming behavior after contract verification. Rewrite runtime/deployment ownership. |
| ADR 55 | Minimize bundled tool catalog | Amend/reissue | Preserve the lean base catalog and project ownership of optional tools. Replace Astro terminology and make it a founding SDK tool-boundary decision. |

## Required Expansion

Phase A0 must also classify the older records referenced by active decisions, including at least:

- ADRs 25, 27–38, and 41–46 under `docs/reference/`;
- runtime credential authentication;
- editable session configuration;
- checkpoint/compaction retention;
- interactive provider sign-in;
- workspace analysis; and
- any repository-local historical decisions referenced by the current Python implementation.

## Decision Rules

- A wire or persistence contract is not re-adopted until a black-box contract test exists.
- An image, package-injection, optional-workspace, or legacy-role assumption is eliminated even when
  embedded inside an otherwise useful ADR.
- A mixed ADR is amended/reissued; it is not copied wholesale.
- Historical records remain available for provenance but do not appear as accepted in the new
  active index.
