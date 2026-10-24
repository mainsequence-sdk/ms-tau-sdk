"""Version-locked interception of Tau's project instruction discovery (ADR 0025).

Tau is a coding-agent harness. It reads the repository root's ``AGENTS.md``, every ``AGENTS.md``
between the project root and the workspace, and ``.agents/AGENTS.md`` as project instructions,
the way Codex and Claude Code do. In a repository that deploys an Agent those files instruct the
coding agents that develop the repository, not the Agent. ``tau-ai==0.4.2`` computes the ancestor
files from the workspace directly, so the SDK's ``TauPaths`` cannot redirect them, and no session
option or extension hook removes a discovered file.

This module wraps the discovery function where ``tau_coding.session`` calls it, on load and on
reload. For resource paths whose ``TauPaths`` declare that they do not read the development
resources, it keeps only the files under the workspace ``.tau`` directory; for every other caller
it returns Tau's result unchanged. Tau's own reading, diagnostics and de-duplication are reused.
Like the provider patch of ADR 0016, the wrapper is locked to the exact Tau version it was
reviewed against: an unverified wrapper could let development instructions reach a deployed Agent
silently.
"""

from __future__ import annotations

from importlib.metadata import version
from pathlib import Path
from typing import Any

from tau_coding import context as tau_context
from tau_coding import session as tau_session
from tau_coding.resources import ResourceDiagnostic, TauResourcePaths
from tau_coding.system_prompt import ProjectContextFile

from ms_tau_sdk.errors import ConfigurationError

AFFECTED_TAU_AI_VERSION = "0.4.2"
_PATCH_MARKER = "__ms_tau_sdk_project_context_filter__"
# The attribute a `TauPaths` sets to `False` to keep project instructions to `.tau`.
READS_DEVELOPMENT_RESOURCES = "reads_development_resources"
EXCLUDED_CONTEXT_MESSAGE = (
    "not read: an AGENTS.md outside .tau instructs the coding agents that develop the "
    "repository, not the Agent; TAU_INCLUDE_DEVELOPMENT_RESOURCES reads it (ADR 0025)"
)


def install_project_context_filter() -> None:
    """Install the reviewed Tau 0.4.2 project instruction filter exactly once.

    A Tau upgrade must review and either remove or explicitly retarget this private-boundary
    wrapper. Failing session construction is safer than applying it to another version unseen.
    """

    installed_version = version("tau-ai")
    if installed_version != AFFECTED_TAU_AI_VERSION:
        raise ConfigurationError(
            "The ms-tau-sdk project instruction filter only supports "
            f"tau-ai=={AFFECTED_TAU_AI_VERSION}; found {installed_version}. Review "
            "tau_coding.context.discover_project_context_with_diagnostics and its use in "
            "tau_coding.session before changing the pin (ADR 0025)."
        )

    session_module: Any = tau_session
    current = session_module.discover_project_context_with_diagnostics
    if getattr(current, _PATCH_MARKER, False):
        return
    if current is not tau_context.discover_project_context_with_diagnostics:
        raise ConfigurationError(
            "Tau project context discovery changed; compatibility review required (ADR 0025)"
        )

    original = current

    def filtered(
        paths: TauResourcePaths | None = None,
    ) -> tuple[tuple[ProjectContextFile, ...], tuple[ResourceDiagnostic, ...]]:
        context_files, diagnostics = original(paths)
        if (
            paths is None
            or paths.cwd is None
            or getattr(paths.paths, READS_DEVELOPMENT_RESOURCES, True)
        ):
            return context_files, diagnostics
        # Tau resolves every discovered path; compare against the resolved `.tau` directory.
        tau_dir = (paths.cwd / ".tau").expanduser().resolve()
        kept: list[ProjectContextFile] = []
        excluded: list[ResourceDiagnostic] = []
        for context_file in context_files:
            path = Path(context_file.path)
            if path.is_relative_to(tau_dir):
                kept.append(context_file)
            else:
                excluded.append(
                    ResourceDiagnostic(
                        kind="context",
                        path=path,
                        severity="info",
                        message=EXCLUDED_CONTEXT_MESSAGE,
                    )
                )
        return tuple(kept), (*diagnostics, *excluded)

    setattr(filtered, _PATCH_MARKER, True)
    session_module.discover_project_context_with_diagnostics = filtered


__all__ = [
    "AFFECTED_TAU_AI_VERSION",
    "EXCLUDED_CONTEXT_MESSAGE",
    "READS_DEVELOPMENT_RESOURCES",
    "install_project_context_filter",
]
