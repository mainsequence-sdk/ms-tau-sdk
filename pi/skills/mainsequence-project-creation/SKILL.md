---
name: mainsequence-project-creation
description: Guidance for turning a user's idea into a concrete Main Sequence project blueprint, component plan, and creation-ready scope before running project creation commands.
---

# Main Sequence Project Creation

Use this skill to turn a human research idea into a self-sufficient Main Sequence build plan.

## When to use

- Always use this skill before `mainsequence project create`.
- Use it when the user has research intent but not yet a concrete Main Sequence design.
- Use it when a request mixes data acquisition, analytics, APIs, dashboards, assets, jobs, or reusable platform resources.

## Mandatory first artifact

Before proposing project creation or implementation, create a temporary file named exactly:

`project_blueprint.md`

This file must be self-sufficient for another Main Sequence agent to read and implement without needing the original chat.

Use the template in [references/project_blueprint_template.md](./references/project_blueprint_template.md).

## Required workflow

1. Create or update `project_blueprint.md`.
2. Run the discovery questions below.
3. Translate each answer into explicit `tasks[]`.
4. Search the Main Sequence platform for reusable resources before proposing new ones.
5. Map the intent into platform components.
6. Only after the project blueprint is coherent should you create the project or start implementation.
7. Resolve the GitHub organization for the new project with the command contract below.
8. Create the project with `mainsequence project create "<name>" --github-org-id <githubOrgId>`.
9. Report the created project id, project name, and blueprint summary. Do not set up a local checkout
   in the orchestrator runtime.

## Operational rules

- Never ask the user to run `mainsequence login` or any other manual auth command.
- If a Main Sequence CLI command fails with auth during project creation, call `ensure_mainsequence_cli_auth` once and retry the blocked command before treating it as a runtime failure. Do not call `ensure_mainsequence_cli_auth` for non-auth failures.
- For every non-auth Main Sequence CLI failure, follow the global Main Sequence CLI failure contract.
- Do not switch sessions and do not hand off to another agent as part of this flow.
- Do not retry project creation with guessed flags or alternate interactive paths after a non-auth failure. Stop and report the missing command, missing id, or exact backend/CLI error.

Do not create the project first and ask questions later.

## GitHub organization selection

Before calling `mainsequence project create`, always run:

```bash
mainsequence organization github-organizations --json
```

Use the returned GitHub organization id for project creation:

```bash
mainsequence project create "<name>" --github-org-id <githubOrgId>
```

Rules:

- If exactly one GitHub organization is returned, use that organization id without asking the user
  for confirmation.
- If more than one GitHub organization is returned, ask the user to choose which organization should
  own the new project before creating it.
- If no GitHub organization is returned, stop and report that project creation cannot continue
  because no GitHub organization is available.
- If `mainsequence organization github-organizations --json` is unavailable, exits nonzero, or returns
  `No such command`, stop and report the failure using the global Main Sequence CLI failure contract.
  Do not call `mainsequence project create`.
- Do not call `mainsequence project create "<name>"` without `--github-org-id`.

## Discovery flow

### 1. Data acquisition

The agent must determine where the data comes from.

- Ask the user whether they already know the source.
- Load DataNode and SimpleTable context first.
- Prefer existing platform resources over building duplicates.

If the user already knows the source:

- decide whether the source should land in a `DataNode`, `SimpleTable`, or `Artifact`
- create implementation tasks for ingestion and storage

If the user does not know the source:

- search Main Sequence first for reusable `DataNode`s and `SimpleTable`s
- ask whether the user wants deep internet research for candidate sources
- prefer sources that can be obtained programmatically through APIs, files, or repeatable downloads
- discourage one-off manual page scraping or brittle document extraction at this stage

### 2. Intelligence type

Ask what kind of intelligence the user wants to build.

If the user only wants to visualize data:

- go directly to the visualization section

If the user wants analytics, signals, transformations, automation, or reusable logic:

- translate intent into coding tasks under `src/`
- load the project builder skill when available

### 3. Analysis and visualization surface

Ask how the user wants to analyze and visualize the result.

If the user chooses Streamlit:

- use the Streamlit dashboard skill when available
- otherwise read the Streamlit docs referenced in [references/mainsequence_component_map.md](./references/mainsequence_component_map.md)

If the user chooses Command Center:

- use the Command Center skills when available
- otherwise read the Command Center docs referenced in [references/mainsequence_component_map.md](./references/mainsequence_component_map.md)
- include API tasks because Command Center widgets usually need an application surface

### 4. API surface

If the workflow needs reusable app access, widget feeds, or agent consumption:

- add FastAPI tasks
- define endpoint contracts
- define request user context middleware when needed
- define widget contract responses when the API feeds Command Center widgets directly

## Component selection rules

Do not reduce Main Sequence to only assets and jobs. Select from the real platform surface.

Read [references/mainsequence_component_map.md](./references/mainsequence_component_map.md) and choose the smallest correct set of components:

- `Project`
- `DataNode`
- `SimpleTable` and `SimpleTableUpdater`
- `APIDataNode`
- `Job`, `Schedule`, `JobRun`, `ProjectImage`
- `Artifact` and `Bucket`
- `Constant` and `Secret`
- `Asset`, `AssetCategory`, translation tables, pricing details, instruments
- `Workspace`, registered widget types, AppComponent widgets, forms, widget data contracts
- `FastAPI` application surfaces
- `ProjectResource` and `ResourceRelease`
- Streamlit dashboards
- Virtual Fund Builder components when portfolio construction is required

## Search before build

Before creating new components, search for existing reusable resources.

Typical discovery commands:

- `mainsequence organization project-names`
- `mainsequence project validate-name "Candidate Name"`
- `mainsequence data-node search "user intent"`
- `mainsequence data-node list`
- `mainsequence simple_table list`
- `mainsequence constants list`
- `mainsequence secrets list`
- `mainsequence cc registered_widget_type list`
- `mainsequence project project_resource list`
- `mainsequence markets asset-translation-table list`

If a companion skill exists for a component, load it.
If the companion skill does not exist, use the SDK docs directly.

## Task writing rules

Every major answer in `project_blueprint.md` must include a `tasks[]` list.

Each task must:

- start with an imperative verb such as `Build`, `Create`, `Register`, `Add`, `Expose`, `Schedule`, or `Deploy`
- name the exact Main Sequence component
- name the source system or business object
- state the expected output or contract
- include important configuration dimensions when they affect identity or scope
- be specific enough that another agent can implement it without guessing

Good example:

- `Build a DataNode that gets daily prices from massive.com and expose a storage_hash dimension for frequency.`
- `Create a SimpleTableUpdater for issuer metadata with a unique issuer_code index and foreign-key links to portfolio rows.`
- `Expose a FastAPI endpoint that returns DataNodeTableSourceInputResponse for the funding curve widget.`

Bad example:

- `Add market data`
- `Create dashboard`
- `Build API`

## Decision rules

- Use `DataNode` for structured, incremental, queryable datasets, especially time series.
- Use `SimpleTable` for master data, reference entities, mappings, and app-facing relational rows.
- Use `Artifact` when the natural unit is still a file.
- Use `Asset` and pricing details when the workflow needs stable market identities or priceable instruments.
- Use `Constant` and `Secret` for runtime configuration, not hardcoded values.
- Use `Job` plus schedules for execution; separate recurring jobs from ad-hoc runs.
- Use `FastAPI` when the system needs an application contract, aggregation layer, or widget-facing endpoint.
- Use Streamlit for a simpler app surface.
- Use Command Center for reusable workspace widgets, forms, and shared application composition.
- Use `ResourceRelease` when the result must be deployed as a reusable platform resource.

## Output requirements

`project_blueprint.md` must contain:

- overall intent description
- assumptions
- unanswered questions
- chosen platform components
- answers to each discovery section
- `tasks[]` under each discovery section
- implementation order
- explicit notes on what should be reused vs newly built

The file should be implementation-oriented, not a brainstorm.

## References to load as needed

- For the project blueprint file structure: [references/project_blueprint_template.md](./references/project_blueprint_template.md)
- For platform component mapping and relevant SDK docs: [references/mainsequence_component_map.md](./references/mainsequence_component_map.md)
