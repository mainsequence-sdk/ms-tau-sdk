# Request lifecycle

This is the easiest way to understand Astro end to end.

## 1. Startup

Pi discovers `.pi/settings.json` and loads:

- local extensions
- local prompts
- local skills
- the repository package itself
- repo-installed packages such as `pi-web-access`

The Astro entrypoints load `.env` from the repo root and prepare Main Sequence auth according
to `MAINSEQUENCE_AUTH_MODE`.
In production, `MAINSEQUENCE_AUTH_MODE=runtime_credential` uses
`MAINSEQUENCE_RUNTIME_CREDENTIAL_ID` and `MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET`; runtime
credential exchange is the production auth path.
For the HTTP stream path, Astro keeps startup and `GET /health` independent from auth, then runs
the Main Sequence auth gate before each real `POST /api/chat` request so Pi never starts from an
unauthenticated CLI state.

The deployable Docker targets boot the same runtime by copying only:

- `.pi/`
- `pi/`
- `interface/`
- `runtime/`
- `adapters/`
- `bin/`
- `tools/`
- `package.json`
- `package-lock.json`
- `tsconfig.json`

`docker-compose.yml` starts those same targets while bind-mounting only:

- editable source paths such as `./.pi`, `./pi`, `./interface`, `./runtime`, `./adapters`,
  `./bin`, and `./tools`
- tmpfs-backed `astro_session_emptydir` volume at `/session-state` for pod-local session files

For the HTTP stream service, compose also sets
`ASTRO_MAINSEQUENCE_CONFIG_DIR=/home/jovyan/.astro-container-data/.config/mainsequence`,
`PI_CODING_AGENT_DIR=/home/jovyan/.astro-container-data/.pi/agent`, and
`ASTRO_STREAM_SESSION_DIR=/session-state/sessions` so local Docker follows the emptyDir session model.
Astro also materializes repo-local `/app/.pi` into
`/home/jovyan/.astro-container-data/.pi/project` and runs `astro-orchestrator` from
`/home/jovyan/.astro-container-data/astro-orchestrator-runtime`, where `.pi` points to that
writable copy. This uses Pi's normal project settings mechanism: Pi reads project settings from
the process cwd's `.pi/settings.json`.

The stream/runtime contract uses `/home/jovyan/.astro-container-data` for rebuildable container
runtime state and `/session-state/sessions` for active session files.

For the normal parent session, Pi also loads `.pi/APPEND_SYSTEM.md` through the writable runtime
copy instead of locking `/app/.pi/settings.json`.

When `BUILD_AGENTS_IN_BACKEND=1`, Astro uses deterministic `agent_unique_id` values to look up or
create backend Agent records and then works with the backend Agent `id`.

For the HTTP stream path, the wrapper accepts the latest UI turn plus optional UI context, then
builds the Pi prompt from:

- the optional request `system`
- the structured request `context`
- only the last message entry in `messages`
- the existing backend `runtime_session_id`

The backend must create the session before Astro is called. Astro attaches to that existing backend
session and must not start a fresh one from the stream hot path.

If that `runtime_session_id` points to an existing backend `astro-orchestrator` session but the
local Astro wrapper files are missing, the same `/api/chat` request first hydrates the local
session metadata/history wrapper state from the backend session record and then continues the
triggering user turn. The frontend does not need to send a second request or any extra fields.

If that latest user message contains the word `MOCK`, the HTTP stream wrapper returns a synthetic
response immediately for frontend testing and does not invoke the Pi runtime or create session
state.

Conversation continuity comes from the server-side session file keyed by the backend AgentSession id
when registration is enabled (fallback to `threadId` when disabled).
The stream wrapper persists the resolved backend Agent `id` alongside that session and includes it
on every SSE chunk as `agent_id`.
Before a stream chunk is written to the client, the HTTP stream layer appends a normalized
conversation event and rewrites the compact conversation snapshot synchronously so the history
endpoint can read that snapshot directly later.
When the frontend starts a project-scoped executor session directly, it provides the selected
project context such as `projectId` and the checked-out or pinned project `cwd` when needed.
Resume requests can reuse the stored session metadata.

## 2. Before the agent starts

Astro keeps the shared prompt static and avoids auto-injecting repository reference material into
the agent context.

Main Sequence child-process policy is package-provided when the Main Sequence package is configured:

- `tmp_ms_pi/pi/extensions/hooks/project-policy/index.ts` appends child-only policy when the process
  is a child runtime process

## 3. The composed runtime contract decides what to do

The active runtime uses only runtime inputs:

- the user request
- the structured request `context`
- installed Pi package prompts, skills, and extensions
- backend/session/project metadata explicitly supplied by the runtime

Then the composed Astro/package contract decides whether the task is:

- platform help
- an SDK question
- a normal Main Sequence project workflow
- an explicitly requested standalone workflow prompt

## 4. Runtime context tracks project attachment

For a normal Main Sequence project, the active runtime:

1. uses the non-project-attached branch for project selection or creation
   - if the user wants a new project, the runtime loads the project-creation skill and uses it to collect the missing intake before creation
2. uses the project-attached branch when `ASTRO_FIXED_PROJECT_CWD` points at the prepared project
   root
3. keeps session identity backend-owned
   - A2A communication does not transfer session ownership

## 5. Runtime context stays explicit

There is no longer an active `delegate_specialist` tool in the normal project workflow.

For the current creation-first flow:

1. non-project-attached runtime handles project creation or selection
2. project-attached runtime works in the prepared project cwd
3. A2A, when needed, connects backend-owned sessions without changing local runtime context

If another runtime is used in a later phase, communication happens through A2A and backend session
identity remains the source of truth.

## 6. Astro reviews and responds

The active runtime may:

- continue the project workflow
- review status from platform/backend context or A2A results

Then it returns:

- project context or workflow status
- blockers or next actions

Project work should no longer rely on switching the active user session into a separate coder
runtime. If another backend-owned runtime is involved later, Astro communicates with it through A2A.

## Read next

- [`../components/extensions.md`](../components/extensions.md)
- [`../components/agents.md`](../components/agents.md)
- [`../components/prompts.md`](../components/prompts.md)
