"""Observability for Tau-native CodeRepository extensions."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import structlog
from tau_agent.types import JSONValue
from tau_coding import CodingSession

from ms_tau_sdk.errors import ConfigurationError
from ms_tau_sdk.runtime.snapshots import sha256_json

TASK_CONTROL_TOOL_NAMES = frozenset({"task_request_input", "task_request_authorization"})


def validate_tool_catalog(
    session: CodingSession,
    *,
    sdk_tool_names: frozenset[str],
    require_complete_project_catalog: bool,
    reserved_sdk_tool_names: frozenset[str] = frozenset(),
) -> None:
    """Reject extension overrides and catalog drift before a model can use tools."""
    sources = session.extension_tool_sources
    reserved = TASK_CONTROL_TOOL_NAMES & sources.keys()
    if reserved:
        raise ConfigurationError(
            "Project extensions cannot replace A2A Task controls: " + ", ".join(sorted(reserved))
        )
    replaced = reserved_sdk_tool_names & sources.keys()
    if replaced:
        raise ConfigurationError(
            "Project extensions cannot replace reserved SDK tools: " + ", ".join(sorted(replaced))
        )
    names = [tool.name for tool in session.tools]
    expected = sdk_tool_names | sources.keys()
    if len(names) != len(set(names)) or set(names) != expected:
        raise ConfigurationError("Effective Tau tool catalog differs from configured tool sources")
    if require_complete_project_catalog:
        metadata = session.extension_runtime.extension_metadata
        for owner in set(sources.values()):
            matches = [item for item in metadata if item.name == owner]
            if len(matches) != 1 or matches[0].source != "project":
                raise ConfigurationError("Tool-only runtime includes a non-project extension tool")
    if require_complete_project_catalog and any(
        diagnostic.severity == "error" for diagnostic in session.extension_runtime.diagnostics
    ):
        raise ConfigurationError("Project extension loading failed in tool-only runtime mode")


@dataclass(slots=True)
class ProjectExtensionState:
    """Mutable extension diagnostics for health snapshots and structured logs."""

    enabled: bool
    loaded_extension_count: int = 0
    project_tool_count: int = 0
    extension_diagnostic_count: int = 0
    extension_error_count: int = 0
    tool_catalog_digest: str = ""

    def update_from_session(self, session: CodingSession) -> None:
        """Capture Tau's effective extension and tool composition."""
        extension_tool_sources = session.extension_tool_sources
        catalog = [
            {
                "name": tool.name,
                "label": tool.label,
                "description": tool.description,
                "parameters": dict(tool.parameters),
                "execution_mode": tool.execution_mode,
                "prompt_snippet": tool.prompt_snippet,
                "prompt_guidelines": list(tool.prompt_guidelines),
                "source": extension_tool_sources.get(tool.name, "ms-tau-sdk"),
            }
            for tool in sorted(session.tools, key=lambda item: item.name)
        ]
        self.loaded_extension_count = len(session.extension_names)
        self.project_tool_count = len(extension_tool_sources)
        diagnostics = session.extension_runtime.diagnostics
        self.extension_diagnostic_count = len(diagnostics)
        self.extension_error_count = sum(
            diagnostic.severity == "error" for diagnostic in diagnostics
        )
        self.tool_catalog_digest = sha256_json(catalog)

    def details(self) -> dict[str, JSONValue]:
        """Return the stable operator-facing diagnostics contract."""
        return {
            "project_extensions_enabled": self.enabled,
            "loaded_extension_count": self.loaded_extension_count,
            "project_tool_count": self.project_tool_count,
            "extension_diagnostic_count": self.extension_diagnostic_count,
            "extension_error_count": self.extension_error_count,
            "tool_catalog_digest": self.tool_catalog_digest or None,
        }


_resources_logger = structlog.get_logger("ms_tau_sdk.runtime.resources")
# Extension and trust diagnostics are logged with the extension composition.
_RESOURCE_KINDS_LOGGED_ELSEWHERE = frozenset({"extension", "project-trust"})


def _workspace_relative_path(path: Path | None, *, cwd: Path) -> str | None:
    # Never an absolute path: a file above the workspace reads as `../AGENTS.md`.
    if path is None:
        return None
    return Path(os.path.relpath(path.resolve(), cwd.resolve())).as_posix()


def project_resources_summary(
    session: CodingSession, *, cwd: Path, development_resources: bool
) -> dict[str, JSONValue]:
    """The Agent's resources Tau selected from the workspace (ADR 0025)."""
    system_prompt_files: list[JSONValue] = [
        _workspace_relative_path(path, cwd=cwd) for path in session.system_prompt_files
    ]
    context_files: list[JSONValue] = [
        _workspace_relative_path(Path(context_file.path), cwd=cwd)
        for context_file in session.context_files
    ]
    skills: list[JSONValue] = [*sorted(skill.name for skill in session.skills)]
    prompt_templates: list[JSONValue] = [
        *sorted(template.name for template in session.prompt_templates)
    ]
    return {
        "developmentResources": development_resources,
        "systemPromptFiles": system_prompt_files,
        "contextFiles": context_files,
        "skills": skills,
        "promptTemplates": prompt_templates,
    }


def log_project_resources(
    session: CodingSession,
    *,
    session_uid: str,
    cwd: Path,
    development_resources: bool,
    phase: str,
) -> None:
    """Log what Tau selected and reported for the Agent's resources on load or reload."""
    summary = project_resources_summary(
        session, cwd=cwd, development_resources=development_resources
    )
    _resources_logger.info(
        "runtime.project_resources.load.completed",
        session_uid=session_uid,
        phase=phase,
        development_resources=development_resources,
        system_prompt_files=summary["systemPromptFiles"],
        context_files=summary["contextFiles"],
        skill_count=len(session.skills),
        prompt_template_count=len(session.prompt_templates),
    )
    workspace = str(cwd.resolve())
    for diagnostic in session.resource_diagnostics:
        if diagnostic.kind in _RESOURCE_KINDS_LOGGED_ELSEWHERE:
            continue
        log = {"error": _resources_logger.error, "warning": _resources_logger.warning}.get(
            diagnostic.severity, _resources_logger.info
        )
        log(
            "runtime.project_resources.diagnostic",
            session_uid=session_uid,
            phase=phase,
            resource_kind=diagnostic.kind,
            resource_name=diagnostic.name,
            resource_path=_workspace_relative_path(diagnostic.path, cwd=cwd),
            diagnostic_severity=diagnostic.severity,
            message=diagnostic.message.replace(workspace, "."),
        )
