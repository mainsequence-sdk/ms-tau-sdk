"""Command-line startup for Main Sequence TAU SDK."""

from __future__ import annotations

import uvicorn

from ms_tau_sdk.app import create_app
from ms_tau_sdk.settings import TauSDKSettings, get_settings


def run(settings: TauSDKSettings | None = None) -> None:
    """Run one application instance from validated settings."""
    resolved = settings or get_settings()
    uvicorn.run(
        create_app(resolved),
        host=resolved.host,
        port=resolved.port,
        log_config=None,
        access_log=False,
    )


def main() -> None:
    """Console-script entrypoint."""
    run()
