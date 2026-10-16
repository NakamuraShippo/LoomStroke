"""3D generation provider adapters."""

from .api_service import ApiService3DProviderAdapter
from .comfyui import ComfyUI3DProviderAdapter

__all__ = ["ApiService3DProviderAdapter", "ComfyUI3DProviderAdapter"]
