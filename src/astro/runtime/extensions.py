"""Observability for Tau-native CodeRepository extensions."""

from __future__ import annotations

from dataclasses import dataclass

from tau_agent.types import JSONValue
from tau_coding import CodingSession

from astro.runtime.snapshots import sha256_json


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
