"""Transport layer for NanoBanana Google Gemini integration."""

from __future__ import annotations

import asyncio
import importlib
import logging
import math
from collections.abc import Callable
from io import BytesIO
from typing import TYPE_CHECKING, Protocol

from loomstroke.plugins.common.layer_bridge import LayerTransferBridge

try:  # Pillow is optional runtime dependency
    from PIL import Image  # type: ignore[import-not-found]  # optional dependency
except ImportError:  # pragma: no cover - handled in health()
    Image = None  # type: ignore

try:  # httpx is a transitive dependency via google-genai; if absent the
    # google import itself fails earlier and we never reach this branch.
    # Cached as a module reference so the retry loop can ``isinstance``
    # against ``httpx.TransportError`` without re-importing on every attempt.
    import httpx as _httpx  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover - google-genai pulls httpx in
    _httpx = None  # type: ignore

from .model_config import image_config_kwargs, supports_google_search, thinking_config_kwargs
from .types import ASPECT_TO_RESOLUTION, DEFAULT_MODEL, NanoBananaOptions


def _is_image_only_model(model: str) -> bool:
    """Legacy name classifier; API capabilities are defined in model_config.

    The word ``image`` does not imply that thinking or search is unsupported.
    """
    return "image" in model.lower()


# User report (2026-04-30): a 1.3 MB PNG input caused the Gemini API
# to drop the connection mid-request (``httpx.RemoteProtocolError:
# Server disconnected without sending a response``) after a 60 s
# stall. The official spec allows up to 7 MB per inline image but in
# practice the endpoint disconnects unpredictably above ~1 MB.
# Re-encode oversize inputs as JPEG (quality 85) before sending so
# the payload stays well under the soft limit; image-to-image
# conditioning quality is unaffected at this rate.
_INLINE_IMAGE_BYTE_LIMIT = 1024 * 1024  # 1 MiB
_JPEG_QUALITY_STEPS = (85, 75, 65, 55, 45)
_MIN_COMPRESSED_EDGE = 512


def _encode_jpeg(image: object, *, quality: int) -> bytes:
    buf = BytesIO()
    image.save(buf, format="JPEG", quality=quality, optimize=True)  # type: ignore[attr-defined]
    return buf.getvalue()


def _compress_rgb_image_for_api(image: object) -> bytes:
    """Encode an RGB PIL image under the inline payload soft limit."""
    best = b""
    best_len = math.inf
    current = image
    while True:
        for quality in _JPEG_QUALITY_STEPS:
            encoded = _encode_jpeg(current, quality=quality)
            if len(encoded) < best_len:
                best = encoded
                best_len = len(encoded)
            if len(encoded) <= _INLINE_IMAGE_BYTE_LIMIT:
                return encoded

        width, height = current.size  # type: ignore[attr-defined]
        if width <= _MIN_COMPRESSED_EDGE and height <= _MIN_COMPRESSED_EDGE:
            return best
        scale = math.sqrt(_INLINE_IMAGE_BYTE_LIMIT / max(1, best_len)) * 0.92
        scale = max(0.35, min(0.85, scale))
        new_width = (
            max(_MIN_COMPRESSED_EDGE, int(width * scale)) if width > _MIN_COMPRESSED_EDGE else width
        )
        new_height = (
            max(_MIN_COMPRESSED_EDGE, int(height * scale))
            if height > _MIN_COMPRESSED_EDGE
            else height
        )
        new_size = (
            new_width,
            new_height,
        )
        if new_size == (width, height):
            return best
        resampling = getattr(Image, "Resampling", Image).LANCZOS
        current = current.resize(new_size, resampling)  # type: ignore[attr-defined]


def _compress_image_for_api(png: bytes) -> tuple[bytes, str]:
    """Re-encode oversized PNG inputs to JPEG for the Gemini API.

    Returns ``(bytes, mime_type)``. PNGs at or below the limit are
    returned as-is so the lossless path is preserved for small
    images. Above the limit the alpha channel is composited onto a
    white background (Gemini ignores alpha on the image input slot
    anyway) and the result is encoded as JPEG, reducing quality and
    dimensions only as far as needed to land below the soft limit.
    """
    if len(png) <= _INLINE_IMAGE_BYTE_LIMIT:
        return png, "image/png"
    if Image is None:
        # Pillow missing — let the SDK try the raw PNG. Health check
        # gates this earlier in the pipeline so reaching here is a
        # config edge case rather than a real-world path.
        return png, "image/png"
    with Image.open(BytesIO(png)) as img:
        if img.mode in ("RGBA", "LA"):
            background = Image.new("RGB", img.size, (255, 255, 255))
            background.paste(img, mask=img.split()[-1])
            converted = background
        elif img.mode != "RGB":
            converted = img.convert("RGB")
        else:
            converted = img.copy()
    return _compress_rgb_image_for_api(converted), "image/jpeg"


class _NoImageReturned(RuntimeError):
    """Internal: Gemini answered 200 OK but the response carried no image.

    ``retryable`` is True for transient cases (empty response with no
    block reason and no text payload) — those are typically dropouts of
    the preview image-generation models and recover within a couple of
    retries. False for deterministic ones (safety blocks, model chose
    text-only response) where retrying just wastes quota.
    """

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


if TYPE_CHECKING:
    from loomstroke.host_api import HostAPI, LayerRef

logger = logging.getLogger(__name__)


class INanoBananaTransport(Protocol):
    async def health(self) -> bool: ...

    async def export_layer_png(self, layer_id: str) -> bytes: ...

    async def apply_png_to_layer(
        self, png: bytes, *, target_layer_id: str | None, metadata: dict[str, object] | None = None
    ) -> None: ...

    async def generate(
        self,
        prompt: str,
        input_pngs: list[bytes],
        options: NanoBananaOptions,
        emit_progress: Callable[[str], None],
        cancelled: Callable[[], bool],
        *,
        api_key: str,
    ) -> bytes: ...


class GoogleGenAITransport(INanoBananaTransport):
    """Concrete transport using the google-genai SDK."""

    def __init__(self, *, model: str = DEFAULT_MODEL) -> None:
        # Phase 8a (NanoBanana) "read-on-demand" policy: API key is
        # NEVER stored on the transport. ``generate()`` accepts the
        # key as a parameter so it lives only inside that call's
        # local scope. Constructing a Transport up-front is fine —
        # without the key it cannot reach Gemini, so an idle
        # Transport is harmless.
        self._model = model
        self._host: HostAPI | None = None
        self._bridge: LayerTransferBridge | None = None

    # ------------------------------------------------------------------
    def attach_host(self, host: HostAPI) -> None:
        self._host = host
        self._bridge = LayerTransferBridge(host)

    def detach_host(self) -> None:
        if self._bridge is not None:
            try:
                self._bridge.close()
            except Exception:  # pragma: no cover - defensive cleanup
                logger.debug("Layer bridge cleanup failed", exc_info=True)
        self._bridge = None
        self._host = None

    # ------------------------------------------------------------------
    async def health(self) -> bool:
        # Health no longer reflects API key presence — that is checked
        # at Generate time via ``NanoBananaService._is_api_key_configured``
        # (which uses ``SecurityConfig.has_credential``, no decryption
        # exposure). Health here is purely "are the libraries installed?"
        if Image is None:
            logger.debug("Pillow is not installed; NanoBanana transport unhealthy")
            return False
        if importlib.util.find_spec("google") is None:
            logger.debug("google-genai is not installed")
            return False
        return True

    # ------------------------------------------------------------------
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
        except Exception:  # pragma: no cover - host fallback
            layers = []
        for layer in layers:
            if layer.id == layer_id:
                return layer
        return None

    async def export_layer_png(self, layer_id: str) -> bytes:
        bridge = self._require_bridge()
        layer = self._resolve_layer(layer_id)
        snapshot = await bridge.capture_async(layer)
        return snapshot.data

    async def apply_png_to_layer(
        self, png: bytes, *, target_layer_id: str | None, metadata: dict[str, object] | None = None
    ) -> None:
        bridge = self._require_bridge()
        if not png:
            raise ValueError("No image data to apply")
        image_array = await bridge.decode_async(png)
        layer_ref = self._resolve_layer(target_layer_id)
        if target_layer_id and layer_ref is None:
            # Build lightweight placeholder for ensure_layer.
            # Local import to avoid cycles.
            from loomstroke.host_api import LayerRef  # noqa: PLC0415

            layer_ref = LayerRef(id=target_layer_id, name=None)
        layer = bridge.ensure_layer(layer_ref, create_if_missing=True, name="NanoBanana Output")
        result = await bridge.apply_async(
            image_array,
            layer=layer,
            metadata={"source": "NanoBanana", **(metadata or {})},
            merge_key=f"nanobanana_{layer.id}",
        )
        if not result.command_executed:
            raise RuntimeError("Failed to apply generated image to layer")

    async def generate(  # noqa: PLR0912, PLR0915 - retry-loop fan-out
        self,
        prompt: str,
        input_pngs: list[bytes],
        options: NanoBananaOptions,
        emit_progress: Callable[[str], None],
        cancelled: Callable[[], bool],
        *,
        api_key: str,
    ) -> bytes:
        if Image is None:
            raise RuntimeError("Pillow is required for NanoBanana generation")
        try:
            from google import genai  # type: ignore  # noqa: PLC0415
            from google.genai import types  # type: ignore  # noqa: PLC0415
        except ImportError:
            # google namespace may be incomplete due to user-level packages
            # shadowing the venv.  Fix the path and retry once.
            import importlib  # noqa: PLC0415
            import sys  # noqa: PLC0415
            from pathlib import Path as _Path  # noqa: PLC0415

            try:
                import google  # noqa: PLC0415
            except ImportError as exc2:
                raise RuntimeError("google-genai is not installed") from exc2

            # Try multiple candidate paths for the venv's google package
            candidates = [
                _Path(sys.prefix) / "Lib" / "site-packages" / "google",
            ]
            exe_dir = _Path(sys.executable).resolve().parent
            if exe_dir.name.lower() == "scripts":
                candidates.append(exe_dir.parent / "Lib" / "site-packages" / "google")
            for cand in candidates:
                cand_str = str(cand)
                if cand_str not in google.__path__ and (cand / "genai").is_dir():
                    google.__path__.append(cand_str)
            importlib.invalidate_caches()
            try:
                from google import genai  # type: ignore  # noqa: PLC0415
                from google.genai import types  # type: ignore  # noqa: PLC0415
            except ImportError as exc2:
                logger.error(
                    "Failed to import google.genai: google.__path__=%s",
                    google.__path__,
                )
                raise RuntimeError("google-genai is not installed") from exc2

        if cancelled():
            raise asyncio.CancelledError()

        # api_key arrives as a kw-only parameter from the caller's
        # short-lived scope (NanoBananaService._run_job). The
        # transport never stores it on self — the local variable
        # lives only for the duration of this call.
        if not api_key:
            raise RuntimeError("NanoBanana API key is missing")

        aspect = options.aspect_ratio
        primary_model = options.model or self._model
        emit_progress(f"model={primary_model}, aspect={aspect}")

        def _prepare_contents() -> list[object]:
            # User report (2026-04-30): a NanoBanana request failed
            # with the model returning a text-only response —
            # "マージする2枚の画像を提供してください。" — even though
            # both input images encoded fine and the request hit the
            # API as 200 OK. The previous code passed PIL ``Image``
            # objects directly into ``contents``, which works on some
            # google-genai versions but is NOT the documented
            # contract: the official examples build image parts via
            # ``types.Part.from_bytes(data=..., mime_type=...)`` so the
            # SDK serialises them as ``inline_data`` and the model
            # recognises them as image inputs rather than dropping
            # them silently. Use the documented form so we never
            # depend on SDK auto-detection of PIL types.
            contents: list[object] = [prompt]
            for png in input_pngs:
                if cancelled():
                    raise asyncio.CancelledError()
                # Compress oversize PNGs to JPEG before sending. The
                # endpoint disconnects mid-request when individual
                # inline image payloads exceed ~1 MiB; see
                # ``_INLINE_IMAGE_BYTE_LIMIT``. Small PNGs flow
                # through unchanged so the lossless path is preserved.
                payload, mime = _compress_image_for_api(png)
                part = types.Part.from_bytes(data=payload, mime_type=mime)
                contents.append(part)
            return contents

        contents = await asyncio.to_thread(_prepare_contents)

        resolution = ASPECT_TO_RESOLUTION.get(aspect)
        size_label = options.image_size or "auto"
        emit_progress(
            f"resolution={resolution[0]}x{resolution[1]}, size={size_label}"
            if resolution
            else f"aspect={aspect}, size={size_label}"
        )

        def _invoke(model: str) -> bytes:  # noqa: PLR0912, PLR0915 - linear response-walking
            client = genai.Client(api_key=api_key)

            cfg_kwargs: dict[str, object] = {
                "response_modalities": (
                    ["Image"] if options.modality == "Image" else ["Text", "Image"]
                ),
                "image_config": types.ImageConfig(**image_config_kwargs(model, options)),
            }

            thinking_kwargs = thinking_config_kwargs(model, options)
            if thinking_kwargs:
                cfg_kwargs["thinking_config"] = types.ThinkingConfig(**thinking_kwargs)

            if options.google_search and supports_google_search(model):
                cfg_kwargs["tools"] = [{"google_search": {}}]

            response = client.models.generate_content(
                model=model,
                contents=contents,
                config=types.GenerateContentConfig(**cfg_kwargs),
            )
            # Walk the response twice: first looking for an image part
            # (the happy path), then collecting text + finish_reason
            # for diagnostics if no image arrived. Gemini sometimes
            # returns 200 OK with text-only candidates when the prompt
            # tripped a safety filter or the model decided not to emit
            # an image — surfacing that text in the error message
            # turns "No image returned from Gemini" from a dead-end
            # message into something the user can act on.
            text_fragments: list[str] = []
            finish_reasons: list[str] = []
            block_reasons: list[str] = []
            for cand in getattr(response, "candidates", []) or []:
                finish = getattr(cand, "finish_reason", None)
                if finish:
                    finish_reasons.append(str(finish))
                content = getattr(cand, "content", None)
                if not content:
                    continue
                for part in getattr(content, "parts", []) or []:
                    if getattr(part, "thought", False):
                        continue
                    inline = getattr(part, "inline_data", None)
                    if inline is not None:
                        with Image.open(BytesIO(inline.data)) as img:
                            buf = BytesIO()
                            img.save(buf, format="PNG")
                            return buf.getvalue()
                    part_text = getattr(part, "text", None)
                    if isinstance(part_text, str) and part_text.strip():
                        text_fragments.append(part_text.strip())
            prompt_feedback = getattr(response, "prompt_feedback", None)
            if prompt_feedback is not None:
                block = getattr(prompt_feedback, "block_reason", None)
                if block:
                    block_reasons.append(str(block))
            detail_parts: list[str] = []
            if finish_reasons:
                detail_parts.append(f"finish_reason={','.join(finish_reasons)}")
            if block_reasons:
                detail_parts.append(f"block_reason={','.join(block_reasons)}")
            if text_fragments:
                snippet = " | ".join(text_fragments)
                if len(snippet) > 500:
                    snippet = snippet[:500] + "…"
                detail_parts.append(f"text={snippet!r}")
            detail = " " + " ".join(detail_parts) if detail_parts else ""
            logger.warning(
                "Gemini returned no image. model=%s%s",
                model,
                f" details: {' '.join(detail_parts)}" if detail_parts else "",
            )
            # Retry only when the response was empty (no block reason,
            # no text part). Safety blocks and text-only refusals are
            # deterministic — retrying just burns quota and the user's
            # patience. The transient "NO_IMAGE with no payload" case
            # recovers on the preview image models within 1-2 retries.
            retryable = not block_reasons and not text_fragments
            raise _NoImageReturned(f"No image returned from Gemini.{detail}", retryable=retryable)

        # Retry loop for transient "no image" responses. The Gemini
        # preview image models (gemini-3.x-flash-image-preview) drop
        # ~10-20% of requests with FinishReason.NO_IMAGE and no other
        # signal; a fresh request usually succeeds. Cap retries at 2
        # extra attempts so a deterministic failure surfaces quickly
        # rather than stalling 3x on a safety block (the ``retryable``
        # flag short-circuits those cases anyway).
        #
        # Same loop also recovers from server-side connection drops
        # (``httpx.RemoteProtocolError`` / other ``httpx.TransportError``
        # subclasses). User report (2026-04-30): a Text+Image request
        # failed once with "Server disconnected without sending a
        # response." even though google-genai's internal tenacity
        # retries had already fired — those retries are conservative
        # and don't always cover RemoteProtocolError. Manual triggers
        # also default to ``max_retries_manual=0`` at the AI Effect
        # controller level, so a single network blip surfaces straight
        # to the user. Treat transport errors the same way as empty
        # responses: one transient failure must not look like a hard
        # failure to a user who clicked Generate once.
        max_attempts = 3
        retry_delay_s = 1.5
        model_attempts: list[tuple[str, int]] = [(primary_model, max_attempts)]
        if primary_model != DEFAULT_MODEL:
            model_attempts.append((DEFAULT_MODEL, 1))
        last_error: BaseException | None = None
        result_bytes: bytes | None = None
        for model_index, (attempt_model, attempts_for_model) in enumerate(model_attempts):
            if model_index > 0:
                emit_progress(f"Retrying with {attempt_model}")
                logger.info(
                    "NanoBanana fallback model after transient failures: %s -> %s",
                    primary_model,
                    attempt_model,
                )
            for attempt in range(attempts_for_model):
                if cancelled():
                    raise asyncio.CancelledError()
                try:
                    result_bytes = await asyncio.to_thread(_invoke, attempt_model)
                    break
                except _NoImageReturned as exc:
                    last_error = exc
                    if not exc.retryable or attempt == attempts_for_model - 1:
                        break
                    emit_progress(
                        f"Empty response — retrying ({attempt + 1}/{attempts_for_model - 1})"
                    )
                    logger.info(
                        "NanoBanana retry attempt=%d/%d model=%s after empty response",
                        attempt + 1,
                        attempts_for_model - 1,
                        attempt_model,
                    )
                    await asyncio.sleep(retry_delay_s * (attempt + 1))
                except Exception as exc:  # noqa: BLE001 - filtered to httpx.TransportError below
                    # Only retry httpx-level transport failures (connection
                    # drop / timeout / protocol error). Everything else
                    # (assertion errors, programmer bugs, SDK bugs) re-raises
                    # so the caller sees the real cause. ``_httpx`` may be
                    # None on a stripped install; the ``isinstance`` short
                    # circuits cleanly in that case.
                    if _httpx is None or not isinstance(exc, _httpx.TransportError):
                        raise
                    last_error = exc
                    if attempt == attempts_for_model - 1:
                        break
                    emit_progress(
                        f"Server disconnected — retrying ({attempt + 1}/{attempts_for_model - 1})"
                    )
                    logger.info(
                        "NanoBanana retry attempt=%d/%d model=%s after %s: %s",
                        attempt + 1,
                        attempts_for_model - 1,
                        attempt_model,
                        type(exc).__name__,
                        exc,
                    )
                    await asyncio.sleep(retry_delay_s * (attempt + 1))
            if result_bytes is not None:
                break
            if isinstance(last_error, _NoImageReturned) and not last_error.retryable:
                break

        if result_bytes is None:
            if isinstance(last_error, _NoImageReturned):
                raise RuntimeError(str(last_error))
            if last_error is not None:
                # Re-raise the original exception so the existing
                # service-level handler keeps its informative message
                # and ``__cause__`` chain — the user sees the real
                # network error rather than a generic wrapper.
                raise last_error
            raise RuntimeError("Gemini returned no image")
        if not result_bytes:
            raise RuntimeError("Gemini returned empty payload")
        return result_bytes


__all__ = [
    "GoogleGenAITransport",
    "INanoBananaTransport",
]
