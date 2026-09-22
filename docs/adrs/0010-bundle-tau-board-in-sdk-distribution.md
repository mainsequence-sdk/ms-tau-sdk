# ADR 0010: Bundle Tau Board in the SDK Distribution

Status: Accepted

Date: 2026-09-22

## Context

ADR 0008 made Tau Board a separate process and a separate `ms-tau-board` PyPI distribution.
The SDK's `tau-board` extra depended on that distribution. The Board distribution was not
published before the SDK advertised the extra, leaving the extra unresolvable and blocking the
SDK release. A second release workflow and publisher configuration added operational work for a
small local development tool. The separate process is valuable; a separate distribution is not
required for it.

## Decision

Publish one `ms-tau-sdk` distribution containing two import packages: `ms_tau_sdk` and
`ms_tau_board`. Keep Board source, tests, and static assets under `packages/tau-board/`, outside
`src/ms_tau_sdk/`. The Board continues to start through its own `tau-board` command, binds only
to loopback, speaks to the Tau process over HTTP, and never imports the SDK runtime. No Board
route or asset is added to Tau's FastAPI application.

The base SDK wheel contains Board code and assets but does not import or start them. The
`tau-board` extra remains an install option for Board's explicit supported dependency bounds.
Python extras select dependencies, not files: installing plain `ms-tau-sdk` still places the
small Board package in the environment. The Board's HTTP and static-file dependencies largely
overlap with existing SDK dependencies; the extra tightens the Starlette and Uvicorn bounds used
to test Board. Users who intend to run Board install `ms-tau-sdk[tau-board]`.

The SDK version is the Board version. Remove the `ms-tau-board` project metadata, workspace
source override, standalone publication workflow, separate tag, and release gate that requires a
prepublished Board. The SDK wheel and source distribution must contain the Board package, entry
point, and static assets. The single distribution verifier enforces those contents, the asset
budget, and the absence of unexpected files. A clean-wheel check invokes the Board command.

## Consequences

One release publishes and validates both the SDK and Board. The Board can no longer be versioned
or installed independently of the SDK. Every SDK installation includes the small Board files,
even when the Board is unused. The process, import, and HTTP boundaries remain independent.
