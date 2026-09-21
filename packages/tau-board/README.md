# Tau Board

An optional local dashboard for a running `ms-tau-sdk` process. Tau Board is a separate process and
does not load the Tau runtime or Main Sequence credentials.

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
process already configured with that selection. Selecting a profile does not change a running
Tau process's provider or model.

The board's SQLite access is read-only. Chat and Task changes use Tau's HTTP API. Bulma CSS and
all other UI assets are packaged locally; no Node or browser CDN is needed.

## Launch from VS Code

From the repository checkout, install the optional package once with
`uv sync --frozen --extra tau-board`. In VS Code's Run and Debug view, select
**Tau Board (local)** and press F5. The launch configuration uses `.venv`, reads the repository's
`.env`, and opens `http://127.0.0.1:8788` when Uvicorn starts. Start a local-mode Tau process
separately; select its loopback URL in the board's Connect view if it differs from the environment
default. The board can still open and inspect an explicit state directory while Tau is stopped.
