"""Concrete AIGateway implementations.

Each module here provides one :class:`AIGateway` subclass. Registration
with :class:`GatewayRegistry` is done by the hosting app (``app.py``)
at startup, after the companion plugin that owns the backend service
has loaded.

The old ``ComfyUIJobHandle`` was retired in the de-duplication refactor:
``ComfyUIGateway.submit`` now delegates runtime streaming to
``ExtendedComfyUIService`` and returns an internal
``_ServiceBackedJobHandle`` — concrete job handles are no longer part
of the public gateway surface.
"""

from __future__ import annotations

from loomstroke.plugins.ai_effect.gateways.comfyui import ComfyUIGateway
from loomstroke.plugins.ai_effect.gateways.gptimage2 import GPTImage2Gateway
from loomstroke.plugins.ai_effect.gateways.nanobanana import NanoBananaGateway

__all__ = ["ComfyUIGateway", "GPTImage2Gateway", "NanoBananaGateway"]
