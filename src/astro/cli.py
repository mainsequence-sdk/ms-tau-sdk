"""Command-line startup for the current extraction package."""

from __future__ import annotations

import uvicorn

from astro.app import create_app
from astro.settings import Settings, get_settings


def run(settings: Settings | None = None) -> None:
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
