# Main Sequence Component Map

Use this file to translate intent into the correct SDK component set.

## Core project setup

- `Project`: top-level execution and repository boundary.
- `ProjectImage`: frozen executable image for reproducible jobs.
- `Job`, `Schedule`, `JobRun`: execution layer for manual runs, recurring tasks, and operational inspection.

Primary docs:

- [Part 1 — Setting a Project (CLI)](https://mainsequence-sdk.github.io/mainsequence-sdk/tutorial/setting_a_project/)
- [Scheduling Jobs](https://mainsequence-sdk.github.io/mainsequence-sdk/knowledge/infrastructure/scheduling_jobs/)

## Data production and storage

- `DataNode`: core unit for structured, incremental, queryable data products.
- `storage_hash`: dataset identity.
- `update_hash`: updater-job identity.
- `APIDataNode`: read published DataNode output from APIs and apps.
- `SimpleTable`: non-time-series schema model for master/reference/application rows.
- `SimpleTableUpdater`: backend owner for a simple table.
- `Artifact` and `Bucket`: durable file storage for raw inputs and generated outputs.

Primary docs:

- [Data Nodes](https://mainsequence-sdk.github.io/mainsequence-sdk/knowledge/data_nodes/)
- [Simple Table](https://mainsequence-sdk.github.io/mainsequence-sdk/knowledge/simple_tables/simple_table/)
- [Artifacts](https://mainsequence-sdk.github.io/mainsequence-sdk/knowledge/infrastructure/artifacts/)

Decision rule:

- choose `DataNode` for time-series or incremental analytical tables
- choose `SimpleTable` for entities, mappings, and reference data
- choose `Artifact` when the natural unit is a file

## Application surfaces

- `FastAPI`: request/response application layer
- `LoggedUserContextMiddleware`: attach resolved Main Sequence user to `request.state`
- widget-facing response contracts under `mainsequence.client.command_center.data_models`
- Streamlit helpers under `mainsequence.dashboards.streamlit`

Primary docs:

- [Create Your First API](https://mainsequence-sdk.github.io/mainsequence-sdk/tutorial/create_your_first_api/)
- [FastAPI](https://mainsequence-sdk.github.io/mainsequence-sdk/knowledge/fastapi/)
- [Streamlit](https://mainsequence-sdk.github.io/mainsequence-sdk/knowledge/dashboards/streamlit/)

## Command Center

- `Workspace`: shared application container
- registered widget types: what can be mounted
- AppComponent widgets: UI components backed by application endpoints
- `EditableFormDefinition`: custom input form contract
- widget data contracts: exact response models for widget-facing APIs

Primary docs:

- [Command Center](https://mainsequence-sdk.github.io/mainsequence-sdk/knowledge/command_center/)
- [Command Center Workspaces](https://mainsequence-sdk.github.io/mainsequence-sdk/knowledge/command_center/workspaces/)
- [Command Center Forms](https://mainsequence-sdk.github.io/mainsequence-sdk/knowledge/command_center/forms/)
- [Command Center Widget Data Contracts](https://mainsequence-sdk.github.io/mainsequence-sdk/knowledge/command_center/widget_data_contracts/)

Decision rule:

- use Streamlit for the fastest single-app surface
- use Command Center for reusable workspaces, widgets, forms, and shared UI composition
- if Command Center is chosen, plan API tasks too

## Market and identity layer

- `Asset`: stable market identity keyed by `unique_identifier`
- `AssetCategory`: reusable named collection of assets
- translation tables: map asset identities to upstream source systems
- pricing details and instruments: make assets priceable and support pricing runtime
- Virtual Fund Builder: portfolio construction and portfolio workflows

Primary docs:

- [Assets](https://mainsequence-sdk.github.io/mainsequence-sdk/knowledge/markets/assets/)
- [Asset Categories](https://mainsequence-sdk.github.io/mainsequence-sdk/knowledge/markets/asset_categories/)
- [Translation Tables](https://mainsequence-sdk.github.io/mainsequence-sdk/knowledge/markets/translation_tables/)
- [Instruments](https://mainsequence-sdk.github.io/mainsequence-sdk/knowledge/instruments/)
- [Virtual Fund Builder](https://mainsequence-sdk.github.io/mainsequence-sdk/knowledge/virtualfundbuilder/)

Decision rule:

- do not use free-form strings for market identity when platform assets are required
- if an asset must be priced, include pricing details and instrument terms

## Runtime configuration and governance

- `Constant`: non-sensitive runtime configuration
- `Secret`: sensitive runtime configuration
- `Project`, `DataNodeStorage`, `Bucket`, `Artifact`, `ResourceRelease`: shareable resource layer
- `ProjectResource` and `ResourceRelease`: deployment and reusable resource exposure

Primary docs:

- [Constants and Secrets](https://mainsequence-sdk.github.io/mainsequence-sdk/knowledge/infrastructure/constants_and_secrets/)
- [Part 5.2 — Streamlit Integration II](https://mainsequence-sdk.github.io/mainsequence-sdk/tutorial/dashboards/streamlit/streamlit_integration_2/)

Decision rule:

- do not hardcode runtime values that should be constants or secrets
- treat release and sharing as first-class design decisions, not an afterthought

## Reuse and discovery commands

Use platform search before building new resources.

- `mainsequence organization project-names`
- `mainsequence project validate-name "Candidate Name"`
- `mainsequence data-node search "query"`
- `mainsequence data-node list`
- `mainsequence simple_table list`
- `mainsequence constants list`
- `mainsequence secrets list`
- `mainsequence cc registered_widget_type list`
- `mainsequence project project_resource list`
- `mainsequence markets asset-translation-table list`

Primary docs:

- [CLI Overview](https://mainsequence-sdk.github.io/mainsequence-sdk/cli/)
