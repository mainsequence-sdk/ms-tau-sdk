# Job-Hosted Batch Execution

**Design accepted; implementation pending.** The Python `run_batch` API shown below is the target
contract from [ADR 0015](../adrs/0015-job-hosted-batch-execution.md). It is not available in the
current SDK release.

A Main Sequence Job can run Tau for one finite assignment inside its project image. The Job and
JobRun remain responsible for launch, task selection, process limits, cancellation, status, and
logs. The project supplies an instruction string and its normal Tau SDK configuration to the
batch entry point. The SDK resolves the workspace's `.tau` resources, composes only configured
tool sources, executes the work, closes its dependencies, and returns. It does not start Uvicorn or
serve an HTTP endpoint.

## Target project script

```python
# jobs/solve_with_tau.py
import asyncio
from pathlib import Path

from ms_tau_sdk import TauSDKSettings, run_batch


async def main() -> None:
    answer = await run_batch(
        "Inspect this project's ingestion pipeline and fix the failing validation.",
        settings=TauSDKSettings(workspace=Path.cwd()),
    )
    print(answer)


if __name__ == "__main__":
    asyncio.run(main())
```

The string passed to `run_batch` is the instruction selected by this Job. It is not a platform
AgentTask or a new SDK record. A different Job script can derive the string from its existing
arguments or project data. The SDK does not prescribe that source or save the answer to a JobRun.
The script above chooses to print the answer, so the existing Job log collector can display it;
project code may instead send it to an existing output destination.

`TauSDKSettings` and project `.tau` configuration have the same meaning in service and batch
execution. In particular, `TAU_EXCLUDE_BASE_TOOLS=true` omits the SDK's standard coding tools and
`TAU_EXCLUDE_MAINSEQUENCE_MCP=true` prevents an MCP connection. Batch startup must not turn those
sources on simply because the process is short lived. Main Sequence supplies the process's
authorized credentials and configuration; the SDK does not receive or validate a JobRun UID.

## Process lifecycle

```text
Main Sequence creates JobRun and starts the project image
  -> project script calls the SDK batch entry point
  -> SDK composes the configured Tau runtime and runs the instruction
  -> SDK finishes the turn, closes the runtime, and returns or raises
  -> project script exits; Main Sequence settles JobRun status and collects logs
```

The current `ms-tau` command starts the ASGI service, and upstream `tau --print` does not compose
the Main Sequence SDK runtime. Neither is the job-hosted batch entry point. Do not present the
example above as runnable until the SDK and backend authorization dependencies in ADR 0015 ship.
