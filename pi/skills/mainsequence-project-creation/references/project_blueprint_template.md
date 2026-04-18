# `project_blueprint.md` Template

Use this exact file name before any new project is created.

## Overall intent

- user objective:
- target users:
- success output:
- business decision supported:

## Assumptions

- assumption:

## 1. Data acquisition

### Answers

- does the user already know the source:
- selected source candidates:
- why these sources are acceptable:
- ingestion mode:
- chosen storage model: `DataNode` | `SimpleTable` | `Artifact` | mixed

### tasks[]

- `Build ...`

## 2. Intelligence to build

### Answers

- visualization only or analytical logic:
- required transformations:
- required models, signals, calculations, or business rules:
- required code location under `src/`:

### tasks[]

- `Build ...`

## 3. Analysis and visualization surface

### Answers

- chosen surface: `streamlit` | `command_center` | both
- user interaction model:
- reusable widgets or one-off screens:
- whether an API is required:

### tasks[]

- `Build ...`

## 4. API surface

### Answers

- whether FastAPI is needed:
- main consumers:
- endpoint families:
- response contracts:
- auth or request user context requirements:

### tasks[]

- `Build ...`

## 5. Platform resources and operations

### Answers

- jobs and schedules:
- constants:
- secrets:
- artifacts and buckets:
- assets or asset categories:
- project resources and releases:
- access control or sharing requirements:

### tasks[]

- `Build ...`

## Chosen Main Sequence components

- `Project`
- `DataNode`
- `SimpleTable`
- `Artifact`
- `FastAPI`
- `Workspace`
- `ResourceRelease`

## Reuse before build

- existing resource candidates found:
- resources approved for reuse:
- resources that must be created new:

## Implementation order

1. `...`
2. `...`
3. `...`

## Open questions

- question:
