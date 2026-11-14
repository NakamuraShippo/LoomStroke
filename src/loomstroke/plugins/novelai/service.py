"""NovelAI image API calls and command-backed layer transfer."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import zipfile
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

import aiohttp
from PIL import Image

from loomstroke.config.secret_provider import SecretProvider
from loomstroke.plugins.common.layer_bridge import LayerTransferBridge
from loomstroke.plugins.common.outbound_policy import ensure_network_services_consent

_API_ROOT = "https://image.novelai.net"
_MAX_RESPONSE_BYTES = 64 * 1024 * 1024
_MAX_CACHED_VIBE_BYTES = 4 * 1024 * 1024
_ADVANCED_PARAMETERS = frozenset(
    {
        "cfg_rescale",
        "color_correct",
        "deliberate_euler_ancestral_bug",
        "dynamic_thresholding",
        "extra_noise_seed",
        "image_format",
        "legacy",
        "noise_schedule",
        "params_version",
        "prefer_brownian",
        "qualityToggle",
        "sampler",
        "scale",
        "seed",
        "skip_cfg_above_sigma",
        "sm",
        "sm_dyn",
        "steps",
        "straight_alpha",
        "tag_hint_qt",
        "tag_hint_transparent_background",
        "tag_hint_uc_preset",
        "ucPreset",
        "v4_prompt",
        "v4_negative_prompt",
    }
)


async def _read_limited_response(response: aiohttp.ClientResponse, too_large_message: str) -> bytes:
    """Read the entire streamed body without exceeding the response cap."""
    data = bytearray()
    while True:
        remaining = _MAX_RESPONSE_BYTES + 1 - len(data)
        chunk = await response.content.read(min(64 * 1024, remaining))
        if not chunk:
            return bytes(data)
        data.extend(chunk)
        if len(data) > _MAX_RESPONSE_BYTES:
            raise ValueError(too_large_message)


def _first_image(data: bytes, content_type: str) -> bytes:
    if "json" in content_type:
        body = json.loads(data)
        entries = body.get("images") if isinstance(body, dict) else None
        if not isinstance(entries, list) or not entries:
            raise ValueError("NovelAI returned no image")
        encoded = entries[0].get("image") if isinstance(entries[0], dict) else None
        if not isinstance(encoded, str):
            raise ValueError("NovelAI returned an invalid image")
        data = base64.b64decode(encoded, validate=True)
    elif "zip" in content_type or zipfile.is_zipfile(io.BytesIO(data)):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = [
                info
                for info in archive.infolist()
                if info.filename.lower().endswith((".png", ".webp"))
            ]
            if not members:
                raise ValueError("NovelAI returned no image")
            if members[0].file_size > _MAX_RESPONSE_BYTES:
                raise ValueError("NovelAI image is too large")
            data = archive.read(members[0])
    return _normalize_image(data)


def _images(data: bytes, content_type: str) -> list[bytes]:
    return [_normalize_image(data) for data in _image_payloads(data, content_type)]


def _normalize_image(data: bytes) -> bytes:
    with Image.open(io.BytesIO(data)) as image:
        image.load()
        if image.width * image.height > 64_000_000:
            raise ValueError("NovelAI image is too large")
        output = io.BytesIO()
        image.convert("RGBA").save(output, format="PNG")
        return output.getvalue()


def _image_payloads(data: bytes, content_type: str) -> list[bytes]:
    images: list[bytes]
    if "json" in content_type:
        body = json.loads(data)
        entries = body.get("images") if isinstance(body, dict) else None
        if not isinstance(entries, list) or not entries:
            raise ValueError("NovelAI returned no image")
        images = []
        for entry in entries:
            encoded = entry.get("image") if isinstance(entry, dict) else None
            if not isinstance(encoded, str):
                raise ValueError("NovelAI returned an invalid image")
            images.append(base64.b64decode(encoded, validate=True))
    elif "zip" in content_type or zipfile.is_zipfile(io.BytesIO(data)):
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            members = [
                info
                for info in archive.infolist()
                if info.filename.lower().endswith((".png", ".webp"))
            ]
            if not members:
                raise ValueError("NovelAI returned no image")
            if len(members) > 4 or sum(info.file_size for info in members) > _MAX_RESPONSE_BYTES:
                raise ValueError("NovelAI images are too large")
            images = []
            for info in members:
                if info.file_size > _MAX_RESPONSE_BYTES:
                    raise ValueError("NovelAI image is too large")
                images.append(archive.read(info))
    else:
        images = [data]
    if len(images) > 4:
        raise ValueError("NovelAI returned too many images")
    return images


def _selection_png(canvas: Any, size: tuple[int, int]) -> bytes:
    from loomstroke.core.selection_state import SelectionStateService  # noqa: PLC0415

    getter = getattr(canvas, "get_selection_mask", None)
    mask = getter() if callable(getter) else None
    if mask is None or mask.isNull() or (mask.width(), mask.height()) != size:
        raise ValueError("Select an area on the source document before inpainting")
    pixels = SelectionStateService.selection_mask_to_numpy(mask)
    if not pixels.any():
        raise ValueError("The inpainting selection is empty")
    output = io.BytesIO()
    Image.fromarray(pixels.astype("uint8"), mode="L").save(output, format="PNG")
    return output.getvalue()


def _precise_reference_png(image_bytes: bytes) -> bytes:
    """Fit a reference to one of NovelAI's documented CR canvas sizes."""
    with Image.open(io.BytesIO(image_bytes)) as image:
        ratio = image.width / image.height
        size = (1024, 1536) if ratio < 0.8 else (1536, 1024) if ratio > 1.2 else (1472, 1472)
        source = image.convert("RGB")
        source.thumbnail(size, Image.Resampling.LANCZOS)
        padded = Image.new("RGB", size, "black")
        padded.paste(source, ((size[0] - source.width) // 2, (size[1] - source.height) // 2))
        output = io.BytesIO()
        padded.save(output, format="PNG")
        return output.getvalue()


class NovelAIService:
    """Capture inputs before reading the token, then apply results via commands."""

    def __init__(
        self,
        host: Any,
        *,
        secrets: SecretProvider | None = None,
        on_image_applied: (
            Callable[[Mapping[str, Any], bytes, int, int], Awaitable[None]] | None
        ) = None,
    ) -> None:
        self._host = host
        self._secrets = secrets or SecretProvider()
        self._bridge = LayerTransferBridge(host)
        self._on_image_applied = on_image_applied
        self._vibe_cache: dict[tuple[str, str, float], bytes] = {}

    def close(self) -> None:
        self._bridge.close()
        self._vibe_cache.clear()

    def _layer(self, layer_id: str) -> Any:
        for layer in self._host.canvas.get_layers():
            if layer.id == layer_id:
                return layer
        raise ValueError("The selected input layer is no longer available")

    async def capture_layer(self, layer_id: str) -> bytes:
        return (await self._bridge.capture_async(self._layer(layer_id))).data

    async def suggest_tags(self, query: str, model: str) -> list[str]:
        if not ensure_network_services_consent(self._host, service_name="NovelAI"):
            raise RuntimeError("External AI services are disabled")
        query = query.strip()
        if not query:
            raise ValueError("Enter a tag fragment")
        with self._secrets.use("novelai", username="api") as token:
            headers = {"Authorization": f"Bearer {token}"}
            params = {"model": model, "prompt": query, "lang": "jp"}
            async with (
                aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session,
                session.get(
                    _API_ROOT + "/ai/generate-image/suggest-tags",
                    params=params,
                    headers=headers,
                ) as response,
            ):
                if response.status != 200:
                    raise RuntimeError(f"NovelAI tag lookup failed (HTTP {response.status})")
                body = await response.json()
        tags = body.get("tags", []) if isinstance(body, dict) else []
        if not isinstance(tags, list):
            return []
        return [
            entry["tag"]
            for entry in tags
            if isinstance(entry, dict) and isinstance(entry.get("tag"), str)
        ][:10]

    async def run(self, request: Mapping[str, Any]) -> bytes:  # noqa: PLR0912, PLR0915
        if not ensure_network_services_consent(self._host, service_name="NovelAI"):
            raise RuntimeError("External AI services are disabled")
        kind = str(request.get("kind", "generate"))
        count = (
            int(request.get("n_samples", 1)) if kind in {"generate", "img2img", "inpaint"} else 1
        )
        if not 1 <= count <= 4:
            raise ValueError("Number of images must be between 1 and 4")
        source_id = request.get("source_layer_id")
        target_id = request.get("target_layer_id")
        if count > 1 and target_id is not None:
            raise ValueError("Select a new layer for multiple NovelAI images")
        prompt = str(request.get("prompt", "")).strip()
        if kind in {"generate", "img2img", "inpaint"} and not prompt:
            raise ValueError("Prompt is required")
        source: bytes | None = None
        mask: bytes | None = None
        reference: bytes | None = None
        vibes: list[tuple[bytes, float, float]] = []
        reference_mode = str(request.get("reference_mode", "none"))
        document = self._host.canvas.current_document()
        document_id = getattr(document, "id", None)
        pages = getattr(self._host, "pages", None)
        active_page = getattr(pages, "get_active_page_id", None)
        page_id = active_page() if callable(active_page) else None
        if kind in {"img2img", "inpaint", "augment", "upscale"}:
            variation_image = request.get("source_image_bytes") if kind == "img2img" else None
            if variation_image is not None:
                if (
                    not isinstance(variation_image, bytes)
                    or len(variation_image) > _MAX_RESPONSE_BYTES
                ):
                    raise ValueError("Invalid Gallery image")
                source = _normalize_image(variation_image)
            else:
                if not isinstance(source_id, str) or not source_id:
                    raise ValueError("Select an input layer")
                source = await self.capture_layer(source_id)
            if kind == "inpaint":
                with Image.open(io.BytesIO(source)) as image:
                    mask = _selection_png(self._host.canvas, image.size)
        if target_id is not None:
            self._layer(str(target_id))
        if reference_mode != "none" and kind in {"generate", "img2img", "inpaint"}:
            if not str(request.get("model", "")).startswith("nai-diffusion-4-5"):
                raise ValueError("References require a NovelAI V4.5 model")
            raw_vibes = request.get("vibes") or []
            if reference_mode == "vibe" and raw_vibes:
                if not isinstance(raw_vibes, list) or len(raw_vibes) > 16:
                    raise ValueError("Select at most 16 potions")
                for item in raw_vibes:
                    if not isinstance(item, dict) or not isinstance(item.get("layer_id"), str):
                        raise ValueError("Invalid potion layer")
                    strength = float(item.get("strength", 0.5))
                    extracted = float(item.get("information_extracted", 1.0))
                    if not 0 <= strength <= 1 or not 0 <= extracted <= 1:
                        raise ValueError("Potion settings must be between 0 and 1")
                    vibes.append((await self.capture_layer(item["layer_id"]), strength, extracted))
            else:
                reference_id = request.get("reference_layer_id")
                if not isinstance(reference_id, str) or not reference_id:
                    raise ValueError("Select a reference layer")
                reference = await self.capture_layer(reference_id)

        if kind in {"generate", "img2img", "inpaint"}:
            params: dict[str, Any] = {
                "width": int(request.get("width", 1024)),
                "height": int(request.get("height", 1024)),
                "steps": int(request.get("steps", 28)),
                "scale": float(request.get("scale", 5.0)),
                "sampler": str(request.get("sampler", "k_euler_ancestral")),
                "n_samples": count,
                "prompt": prompt,
                "negative_prompt": str(request.get("negative_prompt", "")),
                "v4_prompt": {
                    "caption": {"base_caption": prompt, "char_captions": []},
                    "use_coords": False,
                    "use_order": True,
                },
                "v4_negative_prompt": {
                    "caption": {
                        "base_caption": str(request.get("negative_prompt", "")),
                        "char_captions": [],
                    },
                    "use_coords": False,
                    "use_order": True,
                },
            }
            if request.get("seed") is not None:
                params["seed"] = int(request["seed"])
            if request.get("cfg_rescale") is not None:
                params["cfg_rescale"] = float(request["cfg_rescale"])
            if request.get("noise_schedule") is not None:
                params["noise_schedule"] = str(request["noise_schedule"])
            if request.get("variety"):
                if not str(request.get("model", "nai-diffusion-5-full")).startswith(
                    "nai-diffusion-4-5"
                ):
                    raise ValueError("Variety requires a NovelAI V4.5 model")
                params["skip_cfg_above_sigma"] = 58
            if source is not None:
                params.update(
                    image=base64.b64encode(source).decode("ascii"),
                    strength=float(request.get("strength", 0.7)),
                    noise=float(request.get("noise", 0.0)),
                )
            if mask is not None:
                params["mask"] = base64.b64encode(mask).decode("ascii")
            if reference is not None and reference_mode != "vibe":
                params["director_reference_images"] = [
                    base64.b64encode(_precise_reference_png(reference)).decode("ascii")
                ]
                params["director_reference_descriptions"] = [
                    {"caption": {"base_caption": reference_mode, "char_captions": []}}
                ]
                params["director_reference_strength_values"] = [
                    float(request.get("reference_strength", 0.5))
                ]
                params["director_reference_information_extracted"] = [1.0]
            advanced = request.get("advanced_parameters", "{}")
            if not isinstance(advanced, str) or len(advanced) > 16_384:
                raise ValueError("Advanced parameters must be a short JSON object")
            override = json.loads(advanced)
            if not isinstance(override, dict) or set(override) - _ADVANCED_PARAMETERS:
                raise ValueError("Advanced parameters contain unsupported fields")
            params.update(override)
            action = "generate"
            if mask is not None:
                action = "infill"
            elif source is not None:
                action = "img2img"
            model = str(request.get("model", "nai-diffusion-5-full"))
            if mask is not None:
                if model == "nai-diffusion-5-curated":
                    model = "nai-diffusion-4-5-curated-inpainting"
                elif not model.endswith("-inpainting"):
                    model += "-inpainting"
            payload = {
                "action": action,
                "input": prompt,
                "model": model,
                "parameters": params,
            }
            path = "/ai/generate-image"
        elif kind == "augment":
            if source is None:
                raise ValueError("Select an input layer")
            with Image.open(io.BytesIO(source)) as source_image:
                source_width, source_height = source_image.size
            payload = {
                "image": base64.b64encode(source).decode("ascii"),
                "prompt": prompt,
                "req_type": str(request.get("tool", "bg-removal")),
                "width": source_width,
                "height": source_height,
            }
            path = "/ai/augment-image"
        elif kind == "upscale":
            payload = {
                "image": base64.b64encode(source or b"").decode("ascii"),
                "model": str(request.get("model", "nai-diffusion-5-full")),
            }
            path = "/ai/upscale"
        else:
            raise ValueError("Unsupported NovelAI operation")

        with self._secrets.use("novelai", username="api") as token:
            headers = {
                "Authorization": f"Bearer {token}",
                "Accept": (
                    "application/json"
                    if kind in {"generate", "img2img", "inpaint"}
                    else "application/zip"
                ),
            }
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=180)) as session:
                if reference_mode == "vibe" and (vibes or reference is not None):
                    if not vibes and reference is not None:
                        vibes = [(reference, float(request.get("reference_strength", 0.5)), 1.0)]
                    encoded_vibes = []
                    for image, _strength, extracted in vibes:
                        key = (str(payload["model"]), hashlib.sha256(image).hexdigest(), extracted)
                        encoded = self._vibe_cache.get(key)
                        if encoded is None:
                            vibe_payload = {
                                "image": base64.b64encode(image).decode("ascii"),
                                "model": payload["model"],
                                "information_extracted": extracted,
                            }
                            async with session.post(
                                _API_ROOT + "/ai/encode-vibe",
                                json=vibe_payload,
                                headers={**headers, "Accept": "application/binary"},
                            ) as vibe_response:
                                if vibe_response.status not in (200, 201):
                                    raise RuntimeError(
                                        "NovelAI Vibe encoding failed "
                                        f"(HTTP {vibe_response.status})"
                                    )
                                encoded = await _read_limited_response(
                                    vibe_response, "NovelAI Vibe is too large"
                                )
                            if len(encoded) <= _MAX_CACHED_VIBE_BYTES:
                                if len(self._vibe_cache) >= 8:
                                    self._vibe_cache.pop(next(iter(self._vibe_cache)))
                                self._vibe_cache[key] = encoded
                        encoded_vibes.append(base64.b64encode(encoded).decode("ascii"))
                    params = payload["parameters"]
                    params["reference_image_multiple"] = encoded_vibes
                    params["reference_strength_multiple"] = [strength for _, strength, _ in vibes]
                    params["reference_information_extracted_multiple"] = [
                        extracted for _, _, extracted in vibes
                    ]
                async with session.post(
                    _API_ROOT + path, json=payload, headers=headers
                ) as response:
                    # The image endpoint can return 200 in practice even
                    # though its published success response is 201.
                    if response.status not in (200, 201):
                        raise RuntimeError(f"NovelAI request failed (HTTP {response.status})")
                    data = await _read_limited_response(response, "NovelAI response is too large")
                    content_type = response.headers.get("Content-Type", "")
                    image_bytes_list = (
                        _images(data, content_type)
                        if kind in {"generate", "img2img", "inpaint"}
                        else [_first_image(data, content_type)]
                    )
        if len(image_bytes_list) != count:
            raise ValueError("NovelAI returned an unexpected number of images")
        image_arrays = [await self._bridge.decode_async(data) for data in image_bytes_list]
        if getattr(self._host.canvas.current_document(), "id", None) != document_id:
            raise RuntimeError("The document changed during NovelAI generation")
        if callable(active_page) and active_page() != page_id:
            raise RuntimeError("The page changed during NovelAI generation")
        if target_id is None:
            for index, image_array in enumerate(image_arrays, start=1):
                if getattr(self._host.canvas.current_document(), "id", None) != document_id:
                    raise RuntimeError("The document changed during NovelAI generation")
                if callable(active_page) and active_page() != page_id:
                    raise RuntimeError("The page changed during NovelAI generation")
                name = "NovelAI Output" if count == 1 else f"NovelAI Output {index}"
                await self._bridge.apply_new_image_async(
                    image_array, name=name, metadata={"source": "NovelAI"}
                )
                if self._on_image_applied is not None:
                    await self._on_image_applied(request, image_bytes_list[index - 1], index, count)
        else:
            target = self._layer(str(target_id))
            result = await self._bridge.apply_async(
                image_arrays[0], layer=target, metadata={"source": "NovelAI"}
            )
            if not result.command_executed:
                raise RuntimeError("Failed to apply NovelAI image")
            if self._on_image_applied is not None:
                await self._on_image_applied(request, image_bytes_list[0], 1, 1)
        return image_bytes_list[0]
