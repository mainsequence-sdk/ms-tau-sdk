# ADR 0009: Backend Model Selection and Task Timelines in Tau Board

Status: Accepted

The separate-process design remains in force; distribution packaging is amended by ADR 0010.
The A2A Task timestamp presentation is clarified by the 1.2.9 implementation.

Date: 2026-09-22

## Context

ADR 0008 used manually labelled endpoint profiles as the only source of model choices. This did
not expose the authenticated Main Sequence model catalog and could not change the model of a
running local session. Its log view was organized by severity and displayed raw JSON records,
making it difficult to follow a particular session or Task.

## Decision

### Model catalog and session selection

Tau exposes `GET /api/chat/model-providers` in local mode. It calls Main Sequence's existing
authenticated `GET /api/v1/model-providers/` through the SDK backend client and returns the safe
catalog response. Tau Board consumes this Tau route over its allowlisted local proxy. It never
reads the user's JWT or calls the Main Sequence backend directly. The picker shows all published
providers and models; providers whose catalog row is disabled or unauthenticated are visibly
unavailable. An exact selection is still validated by Main Sequence when applied, since the
general catalog does not assert that every model is executable in this Tau process.

The board offers provider, model, and supported thinking choices for Chat and A2A. Before the
next action, it calls `PUT /api/chat/session-model` with the selected session ID and choice. Tau
canonicalizes the local session ID, authorizes the exact selection through the existing local
provider-hydration contract, persists it on the same session, closes the idle loaded runtime,
then reloads that session with its durable history and the new provider evidence. Active turns
reject model changes. New local sessions start from the environment default before an explicit
board choice is applied. The response reports Tau's effective selection; the board does not
assume that choosing an option changed execution until this call succeeds.

The existing `TAU_LOCAL_PROVIDER`, `TAU_LOCAL_MODEL`, and `TAU_LOCAL_THINKING` remain startup
defaults. Changing a local session's selection does not mutate Tau's process environment or any
other session. The session's runtime configuration digest changes, so an older model-specific
snapshot is not restored as if it matched; durable entries remain authoritative.

### Session and Task exploration

Tau Board adds a dedicated Tasks tab alongside A2A. The tab lists recent Tasks, scopes them by a
selected local session, and can open a Task by ID. Its detail shows status, timestamps, attempts,
artifacts, and all retained local Task state events. A2A remains the action composer; Tasks is
the inspection surface.

The public A2A Task contract exposes the timestamp of the current status as
`task.status.timestamp`; it does not define top-level creation or completion fields. Tau Board
therefore renders completion from that standard status timestamp when the current state is
`TASK_STATE_COMPLETED`. It renders creation from the local read-only SQLite Task row, where Tau
already persists `created_at`. For non-completed Tasks, the same protocol timestamp is labelled
as the time the current status was recorded. If the Board is not connected to the matching local
state directory, creation is shown as unavailable. Tau does not add non-standard fields to the
A2A wire Task to satisfy a UI concern.

Logs start with a session picker, then an optional Task picker. Level and event filters are
secondary. A Task timeline combines its SQLite state events and attempts with structured logs
tagged with that Task and session events in the Task's execution window. Events explicitly tagged
to another Task are excluded. Each row identifies its source and exposes raw structured fields
on expansion. The board scans retained rotated log files and paginates matching results; events
outside the local retention window cannot be recovered.

Tau binds Task and session identifiers to detached Task execution logs. The board uses exact ID
matches and the SQLite Task-to-session relation rather than substring searches. SQLite access
remains read-only, and the board continues to run as a separate optional package.

## Consequences

Tau gains general local runtime APIs for catalog discovery and session model selection; these are
not board assets or routes inside the board process. Tau Board continues to import no SDK code.
The model switch reloads the local runtime between turns, which can briefly interrupt an
inspection request for that session. A model unavailable under backend policy fails with an
explicit error and leaves the existing session selection in place.
