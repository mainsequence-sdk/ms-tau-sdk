# Request lifecycle

This is the easiest way to understand Astro end to end.

## 1. Startup

Pi discovers `.pi/settings.json` and loads:

- local extensions
- local prompts
- local skills
- the repository package itself
- repo-installed packages such as `pi-web-access`

The Astro launch scripts load `.env` from the repo root and prepare Main Sequence auth according
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
- `scripts/`
- `package.json`
- `package-lock.json`
- `tsconfig.json`

`docker-compose.yml` starts those same targets while bind-mounting only:

- editable source paths such as `./.pi`, `./pi`, `./interface`, and `./scripts`
- tmpfs-backed `astro_session_emptydir` volume at `/session-state` for pod-local session files

For the HTTP stream service, compose also sets
`ASTRO_MAINSEQUENCE_CONFIG_DIR=/home/appuser/.astro-container-data/.config/mainsequence`,
`PI_CODING_AGENT_DIR=/home/appuser/.astro-container-data/.pi/agent`, and
`ASTRO_STREAM_SESSION_DIR=/session-state/sessions` so local Docker follows the emptyDir session model.
Astro also materializes repo-local `/app/.pi` into
`/home/appuser/.astro-container-data/.pi/project` and runs `astro-orchestrator` from
`/home/appuser/.astro-container-data/astro-orchestrator-runtime`, where `.pi` points to that
writable copy. This uses Pi's normal project settings mechanism: Pi reads project settings from
the process cwd's `.pi/settings.json`.

The stream/runtime contract uses `/home/appuser/.astro-container-data` for rebuildable container
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

Astro keeps the parent prompt static and avoids auto-injecting repo docs into the agent context.

The only runtime policy injection that remains is for runtime-owned child processes:

- `project-policy` appends child-only policy when the process is a child runtime process

## 3. Parent session decides what to do

The parent agent reads:

- the user request
- Astro docs context
- relevant Main Sequence docs

Then it decides whether the task is:

- platform help
- an SDK question
- a normal Main Sequence project workflow
- an Astro-internal review task
- an explicitly requested standalone workflow prompt

## 4. Parent session prepares the created project

For a normal Main Sequence project, the parent:

1. decides whether to select an existing project or create a new one
   - for an existing project, the parent treats "work on/open this project" as selection and setup, not automatic task intake
   - if the user wants a new project, the parent loads the project-creation skill and uses it to collect the missing intake before creation
2. after project creation succeeds, runs `tsx /app/scripts/mainsequence_project_finalize_creation.ts <id>`
   - `mainsequence project create` already waits until `is_initialized=true`
   - the finalize helper sets the project up locally, resolves the checked-out path, copies
     `project_blueprint.md` into the project root, and prints the exact signed-terminal git steps
   - before committing or pushing the project checkout, open a signed terminal with
     `mainsequence project open-signed-terminal <id>`
   - run the printed `git add`, `git commit`, and `git push` commands inside that signed terminal
3. prepares any needed project-local task or status context for the checked-out project
   - use the target project's own instructions, planning files, and status files when they exist
   - do not assume an Astro-owned `astro/` file contract by default
   - for existing projects, do not invent or require `project_blueprint.md` by default
4. continues orchestration without any session switch
   - the active user conversation stays in the orchestrator session
   - if `mainsequence-project-executor` is used later, that communication is A2A-only and does not transfer session ownership
   - when there is no concrete implementation task yet, the orchestrator should establish project-local context and readiness instead of asking the user to restate a first task

## 5. Orchestrator stays active

There is no longer an active `delegate_specialist` tool in the normal project workflow.

For the current creation-first flow:

1. the orchestrator selects or creates the project
2. the orchestrator finalizes the local checkout with `tsx /app/scripts/mainsequence_project_finalize_creation.ts <id>`
3. the orchestrator keeps the active user conversation
4. the workflow stops after `project_blueprint.md` is copied and the signed-terminal commit/push instructions are ready

If executor is used in a later phase, the orchestrator communicates with it through A2A and stays
the owner of the user-facing conversation.

## 6. Parent reviews and responds

The parent may:

- continue orchestrating
- review status directly using the checked-out project's available evidence

Then it returns:

- project context or workflow status
- the local path
- blockers or next actions

Project work should no longer rely on switching the active user session into a separate coder
runtime. For the current new-project flow, the orchestrator remains the user-facing session and
uses `tsx /app/scripts/mainsequence_project_finalize_creation.ts <id>` to persist the creation
blueprint into the initialized checkout, then uses `mainsequence project open-signed-terminal <id>`
for the actual git commit/push step. If executor is involved later, the orchestrator communicates
with it through A2A and remains the owner of the user-facing conversation.

## Read next

- [`../components/extensions.md`](../components/extensions.md)
- [`../components/agents.md`](../components/agents.md)
- [`../components/prompts.md`](../components/prompts.md)
