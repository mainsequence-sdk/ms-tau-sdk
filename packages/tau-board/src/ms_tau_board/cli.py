"""Launch the board without importing or starting the Tau runtime."""

from __future__ import annotations

import argparse
from collections.abc import Sequence

import uvicorn

from .app import create_app
from .config import BoardSettings


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tau-board", description="Local Tau development board")
    parser.add_argument("--tau-url", help="Loopback URL of an existing Tau process")
    parser.add_argument("--state-dir", help="Directory containing runtime.sqlite3 and logs/")
    parser.add_argument("--port", type=int, help="Board port (default: 8788)")
    args = parser.parse_args(argv)
    try:
        settings = BoardSettings.from_environment(
            tau_url=args.tau_url,
            state_dir=args.state_dir,
            port=args.port,
        )
    except ValueError as error:
        parser.error(str(error))
    uvicorn.run(create_app(settings), host="127.0.0.1", port=settings.port, access_log=False)
    return 0
