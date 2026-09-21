"""The installed package is a read-only input, never a runtime state directory.

An installed wheel puts `ms_tau_sdk/resources/` inside site-packages. Writing
there fails outright on a read-only install (a container with a read-only root
filesystem, a shared system site-packages, a Nix-style store) and makes the
package mutate itself at runtime. In this repository the same writes land in
`src/ms_tau_sdk`, where Hatchling then packages them into the next wheel.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from tau_ai.fake import FakeProvider

import ms_tau_sdk
from ms_tau_sdk.backend.models import (
    AgentSession,
    ProviderControl,
    ProviderCredential,
    RuntimeLease,
    RuntimeState,
    SessionEntryList,
    TauRuntimeBootstrap,
)
from ms_tau_sdk.providers.factory import ProviderRuntime
from ms_tau_sdk.resources.loader import resource_root, tau_resource_paths
from ms_tau_sdk.runtime.manager import RUNTIME_CAPABILITIES, SessionRuntimeManager
from ms_tau_sdk.settings import TauSDKSettings

PACKAGE_ROOT = Path(ms_tau_sdk.__file__).resolve().parent


def _package_tree() -> set[Path]:
    return {
        path.relative_to(PACKAGE_ROOT)
        for path in PACKAGE_ROOT.rglob("*")
        if "__pycache__" not in path.parts
    }


def _settings(tmp_path: Path) -> TauSDKSettings:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return TauSDKSettings(
        _env_file=None,
        runtime_credential_id="credential-id",
        runtime_credential_secret="credential-secret",
        workspace=workspace,
        state_root=tmp_path / "tau-state",
        startup_dependencies_enabled=False,
    )


def _bootstrap(session_uid: str) -> TauRuntimeBootstrap:
    return TauRuntimeBootstrap(
        session=AgentSession(
            uid=session_uid,
            agent_uid="agent-1",
            harness="tau",
            harness_protocol="tau-session-v1",
            harness_version="1",
            active_provider="test-provider",
            active_model="test-model",
            runtime_config_sha256="sha256:runtime",
        ),
        lease=RuntimeLease(
            lease_token=f"lease-{session_uid}",
            holder_id="holder",
            lease_expires_at=datetime.now(UTC) + timedelta(minutes=5),
        ),
        runtime_state=RuntimeState(
            harness="tau",
            harness_protocol="tau-session-v1",
            harness_version="1",
            runtime_activity="loading",
            activity_revision=1,
            activity_sequence=0,
            runtime_capabilities=RUNTIME_CAPABILITIES,
        ),
        history=SessionEntryList(entries=[], next_sequence=0),
        resume_snapshot=None,
        provider_credentials={},
        provider_control=ProviderControl(
            schema_version=1,
            catalog_digest=f"sha256:{'0' * 64}",
            provider="test-provider",
            model={
                "model": "test-model",
                "api": "openai-completions",
                "input": ["text"],
                "reasoning": False,
                "thinking_levels": [],
            },
        ),
        runtime_capabilities=RUNTIME_CAPABILITIES,
    )


def test_tau_resource_paths_separate_packaged_inputs_from_durable_state(tmp_path) -> None:
    state_home = tmp_path / "tau-state" / "workspace-digest"

    paths = tau_resource_paths(tmp_path, state_home=state_home)

    # Resource discovery keeps reading the packaged defaults...
    assert paths.root == resource_root()
    assert paths.system_prompt_path == resource_root() / "SYSTEM.md"
    # ...while every durable Tau write is redirected to the writable home.
    assert paths.paths is not None
    assert paths.paths.home == state_home
    assert paths.paths.extension_state_dir == state_home / "state" / "extensions"
    assert resource_root() not in paths.paths.llama_cpp_state_path.parents


async def test_loading_a_session_runtime_leaves_the_packaged_tree_pristine(tmp_path) -> None:
    settings = _settings(tmp_path)
    backend = AsyncMock()
    backend.auth = Mock()
    backend.bootstrap_tau_runtime.return_value = _bootstrap("session-1")
    backend.provider_credential_from_hydration = Mock(
        return_value=ProviderCredential(provider="test-provider", api_key="secret")
    )
    backend.patch_runtime_activity.return_value = RuntimeState(
        harness="tau",
        harness_protocol="tau-session-v1",
        harness_version="1",
        runtime_activity="idle",
        activity_revision=2,
        activity_sequence=1,
        applied=True,
        runtime_capabilities=RUNTIME_CAPABILITIES,
    )
    providers = Mock()
    providers.for_session_credential.return_value = ProviderRuntime(
        name="test-provider",
        model="test-model",
        thinking_level="off",
        provider=FakeProvider([]),
        credential=ProviderCredential(provider="test-provider", api_key="secret"),
        provider_control=_bootstrap("session-1").provider_control,
    )
    manager = SessionRuntimeManager(settings=settings, backend=backend, providers=providers)
    before = _package_tree()

    with patch(
        "ms_tau_sdk.runtime.manager.MainSequenceMCPClient.connect",
        AsyncMock(return_value=SimpleNamespace(tools=(), resources=(), aclose=AsyncMock())),
    ):
        await manager.get("session-1")
        await manager.aclose()

    assert _package_tree() == before
    # The built-in llama.cpp extension is the write that used to land in
    # `ms_tau_sdk/resources/state/extensions`; it now has a writable home.
    assert (settings.tau_state_home / "state" / "extensions").is_dir()
