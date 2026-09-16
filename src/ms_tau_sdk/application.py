"""Construction and lifecycle ownership for the SDK application."""

from __future__ import annotations

from dataclasses import dataclass

from ms_tau_sdk.backend.auth import RuntimeCredentialAuth
from ms_tau_sdk.backend.client import MainSequenceClient
from ms_tau_sdk.providers.factory import ProviderFactory
from ms_tau_sdk.runtime.manager import SessionRuntimeManager
from ms_tau_sdk.settings import TauSDKSettings


@dataclass(slots=True)
class ApplicationServices:
    """Application-scoped services with one explicit lifecycle owner."""

    settings: TauSDKSettings
    auth: RuntimeCredentialAuth
    backend: MainSequenceClient
    providers: ProviderFactory
    runtime: SessionRuntimeManager

    @classmethod
    def create(cls, settings: TauSDKSettings) -> ApplicationServices:
        """Construct the default service graph without starting network work."""
        auth = RuntimeCredentialAuth(settings)
        backend = MainSequenceClient(settings, auth)
        providers = ProviderFactory(backend)
        runtime = SessionRuntimeManager(
            settings=settings,
            backend=backend,
            providers=providers,
        )
        return cls(
            settings=settings,
            auth=auth,
            backend=backend,
            providers=providers,
            runtime=runtime,
        )

    async def start(self) -> None:
        """Validate configuration and start application-scoped dependencies."""
        self.settings.validate_runtime_auth()
        try:
            await self.runtime.start()
        except BaseException:
            await self.backend.aclose()
            raise

    async def aclose(self) -> None:
        """Close the runtime before its shared API client."""
        try:
            await self.runtime.aclose()
        finally:
            await self.backend.aclose()
