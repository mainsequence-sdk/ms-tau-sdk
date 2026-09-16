# tdag-django Cross-Repository Inventory

Status: Read-only input for the later coordinated platform phases

Date: 2026-09-16

Repository inspected: `/Users/jose/code/MainSequenceServerSide/tdag-django`

No Django files were modified from the Astro workspace.

## Summary

The current Django implementation encodes the retired ontology in models, constants, deployment
runs, image relations, build inputs, runtime specs, endpoints, migrations, tests, and documentation.
The change is not isolated to swapping `astro-stream` for `ms-tau`.

The backend currently has:

- two fixed agent types: `astro-orchestrator` and `code-repository-executor`;
- two typed service models: `UserOrchestratorAgentService` and
  `UserCodeRepositoryExecutorAgentService`;
- two deployment-run types: `UserOrchestratorRun` and `CodeRepositoryExecutorRun`;
- a second runtime-image model, `CodeRepositoryExecutorRuntimeImage`, relating the project image
  to an executor-bundle image;
- runtime specs selected by `(agent_type, harness)`;
- separate orchestrator and executor environment dictionaries;
- a CodeRepository Executor automatic deployment service that resolves both base and executor
  bundle images;
- build submission that injects `EXECUTOR_BUNDLE_IMAGE` for executor runtime images;
- Knative launch code that selects the service's related job image and repository working
  directory; and
- runtime credential secrets injected as `MAINSEQUENCE_RUNTIME_CREDENTIAL_ID` and
  `MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET`.

## Exact Ownership Points

### Role constants and image catalog identities

`timeseries_orm/agents/constants.py` defines:

```text
AGENT_TYPE_ASTRO_ORCHESTRATOR = "astro-orchestrator"
AGENT_TYPE_CODE_REPOSITORY_EXECUTOR = "code-repository-executor"
CODE_REPOSITORY_EXECUTOR_SERVICE_KEY = "code-repository-executor"
AGENT_ORCHESTRATOR_IMAGE_SPEC = astro/astro-tau:latest
CODE_REPOSITORY_EXECUTOR_BUNDLE_IMAGE_SPEC = astro/code-repository-executor-bundle:latest
```

Disposition: eliminate both agent-type/runtime-role values and both Astro catalog images. Retain a
normal workspace-bound service identity and direct project image relation in the replacement
backend ADR.

### Service and deployment models

`timeseries_orm/agents/models/infrastructure.py` contains:

- `UserOrchestratorAgentService`, keyed to a user and requiring the orchestrator agent type;
- `UserCodeRepositoryExecutorAgentService`, uniquely keyed to `CodeRepositoryBranch` and requiring
  the executor agent type;
- `AstroOrchestratorServiceRetirement` and `AstroOrchestratorEnvironmentBootstrap` rollout state;
- `CodeRepositoryExecutorRun` and `UserOrchestratorRun`; and
- `CodeRepositoryExecutorRuntimeImage`, with `code_repository_input_image` and
  `executor_bundle_image` foreign keys.

Disposition: design a new workspace-bound service and deployment-run schema. The branch uniqueness,
organization/environment ownership, verified commit, and runtime linkage are candidate contracts to
re-adopt. User-scoped no-workspace service models and executor-bundle image relations are eliminated.
Existing rollout/audit data needs an explicit archive or migration policy before model deletion.

### Runtime specification and environment selection

`timeseries_orm/agents/services/shared.py` defines:

- `TAU_ASTRO_ORCHESTRATOR_RUNTIME_SPEC` with command `("astro-stream",)`;
- `TAU_CODE_REPOSITORY_EXECUTOR_RUNTIME_SPEC` with command `("astro-stream",)`;
- `CODING_AGENT_RUNTIME_SPECS` keyed by legacy agent type and harness;
- `TAU_RUNTIME_ENV` with the `ASTRO_*` service/settings contract;
- `TAU_CODE_REPOSITORY_EXECUTOR_RUNTIME_ENV` with fixed workspace and project-extension enablement;
  and
- `ASTRO_AGENT_EXECUTION_SNAPSHOT` injection for Tau sessionless responses.

Disposition: replace role/harness selection with one workspace-bound TAU SDK service contract.
Replace `astro-stream` with the project-installed `ms-tau`, remove the extension enable flag, rename
accepted SDK settings, preserve stable `MAINSEQUENCE_*` credentials, and separately disposition the
sessionless execution snapshot contract.

### Two-stage executor image build

`timeseries_orm/agents/services/code_repository_executor_auto_deploy.py`:

- resolves the CodeRepository base image;
- resolves the central executor-bundle image;
- records both as `DeploymentRunImageDependency` roles;
- creates a build-and-deploy intent with target kind `code-repository-executor`; and
- validates drift against the persisted executor-bundle relationship.

`timeseries_orm/tdag/pod_manager/services/code_repositories/images.py`:

- treats `code_repository_executor` as a separate image kind;
- loads `CodeRepositoryExecutorRuntimeImage.executor_bundle_image`;
- requires that bundle to be digest pinned; and
- submits `EXECUTOR_BUNDLE_IMAGE=<pinned-uri>` as a Docker build argument.

Disposition: remove the executor runtime-image kind, bundle dependency role, bundle drift, second
Dockerfile source, relational bundle FK, and `EXECUTOR_BUNDLE_IMAGE` injection. The replacement
build is the normal project image build with `ms-tau-sdk` in the project lock.

### Direct Knative deployment seam that can be retained

`timeseries_orm/tdag/pod_manager/services/runtime/deployments.py` already provides a useful direct
deployment primitive:

- `run_knative_coding_agent_service` resolves the service's related job image;
- `resolve_provider_native_coding_agent_image_uri` can select the directly deployable image;
- the repository working directory is selected when the service has a
  `code_repository_branch_id`;
- health probes, resource/security settings, secret environment, labels, and autoscaling are
  applied without requiring the SDK to own an image; and
- the final call is `run_knative_service_from_base_job` with the selected runtime image.

This seam should be amended rather than replaced wholesale. The target path supplies the normal
project image, requires the workspace branch, and commands that image to run `ms-tau`. The current
function uses `base_job.command`; the migration must make command ownership explicit and test that
the deployed command is `ms-tau` from `/workspace`.

### Runtime credentials

`timeseries_orm/tdag/pod_manager/services/runtime/knative/credentials.py` owns:

```text
MAINSEQUENCE_RUNTIME_CREDENTIAL_ID
MAINSEQUENCE_RUNTIME_CREDENTIAL_SECRET
MAINSEQUENCE_AUTH_MODE=runtime_credential
```

It creates/rotates Kubernetes secrets, injects secret references, enforces deployment transaction
boundaries, and exchanges credentials for bounded access tokens.

Disposition: re-adopt. Credential scope and target validation must be updated to the new
workspace-bound service model, but these stable `MAINSEQUENCE_*` names and secret-injection
mechanics remain the deployment authentication contract.

## Primary Django Code Families Requiring Disposition

| Family | Key paths | Initial disposition |
| --- | --- | --- |
| Agent ontology | `agents/constants.py`, `models/infrastructure.py`, `models/agent_sessions.py` | Replace legacy types/models with workspace-bound service ownership. |
| Runtime resolution | `agents/services/shared.py`, `runtime_update.py`, `redeployment_policies.py` | Major amendment around project-selected SDK/image composition. |
| Executor orchestration | `agents/services/code_repository_executor.py`, `code_repository_executor_auto_deploy.py` | Replace names and delete bundle/overlay stages; retain useful branch/service orchestration. |
| API/serialization | `agents/views.py`, `serializers.py`, `custom_schemas.py` | Redesign endpoints and schemas without two role concepts or executor image fields. |
| Image building | `pod_manager/services/code_repositories/images.py`, dependency preflight/distribution/catalog mirrors | Remove executor bundle relation and make the normal project image directly runnable. |
| Deployment runs | `pod_manager/models/deployment_runs.py`, `services/deployment_runs.py`, `deployment_pipelines.py` | Amend target types and image dependency roles. |
| Knative launch | `pod_manager/services/runtime/deployments.py` | Re-adopt direct project image, workspace, probes, security, and secret injection; command becomes `ms-tau`. |
| Runtime credentials | `pod_manager/services/runtime/knative/credentials.py` | Re-adopt with new target model. |
| Migrations/data | `agents/migrations/*`, `pod_manager/migrations/*` | Add explicit forward migration/archive plan; never rewrite applied history. |
| Tests | `agents/tests/*`, `pod_manager/tests/*`, MCP gateway tests | Classify portable contracts versus legacy-role/bundle tests before rewriting. |
| Documentation/ADRs | `docs/agents/*`, `docs/tdag/pod_manager/*`, `docs/index.yml` | New coordinated Django ADR; amend active docs and archive legacy ontology. |

## Known Django ADRs Requiring Coordinated Disposition

At minimum:

- Agents ADR 001 — CodeRepository Executor Coding Agent Service;
- Agents ADR 002 — CodeRepository Executor Image Build Path;
- Agents ADR 003 — CodeRepository Executor Two-Step Runtime Workflow;
- Agents ADR 005 — unified runtime access serializer with executor-specific behavior;
- Agents ADR 010 — executor bundle auto-bump;
- Agents ADR 011 — agent type/runtime identity;
- Agents ADR 012 — automatic executor deployment;
- Agents ADR 013 — no-clone SDK preflight;
- Agents ADR 016 — direct runtime A2A communication;
- Agents ADR 023 — reconciliation behavior;
- Agents ADR 031 — CodeRepository Tau extension tools;
- Pod Manager ADR 008 — runtime monitoring with typed orchestrator/executor services;
- Pod Manager ADR 047 — lean Python ABI/image cutover; and
- platform ADR 0008 — Astro orchestrator session-handle policy.

These are migration inputs, not automatically accepted dependencies of the new SDK.

## Django Test Families Requiring T0 Classification

The initial search found affected tests across:

- agent API, serializers, sessions, signals, runtime services, summaries, harness selection,
  provider credentials, automatic deployment, and Astro cutover;
- CodeRepository image source/build/deletion and relational image lifecycle;
- deployment runs, catalog mirrors, cloud tenancy, runtime monitoring, and Knative deployment;
- runtime credential creation/injection/exchange; and
- MCP protocol catalog, tools, and A2A sender behavior.

Portable contracts likely include runtime credential exchange, provider control, A2A/session wire
behavior, Knative secret handling, and direct project-image deployment primitives. Tests whose sole
assertion is a legacy type, bundle image, overlay build, or orchestrator service are migration-only
or deleted after cutover.

## Later Platform-Cutover Gaps Remaining Outside Both Repositories

- Inventory every project repository that currently relies on injected Astro rather than declaring
  an SDK dependency.
- Identify the Python package index, publication credentials, provenance/signing, and release
  owner for `ms-tau-sdk`.
- Inventory frontend/client assumptions about the two agent types and typed service endpoints.
- Inventory dashboards, alerts, billing, and analytics keyed by Astro images or legacy role names.
- Decide the explicit archive/migration outcome for existing no-workspace agents and sessions.
