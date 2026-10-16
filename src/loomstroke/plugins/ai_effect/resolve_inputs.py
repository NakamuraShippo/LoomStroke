"""Host-side ResolveInputs adapter (Phase 6-d'-2).

Implements the ``ResolveInputs`` callable that ``PanelGenerationBinder``
(Phase 6-b3) expects. Job:

- Resolve ``state.gateway_id`` to an ``AIGateway`` via the registry.
- Walk ``state.slot_refs`` and turn each bound source layer into a
  ``SlotImage`` (PNG bytes + dimensions + exclude_blend flag).
- Collect per-layer ``texture_version`` numbers the controller uses in
  ``compute_param_hash`` so identical inputs hit the Gallery cache.

Scope MVP:
- Raster layers only. Vector / group / mask sources are skipped with
  a warning — the author needs to rasterize (or Phase 6-d'-3 will
  teach this module to rasterize on demand).
- ``source_page_id`` is always ``None``; multi-page document routing
  lands in a later phase.
"""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QColor, QImage, QPainter

from loomstroke.core.comic_panels.ai_image_context import panel_input_image, panel_input_version
from loomstroke.plugins.ai_effect.errors import AIError, AIGatewayError, ErrorClass
from loomstroke.plugins.ai_effect.gateway_protocol import AIGateway, SlotImage

logger = logging.getLogger(__name__)


class HostResolveInputs:
    """Callable adapter satisfying :data:`panel_controller.ResolveInputs`.

    Takes the canvas service + gateway registry by DI so unit tests
    can feed in stubs without standing up the full app. The callable
    signature is pinned by ``PanelGenerationBinder``:

        async (layer_id, state) -> (gateway, layer_versions, slot_images, source_page_id)
    """

    def __init__(self, *, canvas_service: Any, gateway_registry: Any) -> None:
        self._canvas_service = canvas_service
        self._gateway_registry = gateway_registry

    async def __call__(
        self,
        layer_id: str,
        state: Any,
    ) -> tuple[AIGateway, dict[str, int], dict[str, SlotImage], str | None]:
        gateway_id = str(getattr(state, "gateway_id", "") or "")
        try:
            gateway = self._gateway_registry.get(gateway_id)
        except Exception as exc:
            # registry.get raises UnknownGatewayError — wrap as an
            # AIGatewayError so the controller funnels it through the
            # standard error-surfacing path.
            raise AIGatewayError(
                AIError(
                    class_=ErrorClass.DEPENDENCY,
                    title="Unknown gateway",
                    detail=f"gateway_id={gateway_id!r} is not registered",
                )
            ) from exc

        slot_refs = getattr(state, "slot_refs", None)
        if not isinstance(slot_refs, dict):
            slot_refs = {}

        slot_images: dict[str, SlotImage] = {}
        encoded_inputs: dict[str, bytes] = {}
        referenced_ids: list[str] = []
        for internal_id, ref in slot_refs.items():
            if ref is None:
                continue
            target_id = getattr(ref, "layer_id", None)
            if not isinstance(target_id, str) or not target_id:
                continue
            referenced_ids.append(target_id)
            layer = self._find_layer(target_id)
            if layer is None:
                logger.warning(
                    "resolve_inputs: slot %s references missing layer %s",
                    internal_id,
                    target_id,
                )
                continue
            slot_image = _encode_layer_as_slot_image(
                layer,
                exclude_blend=bool(getattr(ref, "exclude_blend", False)),
                canvas_service=self._canvas_service,
            )
            if slot_image is None:
                logger.warning(
                    "resolve_inputs: slot %s source layer %s not encodable (type=%s)",
                    internal_id,
                    target_id,
                    getattr(layer, "layer_type", "?"),
                )
                continue
            slot_images[str(internal_id)] = slot_image
            encoded_inputs[target_id] = slot_image.png_bytes

        layer_versions = self._collect_layer_versions(referenced_ids, encoded_inputs)
        return gateway, layer_versions, slot_images, None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_layer(self, layer_id: str) -> Any | None:
        finder = getattr(self._canvas_service, "_find_layer_by_id_tree", None)
        if finder is None:
            return None
        try:
            return finder(layer_id)
        except Exception:
            logger.debug("resolve_inputs: layer lookup failed for %s", layer_id, exc_info=True)
            return None

    def _collect_layer_versions(
        self, layer_ids: list[str], encoded_inputs: dict[str, bytes] | None = None
    ) -> dict[str, int]:
        versions: dict[str, int] = {}
        for lid in layer_ids:
            layer = self._find_layer(lid)
            if layer is None:
                continue
            ver = getattr(layer, "texture_version", None)
            if isinstance(ver, int):
                versions[lid] = panel_input_version(
                    self._canvas_service, layer, ver, (encoded_inputs or {}).get(lid)
                )
        return versions


# ---------------------------------------------------------------------------
# PNG encoding
# ---------------------------------------------------------------------------


def _encode_layer_as_slot_image(
    layer: Any,
    *,
    exclude_blend: bool,
    canvas_service: Any | None = None,
) -> SlotImage | None:
    """Turn a pixel-bearing source layer into a :class:`SlotImage` payload.

    Supported source kinds:

    - **raster**: the canonical case — author-drawn pixels.
    - **ai_effect**: an AI Effect layer that has previously generated a
      raster result (``layer.image`` is populated when the preset uses
      ``mode == "self"`` or the layer's ``output_mode_override`` is
      ``"self"``). This lets one AI Effect's output feed another
      effect's input slot — a chain like sketch → depth-map (A) →
      stylized render (B), where B's input slot points at A. Without
      this branch, B fired with no image even though A had a valid
      generated result on disk and on screen.
    - **vector** (Phase 6-d'-3): auto-rasterized via
      ``canvas_service._rasterize_vector_layer`` when ``canvas_service``
      is supplied. The vector layer is NOT mutated — a fresh QImage
      snapshot is taken at canvas resolution and fed into the encode
      path, so the slot binding works without forcing the user to
      manually rasterize the layer first.

    Returns ``None`` for layer types still unsupported (group, mask)
    and for any layer that has no encodable image yet — callers log
    the miss so the author knows to generate the upstream effect or
    rasterize / flatten a group source first.

    **Alpha flattening over white.** Saves the layer as a PNG with the
    alpha channel pre-composited against a white background. Rationale:
    ComfyUI's ``LoadImage`` node does ``img.convert("RGB")`` on load,
    which drops alpha by compositing over **black**. A user who draws
    dark strokes on a fresh transparent layer sees "strokes on white
    canvas" in LoomStroke; ComfyUI would see "strokes on black" — the
    resulting near-uniform dark image has no contrast for LineArt /
    Canny / depth preprocessors, so ControlNet gets no guide and the
    generation ignores the author's sketch entirely.

    The dogfood symptom this fixes: "inputにレイヤーを指定しても
    ComfyUIに送信されていない" — the bytes *were* going out (upload
    log confirmed), but the payload was invisible once ComfyUI
    discarded the alpha channel.
    """
    image = _resolve_layer_image(layer, canvas_service)
    if image is None:
        return None

    if canvas_service is not None:
        image = panel_input_image(canvas_service, layer, image)
        if image is None:
            return None

    flattened = _flatten_against_white(image)
    png_bytes = _qimage_to_png(flattened)
    if png_bytes is None:
        return None
    # Diagnostic: surface rough pixel stats so a "content not reaching
    # ComfyUI" complaint can be triaged without reaching for a
    # debugger. ``bytes`` alone is ambiguous — a 100-byte PNG of pure
    # white and a 100-byte PNG of pure black look the same in a log.
    logger.info(
        "Slot image encoded: size=%dx%d png_bytes=%d (layer=%s, exclude_blend=%s)",
        flattened.width(),
        flattened.height(),
        len(png_bytes),
        getattr(layer, "id", "?"),
        exclude_blend,
    )
    return SlotImage(
        png_bytes=png_bytes,
        width=int(flattened.width()),
        height=int(flattened.height()),
        exclude_blend=bool(exclude_blend),
    )


def _resolve_layer_image(  # noqa: PLR0911, PLR0912 - layer-type guard chain
    layer: Any, canvas_service: Any | None
) -> QImage | None:
    """Pick the right ``QImage`` for a slot source, auto-rasterizing
    vector / group / mask layers on the fly.

    Layer-type dispatch:

    - ``raster`` / ``ai_effect``: return ``layer.image`` directly.
    - ``vector`` (with canvas_service): call
      ``canvas_service._rasterize_vector_layer(layer)`` for a fresh
      snapshot — doesn't mutate ``layer.vector_objects``.
    - ``group`` (Phase 6-d'-4, with canvas_service): call
      ``canvas_service._rasterize_group_layer(layer)`` which
      composites visible children. Cross-group references are not
      considered — only the children's own painting contributes.
    - ``mask`` (Phase 6-d'-4): convert the layer's alpha channel to
      a grayscale RGB image so ComfyUI ControlNet preprocessors
      (LineArt / Canny / depth) treat it as a guide.
    - Anything else (unknown): return ``None`` so the caller logs
      "not encodable" and the user knows to flatten manually.

    All branches narrow to a single ``QImage | None`` so the encode
    path stays unchanged.
    """
    layer_type = str(getattr(layer, "layer_type", "raster"))
    if layer_type in ("raster", "ai_effect"):
        image = getattr(layer, "image", None)
        if isinstance(image, QImage) and not image.isNull():
            return image
        return None
    if layer_type == "vector":
        return _auto_rasterize_via_canvas(
            layer,
            canvas_service,
            method_name="_rasterize_vector_layer",
            kind="vector",
        )
    if layer_type == "group":
        return _auto_rasterize_via_canvas(
            layer,
            canvas_service,
            method_name="_rasterize_group_layer",
            kind="group",
        )
    if layer_type == "mask":
        image = getattr(layer, "image", None)
        if not isinstance(image, QImage) or image.isNull():
            return None
        rgb = _alpha_to_grayscale_rgb(image)
        if rgb is None or rgb.isNull():
            return None
        logger.info(
            "Auto-rasterized mask layer %s for slot encode (size=%dx%d)",
            getattr(layer, "id", "?"),
            rgb.width(),
            rgb.height(),
        )
        return rgb
    return None


def _auto_rasterize_via_canvas(
    layer: Any,
    canvas_service: Any | None,
    *,
    method_name: str,
    kind: str,
) -> QImage | None:
    """Shared helper for vector / group auto-rasterize via the canvas
    service. Returns ``None`` and logs at debug for any failure mode
    (no service, missing method, raise, null image)."""
    if canvas_service is None:
        return None
    rasterizer = getattr(canvas_service, method_name, None)
    if not callable(rasterizer):
        return None
    try:
        snapshot = rasterizer(layer)
    except Exception:
        logger.debug(
            "%s auto-rasterize failed for layer %s",
            kind,
            getattr(layer, "id", "?"),
            exc_info=True,
        )
        return None
    if isinstance(snapshot, QImage) and not snapshot.isNull():
        logger.info(
            "Auto-rasterized %s layer %s for slot encode (size=%dx%d)",
            kind,
            getattr(layer, "id", "?"),
            snapshot.width(),
            snapshot.height(),
        )
        return snapshot
    return None


def _alpha_to_grayscale_rgb(image: QImage) -> QImage | None:
    """Project an ARGB image's alpha channel onto an opaque grayscale
    RGB QImage.

    Mask layers store their painted shape in the alpha channel; for
    AI Effect input we want a viewable RGB image where the alpha
    becomes brightness (white = painted, black = unpainted). The
    conversion goes through numpy because PySide6 lacks a direct
    "alpha-to-luminance" helper.

    Returns ``None`` if the input image cannot be converted (null
    pixel buffer, allocation failure).
    """
    import numpy as np  # noqa: PLC0415

    src = image.convertToFormat(QImage.Format.Format_ARGB32)
    if src.isNull():
        return None
    width = src.width()
    height = src.height()
    src_bits = src.bits()
    if src_bits is None:
        return None
    try:
        src_arr = np.frombuffer(src_bits, dtype=np.uint8).reshape((height, width, 4))
    except (BufferError, ValueError):
        return None
    alpha = src_arr[:, :, 3]
    out = QImage(width, height, QImage.Format.Format_RGB32)
    if out.isNull():
        return None
    out_bits = out.bits()
    if out_bits is None:
        return None
    out_arr = np.frombuffer(out_bits, dtype=np.uint8).reshape((height, width, 4))
    out_arr[:, :, 0] = alpha  # B
    out_arr[:, :, 1] = alpha  # G
    out_arr[:, :, 2] = alpha  # R
    out_arr[:, :, 3] = 255  # X (RGB32 ignores but keep deterministic)
    return out


def _flatten_against_white(image: QImage) -> QImage:
    """Pre-composite ``image`` over an opaque white background.

    Returns an ``RGB32`` (opaque) QImage with the same dimensions.
    Uses ``SourceOver`` painting so partially-transparent pixels blend
    correctly (anti-aliased stroke edges stay smooth). Input is not
    mutated; a fresh QImage is returned so callers can keep using the
    original.

    Why ``RGB32`` rather than keeping ``ARGB32_Premultiplied`` and just
    filling the alpha: downstream PNG encoding with an opaque backing
    format produces a strictly smaller, strictly simpler file that
    ComfyUI's ``img.convert("RGB")`` no-ops, removing another source
    of surprise behaviour across Pillow versions.
    """
    flattened = QImage(image.size(), QImage.Format.Format_RGB32)
    flattened.fill(QColor(255, 255, 255))
    painter = QPainter(flattened)
    try:
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.drawImage(0, 0, image)
    finally:
        painter.end()
    # Defensive: Qt can in theory fail mid-paint on an invalid source.
    # Preserve the opaque invariant by returning the explicitly-filled
    # fallback rather than a partially-painted result.
    if flattened.isNull():
        fallback = QImage(image.size(), QImage.Format.Format_RGB32)
        fallback.fill(Qt.GlobalColor.white)
        return fallback
    return flattened


def _qimage_to_png(image: QImage) -> bytes | None:
    """Serialise a QImage to PNG bytes via an in-memory QBuffer.

    Returns ``None`` if Qt reports a save failure — caller treats the
    slot as missing rather than propagating the failure upwards.
    """
    byte_array = QByteArray()
    buffer = QBuffer(byte_array)
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
        return None
    try:
        ok = image.save(buffer, "PNG")
    finally:
        buffer.close()
    if not ok:
        return None
    data = bytes(byte_array)
    return data or None


__all__ = ["HostResolveInputs"]
