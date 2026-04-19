# Request lifecycle

This is the easiest way to understand Astro end to end.

## 1. Startup

Pi discovers `.pi/settings.json` and loads:

- local extensions
- local prompts
- local skills
- the repository package itself
- repo-installed packages such as `pi-web-access`

The Astro launch scripts also load `.env` from the repo root, refresh the Main Sequence access
token, run a deterministic `mainsequence login --access-token ...` bootstrap before any agent work
starts, and then start a Main Sequence token refresh loop when
`MAINSEQUENCE_TOKEN_REFRESH_INTERVAL_SECONDS` is set.
For the HTTP stream path, Astro also re-runs that deterministic CLI login gate before each real
`POST /api/chat` request so Pi never starts from a stale unauthenticated CLI state.

The deployable Docker targets boot the same runtime by copying only:

- `.pi/`
- `pi/`
- `interface/`
- `scripts/`
- `package.json`
- `package-lock.json`
- `tsconfig.json`

`docker-compose.yml` starts those same targets while bind-mounting only:

- `./.astro` to `/app/.astro-migration-source` as a read-only migration source
- `${HOME}/.pi/agent` to `/root/.pi/host-agent` as a read-only migration source for `auth.json`
  and `sessions/`
- named volume `astro_container_data` to `/root/.astro-container-data`

For the HTTP stream service, compose also sets
`ASTRO_MAINSEQUENCE_CONFIG_DIR=/root/.astro-container-data/.config/mainsequence`,
`PI_CODING_AGENT_DIR=/root/.astro-container-data/.pi/agent`, and
`ASTRO_STREAM_SESSION_DIR=/root/.astro-container-data/.astro/stream-sessions` so the named volume
acts like the deployment-time PVC.

At startup, Astro also symlinks:

- `/root/.pi/agent` -> `/root/.astro-container-data/.pi/agent`
- `/root/.config/mainsequence` -> `/root/.astro-container-data/.config/mainsequence`
- `/root/.astro/stream-sessions` -> `/root/.astro-container-data/.astro/stream-sessions`
- `/root/mainsequence` -> `/root/.astro-container-data/mainsequence`
- `/root/mainsequence-dev` -> `/root/.astro-container-data/mainsequence-dev`
- `/root/.local/share/uv` -> `/root/.astro-container-data/uv`

For the normal parent session, Pi also loads `.pi/APPEND_SYSTEM.md`.

When `BUILD_AGENTS_IN_BACKEND=1`, Astro uses deterministic `agent_unique_id` values to look up or
create backend Agent records and then works with the backend Agent `id`.

For the HTTP stream path, the wrapper accepts the latest UI turn plus optional UI context, then
builds the Pi prompt from:

- the optional request `system`
- the structured request `context`
- only the last message entry in `messages`
- the optional `newChat: true` flag (used as a UI hint for a new conversation)

If the request also includes `runtime_session_id`, that explicit session id wins and the wrapper
resumes the existing backend session instead of starting a fresh one.

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
When the frontend starts a project-scoped `mainsequence-project-coder` session, it must provide the
selected `projectId` and checked-out project `cwd` on the first `newChat: true` request. Resume
requests can reuse the stored session metadata.

## 2. Before the agent starts

Astro keeps the parent prompt static and avoids auto-injecting repo docs into the agent context.

The only runtime policy injection that remains is for child specialists:

- `project-policy` appends child-only policy when the process is a delegated specialist

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

## 4. Parent session prepares the project handoff

For a normal Main Sequence project, the parent:

1. decides whether to select an existing project or create a new one
   - for an existing project, the parent treats "work on/open this project" as selection and setup, not automatic task intake
   - if the user wants a new project, the parent loads the project-creation skill and uses it to collect the missing intake before creation
2. runs `tsx /app/scripts/mainsequence_project_set_up_locally.ts <id>` after the project id is known
   - inside Astro, the parent should not call raw `mainsequence project set-up-locally <id>` directly
3. queries the project details with the CLI and waits until `is_initialized=true` before treating the checkout as ready
   - the parent must not hand off or copy `project_blueprint.md` before that readiness check passes
4. resolves the checked-out local path
5. prepares any needed project-local task or status context for the checked-out project
   - use the target project's own instructions, planning files, and status files when they exist
   - do not assume an Astro-owned `astro/` file contract by default
6. either delegates a bounded background task or hands off into a project-scoped coding session
   - when the active conversation should move into the checked-out project, the parent calls `switch_project_session`
   - when there is no concrete implementation task yet, the handoff should establish project-local context and readiness instead of asking the user to restate a first task

## 5. Parent delegates to a specialist

The parent calls `delegate_specialist`.

That tool:

1. discovers `.pi/agents/*.md`
2. reads frontmatter
3. appends shared guideline files if the specialist declares them
4. spawns a child `pi` process
5. passes the checked-out target `cwd` and selected `projectId` when required
6. streams live child progress back to the parent

The HTTP stream layer also supports:

- starting `mainsequence-project-coder` directly as its own backend Agent session when the frontend already knows the selected `projectId` and checked-out project `cwd`
- creating that coder session in response to a structured `switch_project_session` handoff from the orchestrator
- continuing that same handoff response as the new coder session so the frontend can see bootstrap progress without a second user message
- running a deterministic project-runtime bootstrap inside the new `mainsequence-project-coder` session before Pi starts:
  - `mainsequence project sdk-status --path . --json`
  - `mainsequence project build_local_venv --path .`
  - `uv sync`
  - activation of the checked-out project's `.venv`
  - emitting those bootstrap steps as synthetic tool events so the frontend can see them

## 6. Child specialist runs

The child process gets:

- the repo context
- the specialist prompt
- the child-only policy
- the checked-out target project `cwd` when delegated for implementation
- the selected Main Sequence project id when the specialist requires it
- the checked-out project's activated `.venv` when the active agent is `mainsequence-project-coder`
- the deterministic project-runtime bootstrap summary in prompt context when the active agent is `mainsequence-project-coder`

That is how the parent can run a specialist inside another project folder while keeping the parent in its own working directory.

## 7. Parent reviews and responds

The parent may:

- continue orchestrating
- review status directly using the checked-out project's available evidence

Then it returns:

- project context or workflow status
- the local path
- blockers or next actions

When the parent switches into a project session, the stream emits a short user-facing handoff
message and then a structured `session_switch` chunk instead of relying on plain-text narration
alone. After that handoff, the same response can continue immediately with the coder session's
environment verification and bootstrap steps.

## Read next

- [`../components/extensions.md`](../components/extensions.md)
- [`../components/agents.md`](../components/agents.md)
- [`../components/prompts.md`](../components/prompts.md)
