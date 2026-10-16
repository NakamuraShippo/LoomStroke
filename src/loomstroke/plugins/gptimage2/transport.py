"""Transport layer for OpenAI GPT Image 2 integration."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from collections.abc import Callable, Mapping, Sequence
from io import BytesIO
from typing import TYPE_CHECKING, Any, Protocol

import aiohttp

from loomstroke.plugins.common.layer_bridge import LayerTransferBridge

from .input_images import normalize_input_pngs
from .types import GPTImage2Options

if TYPE_CHECKING:
    from loomstroke.host_api import HostAPI, LayerRef

logger = logging.getLogger(__name__)

OPENAI_IMAGE_GENERATIONS_ENDPOINT = "https://api.openai.com/v1/images/generations"
OPENAI_IMAGE_EDITS_ENDPOINT = "https://api.openai.com/v1/images/edits"
_OPENAI_API_KEY_TOKEN_RE = re.compile(r"\bsk-[A-Za-z0-9_*\-=]{6,}")


class IGPTImage2Transport(Protocol):
    async def health(self) -> bool: ...

    async def export_layer_png(self, layer_id: str) -> bytes: ...

    async def apply_image_to_layer(
        self,
        image_bytes: bytes,
        *,
        target_layer_id: str | None,
        metadata: dict[str, object] | None = None,
    ) -> None: ...

    async def generate(  # noqa: PLR0912 - linear request payload / response handling
        self,
        prompt: str,
        options: GPTImage2Options,
        emit_progress: Callable[[str], None],
        cancelled: Callable[[], bool],
        *,
        api_key: str,
        input_pngs: Sequence[bytes] | None = None,
    ) -> bytes: ...


def _extract_openai_error(status: int, text: str) -> str:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return f"OpenAI image generation failed with HTTP {status}"
    if not isinstance(payload, Mapping):
        return f"OpenAI image generation failed with HTTP {status}"
    error = payload.get("error")
    if isinstance(error, Mapping):
        message = _OPENAI_API_KEY_TOKEN_RE.sub(
            "sk-REDACTED",
            str(error.get("message") or "").strip(),
        )
        code = str(error.get("code") or error.get("type") or "").strip()
        if message and code:
            return f"OpenAI image generation failed ({code}): {message}"
        if message:
            return f"OpenAI image generation failed: {message}"
    return f"OpenAI image generation failed with HTTP {status}"


def _ensure_png_bytes(image_bytes: bytes, *, source_format: str) -> bytes:
    """Normalize API output to PNG for LoomStroke's layer/history pipeline."""
    if source_format == "png":
        return image_bytes
    try:
        from PIL import Image  # type: ignore[import-not-found]  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - app runtime includes Pillow
        raise RuntimeError("Pillow is required to normalize GPT Image 2 output") from exc

    with Image.open(BytesIO(image_bytes)) as image:
        converted = image.convert("RGBA")
        output = BytesIO()
        converted.save(output, format="PNG")
        return output.getvalue()


class OpenAIImageTransport(IGPTImage2Transport):
    """Concrete transport using OpenAI's Image API."""

    def __init__(
        self,
        *,
        endpoint: str = OPENAI_IMAGE_GENERATIONS_ENDPOINT,
        edit_endpoint: str = OPENAI_IMAGE_EDITS_ENDPOINT,
    ) -> None:
        self._endpoint = endpoint
        self._edit_endpoint = edit_endpoint
        self._host: HostAPI | None = None
        self._bridge: LayerTransferBridge | None = None

    def attach_host(self, host: HostAPI) -> None:
        self._host = host
        self._bridge = LayerTransferBridge(host)

    def detach_host(self) -> None:
        if self._bridge is not None:
            try:
                self._bridge.close()
            except Exception:
                logger.debug("GPT Image 2 layer bridge cleanup failed", exc_info=True)
        self._bridge = None
        self._host = None

    async def health(self) -> bool:
        return True

    def _require_bridge(self) -> LayerTransferBridge:
        if self._bridge is None:
            raise RuntimeError("Layer bridge not attached")
        return self._bridge

    def _resolve_layer(self, layer_id: str | None) -> LayerRef | None:
        if not self._host:
            return None
        canvas = getattr(self._host, "canvas", None)
        if not canvas:
            return None
        if layer_id is None:
            return canvas.current_layer()
        try:
            layers = canvas.get_layers()
        except Exception:
            layers = []
        for layer in layers:
            if layer.id == layer_id:
                return layer
        return None

    async def export_layer_png(self, layer_id: str) -> bytes:
        layer = self._resolve_layer(layer_id)
        if layer is None:
            raise ValueError(f"Input layer is no longer available: {layer_id}")
        snapshot = await self._require_bridge().capture_async(layer)
        return snapshot.data

    async def apply_image_to_layer(
        self,
        image_bytes: bytes,
        *,
        target_layer_id: str | None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        bridge = self._require_bridge()
        if not image_bytes:
            raise ValueError("No image data to apply")
        image_array = await bridge.decode_async(image_bytes)
        layer_ref = self._resolve_layer(target_layer_id)
        if target_layer_id and layer_ref is None:
            from loomstroke.host_api import LayerRef  # noqa: PLC0415

            layer_ref = LayerRef(id=target_layer_id, name=None)
        layer = bridge.ensure_layer(layer_ref, create_if_missing=True, name="GPT Image 2 Output")
        result = await bridge.apply_async(
            image_array,
            layer=layer,
            metadata={"source": "GPT Image 2", **(metadata or {})},
            merge_key=f"gptimage2_{layer.id}",
        )
        if not result.command_executed:
            raise RuntimeError("Failed to apply generated image to layer")

    async def generate(  # noqa: PLR0912
        self,
        prompt: str,
        options: GPTImage2Options,
        emit_progress: Callable[[str], None],
        cancelled: Callable[[], bool],
        *,
        api_key: str,
        input_pngs: Sequence[bytes] | None = None,
    ) -> bytes:  # noqa: PLR0912 - linear request payload / response handling
        if not api_key:
            raise RuntimeError("GPT Image 2 API key is missing")
        if cancelled():
            raise asyncio.CancelledError()
        images = normalize_input_pngs(input_pngs if input_pngs is not None else [])

        payload: dict[str, Any] = {
            "model": options.model,
            "prompt": prompt,
            "n": 1,
            "output_format": options.output_format,
        }
        if options.size != "auto":
            payload["size"] = options.size
        if options.quality != "auto":
            payload["quality"] = options.quality
        if options.background != "auto":
            payload["background"] = options.background
        if options.moderation != "auto":
            payload["moderation"] = options.moderation
        if options.output_compression is not None and options.output_format in {"jpeg", "webp"}:
            payload["output_compression"] = options.output_compression

        emit_progress(f"model={options.model}, size={options.size}, quality={options.quality}")
        headers = {"Authorization": f"Bearer {api_key}"}
        endpoint = self._endpoint
        request_kwargs: dict[str, Any]
        if images:
            endpoint = self._edit_endpoint
            form = aiohttp.FormData()
            for key, value in payload.items():
                form.add_field(key, str(value))
            for index, image in enumerate(images):
                form.add_field(
                    "image[]",
                    image,
                    filename=f"input-{index + 1}.png",
                    content_type="image/png",
                )
            request_kwargs = {"data": form}
        else:
            headers["Content-Type"] = "application/json"
            request_kwargs = {"json": payload}
        timeout = aiohttp.ClientTimeout(total=240)

        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.post(
                endpoint,
                headers=headers,
                **request_kwargs,
            ) as response,
        ):
            text = await response.text()
            if response.status >= 400:
                raise RuntimeError(_extract_openai_error(response.status, text))
            try:
                data = json.loads(text)
            except json.JSONDecodeError as exc:
                raise RuntimeError("OpenAI image generation returned invalid JSON") from exc

        if cancelled():
            raise asyncio.CancelledError()
        try:
            image_base64 = data["data"][0]["b64_json"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("OpenAI image generation returned no image data") from exc
        try:
            image_bytes = base64.b64decode(image_base64)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("OpenAI image generation returned invalid image data") from exc
        if not image_bytes:
            raise RuntimeError("OpenAI image generation returned empty image data")
        return _ensure_png_bytes(image_bytes, source_format=options.output_format)


__all__ = [
    "OPENAI_IMAGE_EDITS_ENDPOINT",
    "OPENAI_IMAGE_GENERATIONS_ENDPOINT",
    "IGPTImage2Transport",
    "OpenAIImageTransport",
    "_ensure_png_bytes",
]
