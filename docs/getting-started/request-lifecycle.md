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

- `${HOME}/.pi/agent` to `/root/.pi/agent`
- `${HOME}/mainsequence` to `/root/mainsequence`
- `${HOME}/mainsequence-dev` to `/root/mainsequence-dev`

For the normal parent session, Pi also loads `.pi/APPEND_SYSTEM.md`.

When `BUILD_AGENTS_IN_BACKEND=1`, every session start (parent or child) runs a deterministic
Main Sequence CLI registration step to `get-or-create` the agent record.

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

- a normal Main Sequence project workflow
- an Astro-internal review task
- an explicitly requested standalone workflow prompt

## 4. Parent session prepares the project handoff

For a normal Main Sequence project, the parent:

1. authenticates with `mainsequence`
2. creates or opens the project
3. checks it out locally
4. writes the target project's `astro/` files:
   - `astro/brief.md`
   - `astro/tasks.md`
   - `astro/record.md`
   - `astro/status.md`

## 5. Parent delegates to a specialist

The parent calls `delegate_specialist`.

That tool:

1. discovers `.pi/agents/*.md`
2. reads frontmatter
3. appends shared guideline files if the specialist declares them
4. spawns a child `pi` process
5. streams live child progress back to the parent

## 6. Child specialist runs

The child process gets:

- the repo context
- the specialist prompt
- the child-only policy
- an optional different `cwd`, often the checked-out target project

That is how Astro can run a specialist inside another project folder while keeping the parent in the Astro repo.

## 7. Parent reviews and responds

The parent may:

- continue orchestrating
- review status directly using the `astro/` evidence

Then it returns:

- project or workflow status
- the local path
- blockers or next actions

## Read next

- [`../components/extensions.md`](../components/extensions.md)
- [`../components/agents.md`](../components/agents.md)
- [`../components/prompts.md`](../components/prompts.md)
