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
- Astro already has the authoritative local Pi session file even though backend `usage_summary` is
  still stale
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

## 13. Use one PVC-like volume as the container runtime source of truth

Implementation:

- `reference/adr-pvc-volume-layout.md`

Why:

- local Docker should simulate a single-PVC deployment instead of mixing repo-local state, host Pi
  auth/session state, and one named volume
- Pi runtime state should live under a standard `.pi/agent` folder inside the durable volume
- legacy repo-local state should be migrated once, with host Pi `auth.json` and `sessions/` merged
  once, then the volume should become the only runtime source of truth

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

- deployed coding-agent pods authenticate with runtime credentials instead of user refresh tokens
- the stream runtime must not block startup on `MAINSEQUENCE_REFRESH_TOKEN` when
  `MAINSEQUENCE_AUTH_MODE=runtime_credential`
- child `pi`, specialist, and project setup processes need the same runtime credential env as the
  parent process
