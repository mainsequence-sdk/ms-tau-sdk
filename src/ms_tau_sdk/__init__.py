"""Public construction surface for Main Sequence TAU SDK."""

from importlib.metadata import version

__version__: str = version("ms-tau-sdk")

from .app import create_app
from .runtime.deployment_health import (
    RUNTIME_HEALTH_ABI_VERSION,
    register_deployment_readiness_hook,
)
from .settings import TauSDKSettings

__all__ = [
    "RUNTIME_HEALTH_ABI_VERSION",
    "TauSDKSettings",
    "__version__",
    "create_app",
    "register_deployment_readiness_hook",
]
