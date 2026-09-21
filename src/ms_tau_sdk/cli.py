"""Command-line startup for Main Sequence TAU SDK."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

import uvicorn

from ms_tau_sdk.app import create_app
from ms_tau_sdk.settings import TauSDKSettings, get_settings
from ms_tau_sdk.skills import (
    SkillSyncError,
    list_packaged_skills,
    packaged_skills_path,
    resolve_packaged_skill,
    sync_skills,
)


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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ms-tau")
    commands = parser.add_subparsers(dest="command")

    skills = commands.add_parser("skills", help="Inspect or synchronize SDK-owned agent skills")
    skill_commands = skills.add_subparsers(dest="skills_command", required=True)

    sync = skill_commands.add_parser("sync", help="Synchronize skills into a project")
    sync.add_argument("--path", type=Path, default=Path.cwd(), help="Project root")
    sync.add_argument("--dry-run", action="store_true", help="Report without changing files")
    sync.add_argument("--json", action="store_true", dest="as_json", help="Emit JSON")

    list_command = skill_commands.add_parser("list", help="List bundled skills")
    list_command.add_argument("--json", action="store_true", dest="as_json", help="Emit JSON")

    path_command = skill_commands.add_parser("path", help="Print the skill bundle or one skill")
    path_command.add_argument("skill", nargs="?", help="Optional bundled skill name")

    return parser


def _run_skills_command(arguments: argparse.Namespace) -> int:
    if arguments.skills_command == "sync":
        result = sync_skills(arguments.path, dry_run=arguments.dry_run)
        if arguments.as_json:
            print(result.to_json())
        else:
            action = "Would synchronize" if result.dry_run else "Synchronized"
            print(
                f"{action} {len(result.skills)} ms-tau-sdk skills "
                f"for version {result.installed_version} to {result.destination}"
            )
        return 0

    if arguments.skills_command == "list":
        skills = list_packaged_skills()
        if arguments.as_json:
            print(json.dumps({"skills": skills}, sort_keys=True))
        else:
            print("\n".join(skills))
        return 0

    if arguments.skills_command == "path":
        path = (
            resolve_packaged_skill(arguments.skill) if arguments.skill else packaged_skills_path()
        )
        print(path)
        return 0

    raise SkillSyncError(f"Unknown skills command: {arguments.skills_command}")


def main(argv: Sequence[str] | None = None) -> int:
    """Start Tau by default or execute an explicit SDK maintenance command."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        run()
        return 0

    try:
        parsed = _parser().parse_args(arguments)
        if parsed.command == "skills":
            return _run_skills_command(parsed)
    except SkillSyncError as error:
        print(f"ms-tau: {error}", file=sys.stderr)
        return 2

    _parser().print_help()
    return 2
