# Tau Board

A local dashboard bundled in the `ms-tau-sdk` wheel. Tau Board is a separate process and does
not load the Tau runtime or use Main Sequence credentials. The `tau-board` extra selects its
tested dependency bounds; the command and assets are included in the SDK distribution.

```bash
uv add 'ms-tau-sdk[tau-board]'
tau-board --tau-url http://127.0.0.1:8010
```

Open `http://127.0.0.1:8788`. The board reads `TAU_BOARD_TAU_URL` or
`MAINSEQUENCE_TAU_PORT` for its initial Tau URL. `TAU_BOARD_STATE_DIR` selects the directory
containing `runtime.sqlite3` and `logs/tau.jsonl`; otherwise the board uses
`TAU_LOCAL_STATE_ROOT` and the connected runtime's workspace digest. You can override both in the
Connect view. The board accepts only local-mode Tau endpoints on loopback.

`TAU_BOARD_PROFILES` may contain a JSON array of additional profiles with `name`, `url`,
`provider`, `model`, optional `thinking`, and optional `stateDir`. Each profile points to a Tau
process. Chat and A2A load provider, model, and thinking options from the authenticated Main
Sequence catalog through Tau. Choosing a different model updates the same idle local session
before the next action; a working session must finish or be cancelled first.

The **Tasks** tab selects a local Task and shows its status, attempts, artifacts, state events,
and related Task and session logs. The **Logs** tab starts with a local session picker and can
narrow to a Task. Log history is limited by Tau's rotated local files.

The board's SQLite access is read-only. Chat and Task changes use Tau's HTTP API. Bulma CSS and
all other UI assets are packaged locally; no Node or browser CDN is needed.

The **Settings** tab shows non-secret environment values inherited by the board process, including
`MAINSEQUENCE_ENDPOINT`. It also lets you edit those named variables in the working directory's
`.env` file, or in the file chosen by `TAU_BOARD_ENV_FILE`. Credential values stay hidden; only
their presence is shown. Saved `.env` changes require restarting Tau or Tau Board to affect their
processes. The selected Tau URL and local state directory can be overridden immediately for the
current board session.

## Launch from VS Code

From the repository checkout, install the SDK with Board dependencies once with
`uv sync --frozen --extra tau-board`. In VS Code's Run and Debug view, select
**Tau Board (local)** and press F5. The launch configuration uses `.venv`, reads the repository's
`.env`, and opens `http://127.0.0.1:8788` when Uvicorn starts. Start a local-mode Tau process
separately; select its loopback URL in the board's Connect view if it differs from the environment
default. The board can still open and inspect an explicit state directory while Tau is stopped.
