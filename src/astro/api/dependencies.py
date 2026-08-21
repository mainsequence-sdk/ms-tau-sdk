"""Typed access to application-owned services."""

from __future__ import annotations

from typing import cast

from fastapi import Request

from astro.backend.client import MainSequenceClient
from astro.providers.factory import ProviderFactory
from astro.providers.signin import ProviderSignInManager
from astro.runtime.manager import SessionRuntimeManager
from astro.settings import Settings


def settings(request: Request) -> Settings:
    return cast(Settings, request.app.state.settings)


def backend(request: Request) -> MainSequenceClient:
    return cast(MainSequenceClient, request.app.state.backend)


def runtime_manager(request: Request) -> SessionRuntimeManager:
    return cast(SessionRuntimeManager, request.app.state.runtime_manager)


def provider_factory(request: Request) -> ProviderFactory:
    return cast(ProviderFactory, request.app.state.provider_factory)


def provider_signin_manager(request: Request) -> ProviderSignInManager:
    return cast(ProviderSignInManager, request.app.state.provider_signin_manager)
