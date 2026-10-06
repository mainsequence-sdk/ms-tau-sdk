"""Public construction surface for Main Sequence TAU SDK."""

from importlib.metadata import version

__version__: str = version("ms-tau-sdk")

from .app import create_app
from .runtime.deployment_health import (
    RUNTIME_HEALTH_ABI_VERSION,
    register_deployment_readiness_hook,
)
from .runtime.requester import current_requester, requester_client
from .settings import TauSDKSettings

__all__ = [
    "RUNTIME_HEALTH_ABI_VERSION",
    "TauSDKSettings",
    "__version__",
    "create_app",
    "current_requester",
    "register_deployment_readiness_hook",
    "requester_client",
]
