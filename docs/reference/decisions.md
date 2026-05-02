# Decisions

## 1. Keep the parent prompt static

Implementation:

- `.pi/APPEND_SYSTEM.md`

Why:

- the parent role is mostly stable
- static prompt files are easier to inspect than runtime-generated parent policy

## 2. Keep child policy dynamic

Implementation:

- `pi/extensions/hooks/project-policy/child-policy.md`
- `pi/extensions/hooks/project-policy/index.ts`

Why:

- child specialists should not behave like the parent
- child-only guardrails are naturally runtime-specific

## 3. Use specialists for focused delegated work

Implementation:

- `.pi/agents/mainsequence-project-coder.md`

Why:

- implementation belongs in a checked-out project specialist instead of the parent session
- tutorial verification can still delegate checked-out project work without introducing a second builder role

## 4. Keep repo-local runtime in TypeScript

Implementation:

- `pi/extensions/hooks/`
- `pi/extensions/tools/`
- `pi/extensions/shared/`
- `scripts/*.ts`

Why:

- the local runtime logic is mostly orchestration and file shaping
- avoiding a local language bridge keeps Astro easier to debug

## 5. Keep dependency installation out of `.pi`

Implementation:

- `.pi/settings.json`
- `package.json`
- `node_modules/pi-web-access`

Why:

- the runtime should stay inspectable and avoid generating extra package state inside `.pi`
- external Pi packages should live in normal npm dependency locations

## 6. Keep tutorial verification out of the default parent-agent contract

Implementation:

- `pi/prompts/verify-mainsequence-tutorial.md`

Why:

- it is a fixed CI-shaped workflow
- it is too specific to live inside the default always-on agent capability list
- it can still exist as an explicit standalone prompt without shaping normal project routing

## 7. Do not auto-inject docs into agent context

Implementation:

- `docs-context` extension removed

Why:

- the agent already has role instructions in `.pi/APPEND_SYSTEM.md` and `.pi/agents/*.md`
- user-facing docs should not be mixed into the agent prompt by default

## 8. Keep inspectable runtime state outside ephemeral containers

Implementation:

- `docker-compose.yml`
- `Dockerfile`
- `docs/reference/persistent-state.md`

Why:

- session continuity, hydration, and deterministic backend tools depend on runtime artifacts
- developers need to inspect those artifacts from the host without shelling into containers
- containers should stay disposable while durable state lives under the repo-local `.astro/` root

## 9. Treat custom model selection as first-class session state

Implementation:

- `reference/adr-custom-model-integration.md`

Why:

- private endpoints and Ollama selection need a persistent session-level model identity
- request-scoped custom providers should not be implemented by mutating shared runtime `models.json`
- Ollama discovery needs a clear contract for listing models before the user selects one

## 10. Expose session usage and context from the local Pi session file first

Implementation:

- `reference/adr-session-usage-and-context.md`

Why:

- users need live visibility into token consumption and remaining context budget before compaction
- Astro already has the authoritative local Pi session file and backend session insights are the
  evolving usage source of truth
- read-only session usage/context endpoints unblock the UI without waiting for backend mirroring

## 11. Expose auth-backed model providers as a dedicated remote control plane

Implementation:

- `reference/adr-remote-model-providers.md`

Why:

- providers like OpenAI and Anthropic need inspectable runtime auth state
- credentials can remain environment-owned while Astro manages provider sign-in and sign-off
- auth-backed model discovery should show known models separately from current usability

## 12. Handle interactive provider signin as explicit attempt state

Implementation:

- `reference/adr-interactive-provider-signin.md`

Why:

- OAuth-style providers such as `openai-codex` need a remote lifecycle that survives the initial
  `POST /signin`
- the frontend needs provider-agnostic `status` and `nextAction` hints instead of provider-specific
  branching
- signoff must remain provider-level even while a signin attempt is running

## 13. Removed local durable runtime storage as a source of truth

Implementation:

- superseded by `reference/adr-emptydir-session-checkpoint-storage.md`

Why:

- local Docker and Kubernetes must not depend on durable local session/auth storage
- Pi runtime state may use local files while the container is alive, but continuity must come from
  backend checkpoints and backend-owned session records
- the `astro-orchestrator` process should use a writable runtime cwd with a materialized `.pi`
  copy, not `/app`, because Pi creates project settings lock files next to `.pi/settings.json`
- provider auth/signin state is pruned on container startup and is not restored from host or
  repo-local paths

## 14. Advertise editable session config through session-insights

Implementation:

- `reference/adr-editable-session-config.md`

Why:

- the frontend should learn which session config fields are editable directly from the read
  contract
- editability needs richer metadata than `true/false`, including types, ranges, units, and enum
  values
- writes should still happen through a separate narrow patch endpoint without duplicating the full
  effective config payload

## 15. Hydrate Astro local orchestrator runtime state from backend-owned sessions

Implementation:

- `reference/adr-backend-session-hydration.md`

Why:

- backend `AgentSession.id` should be the source of truth for orchestrator session
  recoverability, not only the visible runtime id
- Astro must be able to attach to backend-created `astro-orchestrator` sessions it did not
  originally start
- the first implementation should attach and locally hydrate wrapper state, not create a second
  backend session or re-run deterministic agent registration
- project-scoped coder sessions are intentionally out of scope for the first hydration pass

## 16. Treat runtime credentials as the production Main Sequence auth mode

Implementation:

- `reference/adr-runtime-credential-auth.md`

Why:

- deployed coding-agent pods authenticate with runtime credentials
- the stream runtime must not block startup on token-style auth when
  `MAINSEQUENCE_AUTH_MODE=runtime_credential`
- child `pi`, specialist, and project setup processes need the same runtime credential env as the
  parent process

## 17. Replace shared runtime PVC session state with emptyDir checkpoints

Implementation:

- `reference/adr-emptydir-session-checkpoint-storage.md`

Why:

- Pi requires local session JSONL files, but shared writable PVC state prevents clean horizontal
  scaling
- Astro can restore a session bundle into pod-local `emptyDir` before launching each Pi child
  process
- a sidecar can continuously checkpoint local Pi/Astro session files back to the backend without
  putting Postgres on every streamed chat chunk

## 18. Make the backend authoritative for Astro AgentSession allocation

Implementation:

- `reference/adr-backend-owned-agent-session-allocation.md`

Why:

- checkpoint restore and leases need the backend to own the canonical session identity first
- Astro should execute restored sessions, not duplicate backend Agent and AgentSession creation
  rules
- project handoff state should be visible in backend-owned session metadata from creation time

## 19. Make workspace analysis a first-class orchestrator capability

Implementation:

- `reference/adr-workspace-analysis-from-orchestrator.md`

Why:

- workspace analysis is a distinct orchestrator responsibility, not project implementation
- the required Main Sequence analysis context should be prepared during deterministic startup, not
  discovered lazily during the first chat turn
- runtime readiness for that capability should be observable before Astro starts serving requests

## 19. Store user model-provider credentials in the backend

Implementation:

- `reference/adr-backend-owned-provider-credentials.md`

Why:

- provider sign-in should keep the same user-facing flow while durable storage moves out of pods
- Pi expects provider credentials in `PI_CODING_AGENT_DIR/auth.json`, so Astro should hydrate a
  scoped pod-local auth dir before provider-backed model use
- OAuth providers can refresh credentials while Pi is running, so Astro needs an explicit
  hydrate/flush cycle with backend version checks

## 20. Treat Pi compaction as a backend checkpoint retention boundary

Implementation:

- `reference/adr-compaction-checkpoint-retention.md`

Why:

- Pi compaction changes model context but does not automatically delete old JSONL entries
- backend-owned checkpoint storage must not retain the full pre-compaction conversation forever
- accepted compaction flushes should normalize and prune the latest checkpoint bundle while keeping
  Pi restore valid
- frontend history hydration should render a compacted summary boundary instead of resurrecting
  deleted turns

## 21. Preserve reasoning presence as checkpoint metadata

Implementation:

- `reference/adr-checkpoint-reasoning-annotations.md`

Why:

- some Pi/provider paths stream reasoning live but do not persist it into Pi JSONL
- storing full frontend history in the backend would create a second transcript authority
- a lightweight checkpoint annotation lets Astro hydrate the thinking UI without duplicating the
  conversation or storing raw reasoning text

## 22. Detached browser clients do not cancel active session runs

Implementation:

- `reference/adr-detached-client-background-session-runs.md`

Why:

- browser close is a transport detach, not an agent-session cancellation
- backend-backed sessions should keep Pi running until the assistant run finishes or fails
- checkpoint lease renewal and runtime-state reporting let the backend know that a session is still
  working even when no browser is connected

## 23. Treat project executors as backend-mediated runtimes

Implementation:

- `reference/adr-23-backend-mediated-project-executor-runtimes.md`

Why:

- `mainsequence-project-executor` is a separate execution runtime, not a normal orchestrator
  specialist
- local development needs a mounted-project executor harness without changing the backend control
  plane shape
- the backend should be the only component that knows how to launch and route work to project
  executors

## 24. Treat A2A discovery as a shared prompt-layer collaboration rule

Implementation:

- `reference/adr-24-a2a-discovery-and-collaboration.md`

Why:

- A2A should be a collaboration modality, not a session-switch substitute
- the orchestrator needs explicit user-confirmed discovery behavior before A2A initiation
- project-scoped agents need bounded A2A without broadening their core roles
