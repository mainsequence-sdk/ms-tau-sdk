![Main Sequence logo](https://main-sequence.app/static/media/logos/MS_logo_long_black.png)

# Astro

Astro is a parent orchestrator for Main Sequence project assistants. It turns user intent into a concrete Main Sequence project, prepares handoff files in the checked-out project, and launches a coding subagent in that project folder.

This version includes **actual delegate wiring**, not only folder structure:

1. `before_agent_start` injects generated repository documentation context.
2. `.pi/APPEND_SYSTEM.md` provides the static parent orchestrator prompt, while `before_agent_start` only adds child-specialist policy when Astro spawns a child.
3. The main agent can call `delegate_specialist`.
4. `delegate_specialist` discovers `.pi/agents/*.md`, reads frontmatter, spawns a child `pi` process, and can run that child in another project folder via `cwd`.
5. The parent agent can still call `audit_recent_changes` when Astro itself is edited.
6. Astro standardizes on the external `pi-web-access` package for fresh web and documentation context.

## Philosophy

- Build **on top of Pi**, not inside Pi core.
- Keep Astro's repo-local runtime in **TypeScript** and prefer supported external Pi packages over duplicate local wrappers.
- Treat this repository as both:
  - an orchestrator package for Main Sequence project assistants
  - a teaching repository for how to extend Astro

## Main workflow

Astro should normally do this:

1. Read Astro docs and relevant Main Sequence documentation.
2. Translate the user request into a project brief and task list.
3. Authenticate with the Main Sequence CLI.
4. Create the platform project.
5. Set the project up locally.
6. Apply Astro's own `astro/` handoff convention in the checked-out project.
7. Launch `mainsequence-project-coder` as a coding subagent in the project folder.
8. Review progress with `doc-bug-auditor` and summarize what finished or failed.

## Project handoff files

Astro uses its own handoff contract inside the checked-out project's `astro/` folder:

- `astro/brief.md` for the translated user intent, project goal, and acceptance criteria
- `astro/tasks.md` for the prioritized actionable task list, ideally with statuses or checkboxes
- `astro/record.md` for project metadata such as project id, local checkout path, and orchestration notes
- `astro/status.md` for the latest state, evidence checked, blockers or failures, and next actions
  - when blocked or failed, include the exact command or action attempted, the working directory or target path when relevant, the exit code if known, and a traceback, stderr excerpt, or log snippet
  - include concrete identifiers such as a failing file, script, job id, run id, or URL when available
  - include what was already tried and the next recovery step

When Astro delegates implementation inside the checked-out project:

- the target project's `AGENTS.md` should be treated as canonical when it exists
- the target project's `.agents/skills/mainsequence-project/SKILL.md` should also be treated as canonical when it exists
- Astro's `astro/` files still define the requested scope and priorities

## Quick start

```bash
cd astro
npm run pi
```

Pi loads this repository through `.pi/settings.json`, which points to the repo root package and the standard `npm:pi-web-access` community package.

`npm run pi` is the one-command launcher. It installs local npm dependencies if needed, ensures `pi-web-access` is available for this repo, refreshes generated docs context, runs the TypeScript check, and then starts `pi`.

## Required secrets

Astro expects machine-local secrets for the external systems it drives. Do not store these in the repo, `.env` files committed to Git, or the Dockerfile.

Main Sequence login secrets:

- `astro-mainsequence-email`
  - the email passed to `mainsequence login <email>`
- `astro-mainsequence-password`
  - the password used for Main Sequence CLI login

GitHub issue-escalation secrets:

- `astro-github-token`
  - required for duplicate-issue search and opening new issues in `mainsequence-sdk/mainsequence-sdk`
- `astro-github-user`
  - optional metadata for reporting which dedicated GitHub account Astro is expected to use

Notes:

- For the public `mainsequence-sdk` repository, Astro can usually inspect or clone source without GitHub auth.
- The GitHub token is mainly needed for issue search and issue creation.
- Astro should use GitHub REST API as the primary path for duplicate-issue search and issue creation.
- On macOS, store these in Keychain and read them with `security`.
- Use the retrieved PAT directly in the REST request or place it only in a short-lived local shell variable such as `ASTRO_GITHUB_TOKEN`.
- Astro should not look for or use any GitHub credentials outside `astro-github-token` and optional `astro-github-user`.
- Astro should not use generic GitHub environment variables like `GH_TOKEN` or `GITHUB_TOKEN` as an auth source for issue escalation.
- When `doc-bug-auditor` has enough evidence for an upstream SDK issue and no close duplicate exists, it does not need a second user confirmation to open the issue.

Example Keychain setup:

```bash
security add-generic-password -a "$USER" -s astro-mainsequence-email -w 'you@example.com'
security add-generic-password -a "$USER" -s astro-mainsequence-password -w 'your-password'
security add-generic-password -a "$USER" -s astro-github-user -w 'astro-bot'
security add-generic-password -a "$USER" -s astro-github-token -w 'github_pat_...'
```

## What is wired right now

- `extensions/docs-context/`
  - Appends generated repo context to the system prompt at `before_agent_start`.
  - Registers `refresh_docs_index` for regenerating `knowledge/` from inside Astro.
  - `npm run docs:index` is just a CLI wrapper around the same TypeScript implementation.
- `extensions/project-policy/`
  - Appends child-specialist policy at `before_agent_start`.
  - Parent runs use the static prompt in `.pi/APPEND_SYSTEM.md`.
- `extensions/specialist-delegate/`
  - Registers `delegate_specialist`.
  - Discovers project specialists from `.pi/agents/`.
  - Spawns a child `pi` process for each delegated specialist call.
- `extensions/recent-changes/`
  - Tracks files changed by parent-session `write` and `edit` tool calls inside Astro itself.
  - Registers `list_recent_changes` and `audit_recent_changes`.
- External package: `npm:pi-web-access`
  - Provides `web_search`, `fetch_content`, and `get_search_content`.
  - This is the preferred Pi web/browsing package for Astro instead of a repo-local web wrapper.

## Default workflow

For a normal Main Sequence project request:

1. Main agent understands the user intent and defines the target project tasks.
2. Main agent uses the Main Sequence CLI to create and set up the project locally.
3. Main agent writes the `astro/` handoff files in the checked-out project.
4. Main agent calls `delegate_specialist` with `mainsequence-project-coder` and `cwd` set to that project folder.
5. Main agent calls `delegate_specialist` with `doc-bug-auditor` when project status needs review.
6. Main agent returns project metadata, current status, and next steps.

This keeps Astro focused on orchestration while the coding work happens in the target project checkout.

When the review path finds a likely upstream `mainsequence-sdk` execution bug, `doc-bug-auditor` should:

1. inspect the failure evidence and classify whether the SDK is likely involved
2. inspect local package evidence first, then inspect or clone the public `mainsequence-sdk` repository if needed
3. search for duplicate upstream issues
4. open a new issue through GitHub REST API only when the evidence is strong and no close duplicate exists

## Python runtime

The repo root `Dockerfile` is a small Python 3.11 runtime for agent-side Python work.

It installs `uv` and then installs `mainsequence` into the image so Python-based tasks do not have to depend on the host system Python.

The `Dockerfile` defines the image only. To give the container access to the host Main Sequence workspace, run it with a bind mount such as:

```bash
docker build -t astro-python .
docker run --rm -it \
  -e MAINSEQUENCE_USER="$USER" \
  -v "$PWD:/workspace/astro" \
  -v "$HOME/mainsequence:/Users/$USER/mainsequence" \
  -w /workspace/astro \
  astro-python bash
mainsequence settings set-base "/Users/$MAINSEQUENCE_USER/mainsequence"
```

The important part is that you mount the whole host `~/mainsequence` root, not an individual organization subfolder, so `mainsequence` can keep creating and using `/Users/<user>/mainsequence/<org>/...` inside the container.

## Future implementation

Planned infrastructure direction:

- Astro itself can later run as a control-plane container with its own Node, Python, Pi, and Main Sequence CLI environment.
- The host machine can mount the local Main Sequence projects directory into that Astro container.
- Each newly created Main Sequence project should run in its **own independent Docker environment**, separate from Astro and separate from other generated projects.

That separation is important because Astro is the orchestrator, while each generated project should keep its own runtime, dependencies, and container lifecycle.

## Standard web access

Astro does not ship a custom repo-local web extension anymore.

Instead, it standardizes on [`pi-web-access`](https://www.npmjs.com/package/pi-web-access), which is currently the most adopted Pi web-access package we found and gives Astro:

- `web_search` for current web research
- `fetch_content` for page, repo, PDF, and URL extraction
- `get_search_content` for retrieving stored search/fetch results

The package is referenced in `.pi/settings.json`, and the local install cache lives under `.pi/npm/`, which should stay out of Git.

## Customize first

Start here if you want fast changes:

- change the parent routing rules in `.pi/APPEND_SYSTEM.md`
- change specialist behavior in `.pi/agents/*.md`
- change tool behavior in `extensions/*/index.ts`
- add shared helpers in `extensions/shared/`
- update teaching docs in `tutorial/`

## Tutorial path

Read these in order:

1. `tutorial/00-start-here.md`
2. `tutorial/01-what-loads-when.md`
3. `tutorial/02-before-agent-start.md`
4. `tutorial/03-specialists-and-routing.md`
5. `tutorial/04-agent-files-and-frontmatter.md`
6. `tutorial/05-typescript-runtime.md`

## Important note

Astro uses a split prompt model on purpose. The parent runtime prompt lives in `.pi/APPEND_SYSTEM.md`, while current-turn docs context and child-specialist policy are added through extensions.


## Dev note

`types/pi-stubs.d.ts` is only there to make local `tsc --noEmit` checks ergonomic before the real Pi packages are present. When you install the actual Pi dependencies, those real package types take over.
