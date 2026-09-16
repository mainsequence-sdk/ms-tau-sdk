"""Public construction surface for Main Sequence TAU SDK."""

from importlib.metadata import version

__version__: str = version("ms-tau-sdk")

from .app import create_app
from .settings import TauSDKSettings

__all__ = ["TauSDKSettings", "__version__", "create_app"]
