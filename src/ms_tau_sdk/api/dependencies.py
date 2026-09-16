"""Typed access to application-owned services."""

from __future__ import annotations

from typing import cast

from fastapi import Request

from ms_tau_sdk.backend.client import MainSequenceClient
from ms_tau_sdk.providers.factory import ProviderFactory
from ms_tau_sdk.runtime.manager import SessionRuntimeManager
from ms_tau_sdk.settings import TauSDKSettings


def settings(request: Request) -> TauSDKSettings:
    return cast(TauSDKSettings, request.app.state.settings)


def backend(request: Request) -> MainSequenceClient:
    return cast(MainSequenceClient, request.app.state.backend)


def runtime_manager(request: Request) -> SessionRuntimeManager:
    return cast(SessionRuntimeManager, request.app.state.runtime_manager)


def provider_factory(request: Request) -> ProviderFactory:
    return cast(ProviderFactory, request.app.state.provider_factory)
