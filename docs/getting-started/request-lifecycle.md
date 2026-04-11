# Request lifecycle

This is the easiest way to understand Astro end to end.

## 1. Startup

Pi discovers `.pi/settings.json` and loads:

- local extensions
- local prompts
- local skills
- the repository package itself
- repo-installed packages such as `pi-web-access`

The Astro launch scripts also load `.env` from the repo root and start a Main Sequence token
refresh loop when `MAINSEQUENCE_TOKEN_REFRESH_INTERVAL_SECONDS` is set.

The deployable Docker targets boot the same runtime by copying only:

- `.pi/`
- `pi/`
- `interface/`
- `scripts/`
- `package.json`
- `package-lock.json`
- `tsconfig.json`

`docker-compose.yml` starts those same targets while bind-mounting only:

- `${HOME}/.pi/agent` to `/root/.pi/host-agent`
- `${HOME}/mainsequence` to `/root/mainsequence`
- `${HOME}/mainsequence-dev` to `/root/mainsequence-dev`

For the normal parent session, Pi also loads `.pi/APPEND_SYSTEM.md`.

When `BUILD_AGENTS_IN_BACKEND=1`, Astro uses deterministic `agent_unique_id` values to look up or
create backend Agent records and then works with the backend Agent `id`.

For the HTTP stream path, the wrapper accepts the latest UI turn plus optional UI context, then
builds the Pi prompt from:

- the optional request `system`
- the structured request `context`
- only the last message entry in `messages`
- the optional `newChat: true` flag (used as a UI hint for a new conversation)

Conversation continuity comes from the server-side session file keyed by the backend agent
unique id plus a session suffix when registration is enabled (fallback to `threadId` when disabled).
The stream wrapper persists the resolved backend Agent `id` alongside that session and includes it
on every SSE chunk as `agent_id`.

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

1. translates the request into a short brief, task list, and acceptance criteria
2. decides whether to select an existing project or create a new one
   - if the user wants a new project, the parent loads the project-creation skill and uses it to collect the missing intake before creation
3. runs `mainsequence project set-up-locally <id>` after the project id is known
4. resolves the checked-out local path
5. prepares any needed project-local task or status context for the checked-out project
   - use the target project's own instructions, planning files, and status files when they exist
   - do not assume an Astro-owned `astro/` file contract by default

## 5. Parent delegates to a specialist

The parent calls `delegate_specialist`.

That tool:

1. discovers `.pi/agents/*.md`
2. reads frontmatter
3. appends shared guideline files if the specialist declares them
4. spawns a child `pi` process
5. passes the checked-out target `cwd` and selected `projectId` when required
6. streams live child progress back to the parent

## 6. Child specialist runs

The child process gets:

- the repo context
- the specialist prompt
- the child-only policy
- the checked-out target project `cwd` when delegated for implementation
- the selected Main Sequence project id when the specialist requires it

That is how the parent can run a specialist inside another project folder while keeping the parent in its own working directory.

## 7. Parent reviews and responds

The parent may:

- continue orchestrating
- review status directly using the checked-out project's available evidence

Then it returns:

- project context or workflow status
- the local path
- blockers or next actions

## Read next

- [`../components/extensions.md`](../components/extensions.md)
- [`../components/agents.md`](../components/agents.md)
- [`../components/prompts.md`](../components/prompts.md)
