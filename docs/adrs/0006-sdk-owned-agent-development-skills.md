# ADR 0006: SDK-Owned Agent Development Skills

Status: Accepted

Date: 2026-09-17

## Context

Main Sequence platform skills currently combine two different authorities. Django correctly owns
the platform ontology, deployment resources, canonical A2A protocol, authorization, and durable
task lifecycle. Some of those skills also prescribe the concrete `ms-tau-sdk` dependency,
application module, environment variables, local-development workflow, `.tau` extensions, and
runtime debugging procedure.

Those implementation details version with this Python distribution, not with Django. Keeping them
in the platform bundle can make a consuming project receive instructions that do not match its
installed SDK. It also keeps terminology and runtime roles from the retired deployment project in
otherwise current platform guidance.

The Main Sequence Python SDK already demonstrates the desired ownership model: the platform owns
language-neutral semantics while an installed library supplies version-matched execution skills.

## Decision

### 1. Package SDK development skills

`ms-tau-sdk` packages these skills below `src/ms_tau_sdk/agent_skills`:

- `tau_repository_integration`;
- `tau_local_development`;
- `tau_project_customization`; and
- `tau_a2a_runtime_adapter`.

They own SDK installation and entry points, authenticated local execution, debugging, `.tau`
customization, extension mechanics, and the TAU host side of A2A. They do not redefine platform
identity, deployment, authorization, discovery, or task-lifecycle semantics.

### 2. Synchronization is explicit and version-matched

A consuming project invokes the command from its installed and locked SDK:

```bash
uv run ms-tau skills sync --path .
```

The command atomically replaces only:

```text
.agents/skills/ms_tau_sdk/
```

It writes `PINNED_FROM.txt` with the distribution version and source provenance. It does not alter
repository-owned skills or `.agents/skills/mainsequence/`. Installation, import, application
construction, and ordinary `ms-tau` startup never synchronize files implicitly. A dry run is
available for inspection.

No-argument `ms-tau` remains the runtime entry point. `skills list` and `skills path` expose the
installed bundle for diagnosis.

### 3. Keep the distribution independent

The synchronization implementation is owned locally and introduces no dependency on the
`mainsequence` Python distribution. It may follow the same namespace and provenance principles,
but neither CLI owns or updates the other package's managed skill namespace.

### 4. Reduce Django skills to platform authority

Django's `a2a_communication` skill retains canonical MCP inputs, discovery, runtime-access
authorization, caller-session proof requirements, A2A wire behavior, task lifecycle, idempotency,
and retry rules. It describes trusted-host obligations generically and contains no TAU host-tool
projection or retired runtime terminology.

Django's `code_repository_to_agent` skill retains repository-backed Agent ontology, the source
card, resource indexing, the `harness_agent` workflow, ResourceRelease behavior, and deployment
evidence. For runtime implementation it directs the user to synchronize and read the installed
`ms-tau-sdk` skills instead of embedding those instructions.

The cut is immediate. There is no period in which Django and this package both claim authority for
the same implementation mechanics.

### 5. Keep repository behavior repository-owned

The synchronized SDK skills explain how `.tau` works but do not become runtime configuration.
Effective instructions, prompts, skills, extensions, optional tools, and business behavior remain
owned by the consuming repository under its normal `.tau` and application trees.

## Ownership matrix

| Owner | Authoritative concerns |
| --- | --- |
| Django | Platform ontology, A2A protocol, authorization, task state, ResourceRelease and workflow contracts |
| `mainsequence-sdk` | Main Sequence Python client and CLI execution mechanics |
| `ms-tau-sdk` | TAU application/runtime mechanics, local development, debugging, project customization, A2A host adaptation |
| Consuming repository | Effective agent behavior, domain code, project skills, `.tau` resources and extensions |

## Implementation gates

1. The wheel and source distribution contain the complete skill bundle.
2. A clean installed wheel can synchronize the bundle and provenance into a temporary project.
3. Synchronization removes skills retired by the installed SDK without changing sibling
   namespaces.
4. No-argument `ms-tau` continues to start the application.
5. Active Django platform capabilities contain no retired project terminology or SDK-versioned
   local-run, debugging, entrypoint, or extension instructions.
6. Main Sequence scaffold routing recognizes that TAU implementation guidance comes from the
   independently managed `ms_tau_sdk` namespace.

## Consequences

Consuming repositories gain instructions that match their installed runtime. Django skills become
smaller and remain stable across SDK implementation changes. Updating platform knowledge and TAU
knowledge becomes two explicit operations with independent provenance.

An existing repository must run the synchronization command once after adopting a release that
contains this decision. Managed copies may be overwritten on the next synchronization and must not
contain repository-owned edits.
