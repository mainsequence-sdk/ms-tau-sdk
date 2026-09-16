# ADR 53: Retire the Agent Capability Registry from Astro Tau

Status: Accepted

Date: 2026-09-14

Implementation Status: Implemented in Astro Tau 4.0.23 as the client side of the coordinated
tdag-django ADR-032 contract cutover. Astro Tau 4.0.22 removed the fields but retained the obsolete
runtime-version gates and must not be deployed with the new Django contract.

Owners: Astro Tau and tdag-django

Supersedes:

- the runtime and control-plane design in
  [`ADR 32`](../reference/adr-32-agent-session-capability-bindings.md); and
- the capability payload, cache, and capability-hash portions of
  [`ADR 49`](./adr-49-decouple-tau-conversational-critical-path.md).

## Context

Astro previously consumed a centralized `AgentCapability` resource with Agent and AgentSession
binding rows. Cold session loading sent locally cached content hashes, received bindings and
content through the Tau bootstrap response, wrote session-specific `.agents/skills` files under a
temporary asset root, and passed that generated root to Tau. Resume snapshots also carried a
capability-set hash so a binding change invalidated a snapshot.

That design duplicates the authoritative delivery mechanisms that now exist:

- a CodeRepository owns project-local skills under `.agents/skills`;
- a CodeRepository owns executable Tau extensions under `.tau/extensions`;
- the Main Sequence MCP server owns platform skills, resources, and operations; and
- Tau owns repository resource and extension discovery from the configured working directory.

The registry adds DTOs, routes, storage, cache scans, network response size, filesystem writes,
hash computation, snapshot coupling, and failure modes without owning a remaining authoritative
capability category.

Two other concepts use the word capability and remain distinct:

- A2A Agent Card `capabilities`, including protocol extensions, describe wire-protocol behavior.
- backend `runtime_capabilities` advertise versioned Astro/Django runtime operations such as Tau
  bootstrap and resume snapshots.

Neither concept is a content registry or a session materialization source.

## Decision

Retire the centralized AgentCapability/session-binding registry from Astro Tau and remove its
runtime effects.

Astro will no longer:

- define AgentCapability, SessionCapabilityBinding, or CapabilityContent DTOs;
- call session capability-list or capability-content routes;
- scan a session asset cache for known capability hashes;
- accept a typed capability payload in the Tau bootstrap model;
- materialize backend content into a generated `.agents` tree;
- configure or create `ASTRO_SESSION_ASSET_ROOT`;
- retain a capability-set hash on a loaded runtime; or
- send, validate, persist, or compare capability hashes in bootstrap or resume snapshots.

The Tau runtime receives the CodeRepository as `cwd` and uses its native resource discovery. Astro
continues to pass the deployment-controlled `project_extensions_enabled` setting. Generic Astro
deployments default it to false; CodeRepository Executor images enable it according to ADR 52.
Astro also continues to initialize and reuse the process-scoped Main Sequence MCP catalog.

### Authoritative ownership

| Concern | Authoritative source | Astro behavior |
| --- | --- | --- |
| Project skills | `<repository>/.agents/skills` | Let Tau discover them from `cwd`. |
| Project executable extensions | `<repository>/.tau/extensions` | Let Tau load them when the executor setting is enabled. |
| Platform skills, resources, and operations | Main Sequence MCP | Discover once per process and expose through the MCP adapter. |
| A2A protocol capabilities and extensions | Effective Agent Card | Preserve normalization, validation, and advertisement. |
| Runtime protocol versions | Django `runtime_capabilities` | Require the supported Tau bootstrap/runtime contract. |
| Session history and acceleration | Django entries and Tau resume snapshot | Validate only schema, Tau version, runtime configuration, payload hash, and sequence boundaries. |

Project resources are revisioned with the CodeRepository image or checkout. A new project revision
therefore receives a new runtime deployment boundary rather than mutating an existing session
through an out-of-band binding overlay.

## Coordinated contract cutover

This is the Astro side of tdag-django ADR-032's destructive registry retirement. Django removes the
models, routes, serialization, snapshot field, and bootstrap behavior rather than retaining a
transition capability payload.

The new shared contract is explicit:

- Django advertises `tau_runtime_bootstrap="v3"` and returns no `capabilities` field;
- Django advertises `tau_resume_snapshot="v2"` and accepts/returns no
  `capability_set_sha256`;
- Astro requires those exact `runtime_capabilities` versions;
- Astro advertises and writes snapshot schema version `2`; and
- Astro sends neither `known_capability_hashes` nor `capability_set_sha256`.

This version gate intentionally makes the incompatible combinations fail closed. Astro 4.0.21 and
earlier require Django's old v2/v1 contract. Astro 4.0.22 removed the fields but accidentally kept
those old gates. Astro 4.0.23 requires Django's new v3/v2 contract. Deploy Django ADR-032 and Astro
4.0.23 in one coordinated, drained rollout; neither side should serve conversation traffic while
paired with the other contract generation.

## Hot-path effect

For every cold loaded session, the change removes:

1. one recursive cache-directory scan and hash-set construction before bootstrap;
2. capability binding/content response parsing;
3. one materialization task, including directory cleanup, path validation, file writes, and content
   hashing; and
4. capability-set comparison during snapshot restoration.

For every asynchronous snapshot upload, it removes capability-hash projection and persistence.
Warm turns were already insulated by the loaded runtime cache, so their provider and durability
paths are otherwise unchanged. This decision reduces cold-path work and state dimensions; it does
not claim a fixed latency improvement independent of repository size, Django latency, or provider
latency.

## Compatibility and non-goals

This decision does not remove or rename:

- A2A Agent Card `capabilities` or `capabilities.extensions`;
- `AgentSession.runtime_capabilities`, `RuntimeState.runtime_capabilities`, or the bootstrap
  response's `runtime_capabilities`;
- repository `.agents/skills` discovery;
- repository `.tau/extensions` loading;
- Main Sequence MCP resources/tools; or
- Tau resume snapshots themselves.

It does not introduce a replacement session-overlay mechanism. A future requirement for dynamic
session instructions must choose an explicit owner and delivery contract in a separate decision;
it must not silently recreate the retired generic registry.

## Verification

Astro tests must prove that:

- bootstrap requests omit `known_capability_hashes`;
- snapshot upload requests omit `capability_set_sha256`;
- runtime capability gates require bootstrap v3 and resume snapshot v2;
- snapshot requests, payloads, and compatibility checks use schema version 2;
- session loading performs no capability route call or materialization;
- Tau receives the repository `cwd` with no generated `agents_root`;
- repository extensions retain the deployment-controlled setting;
- MCP tools and resources remain composed into the session; and
- A2A and `runtime_capabilities` tests remain unchanged and passing.

Static checks must find no Astro production references to the retired DTOs, routes, materializer,
asset-root setting, or capability hashes.

## Consequences

Benefits:

- fewer cold-session operations and no registry/materialization failure path;
- smaller bootstrap and snapshot contracts;
- no duplicated project skill state under `/tmp`;
- one clear owner for each resource category; and
- a simpler destructive cleanup path in tdag-django.

Costs:

- backend-authored session-specific capability overlays cease to affect Astro execution;
- changing project skills or extensions follows the CodeRepository revision/deployment lifecycle;
  and
- rollout requires the small Django serializer compatibility step before Astro deployment.

## Alternatives rejected

### Keep the registry as an optional overlay

Rejected because an optional generic overlay preserves the same ambiguity, contracts, cache,
materializer, and snapshot invalidation dimension.

### Move all project resources into MCP

Rejected because repository skills and executable extensions are revision-local assets already
supported by Tau. MCP remains authoritative for platform-wide resources and operations.

### Encode capability hashes elsewhere in the snapshot

Rejected because Astro has no remaining centralized capability set to hash. Runtime configuration,
snapshot payload, schema/version, and durable sequence checks remain sufficient for Astro-owned
snapshot validation.
