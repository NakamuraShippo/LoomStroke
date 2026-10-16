"""Full-carrier filter evaluation for the filter pen's dedicated worker."""

from __future__ import annotations

import copy
from types import SimpleNamespace
from typing import Any

import numpy as np
from PySide6.QtGui import QImage

from loomstroke.core.filters import FilterRegistry

from .device_manager import WgpuDeviceManager
from .filter_processor import WgpuFilterProcessor
from .filter_raster_context import FilterRasterContext
from .readback import align_bytes_per_row
from .texture_manager import WgpuTextureManager
from .types import WgpuRenderTargetHandle

_SOURCE_ID = "__effect_pen_source__"


class FilterPenGpuProcessor:
    """Evaluate filters without borrowing a live canvas device or clipping pixels.

    Construct, use and close this object on one persistent worker. GPU resources
    are created lazily there, and reused between strokes. The caller supplies an
    isolated image and performs all canvas mutations/history on the main thread.
    Only registered plugin filters without WGSL use their existing CPU hook;
    built-in filters always use the production wgpu filter implementation.
    """

    def __init__(self, device_manager: WgpuDeviceManager | None = None) -> None:
        self._device_manager = device_manager or WgpuDeviceManager()
        self._bundle: Any | None = None
        self._textures: WgpuTextureManager | None = None
        self._filters: WgpuFilterProcessor | None = None
        self._source_version = 0
        self._closed = False

    @property
    def _effects(self) -> WgpuFilterProcessor | None:
        """Legacy worker attribute shares the canonical processor."""
        return self._filters

    @_effects.setter
    def _effects(self, value: WgpuFilterProcessor | None) -> None:
        self._filters = value

    def filter_image(
        self,
        source: QImage,
        filter_type: str | None = None,
        params: dict[str, Any] | None = None,
        *,
        document_dpi: float,
        layer_origin: tuple[int, int],
        canvas_size: tuple[int, int],
        effect_type: str | None = None,
    ) -> QImage:
        """Return the filtered full layer, including its off-canvas pixels.

        Mosaic's grid remains local to the carrier, as with Add Filter Layer.
        Screentone receives the original carrier offset and document DPI, keeping
        page/local/custom screen origins identical to the ordinary filter path.
        No selection or stroke mask participates in this precomputation.
        """
        if effect_type is not None:
            if filter_type is not None and filter_type != effect_type:
                raise TypeError("Conflicting filter_type and effect_type")
            filter_type = effect_type
        if filter_type is None or params is None:
            raise TypeError("filter_image() requires filter_type and params")
        if self._closed:
            raise RuntimeError("Filter pen processor is closed.")
        if source.isNull():
            raise ValueError("Filter pen source image is empty.")
        filter_class = FilterRegistry.get(filter_type)
        if filter_class is None:
            raise ValueError(f"Unknown filter pen filter: {filter_type}")
        image_filter = filter_class()
        params_copy = copy.deepcopy(params)
        if not WgpuFilterProcessor.supports(filter_type) and not image_filter.wgsl_shader_source():
            # This is the BaseFilter compatibility contract, not a canvas render
            # fallback. Give the plugin private inputs and propagate failures.
            result = image_filter.apply(source.copy(), params_copy, None)
        else:
            result = self._filter_gpu(
                source,
                filter_type,
                params_copy,
                document_dpi=document_dpi,
                layer_origin=layer_origin,
                canvas_size=canvas_size,
            )
        if not isinstance(result, QImage) or result.isNull() or result.size() != source.size():
            raise ValueError("Filter pen filter returned an invalid image.")
        return result.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied).copy()

    def _filter_gpu(
        self,
        source: QImage,
        filter_type: str,
        params: dict[str, Any],
        *,
        document_dpi: float,
        layer_origin: tuple[int, int],
        canvas_size: tuple[int, int],
    ) -> QImage:
        owned: list[WgpuRenderTargetHandle] = []
        try:
            self._initialize_gpu()
            assert self._textures is not None and self._filters is not None
            assert self._bundle is not None
            self._source_version += 1
            layer = SimpleNamespace(
                id=_SOURCE_ID,
                image=source,
                texture_version=self._source_version,
                offset_x=0,
                offset_y=0,
                filter_type=filter_type,
                filter_params=params,
                filter_mask=None,
                opacity=1.0,
            )
            texture, _, _ = self._textures.sync_layer_texture(layer)
            encoder = self._bundle.device.create_command_encoder()

            def create_target(width: int, height: int) -> WgpuRenderTargetHandle:
                target = self._create_target(width, height)
                owned.append(target)
                return target

            result = self._filters.apply_layer_filter(
                encoder,
                texture,
                layer,
                create_target=create_target,
                use_cache=False,
                document_dpi=document_dpi,
                layer_origin=layer_origin,
                raster_context=FilterRasterContext(origin=(0, 0), canvas_size=canvas_size),
            )
            owned.extend(result.owned_targets)
            if result.texture is texture:
                return source.copy()
            self._bundle.queue.submit([encoder.finish()])
            rgba = self._read_straight_rgba(result.texture)
            return QImage(
                rgba.data,
                rgba.shape[1],
                rgba.shape[0],
                int(rgba.strides[0]),
                QImage.Format.Format_RGBA8888,
            ).copy()
        except Exception:
            # Do not retain outputs of a failed submission or a lost device.
            self._release_gpu()
            raise
        finally:
            seen: set[int] = set()
            for target in owned:
                key = id(target.texture)
                if key not in seen:
                    seen.add(key)
                    target.texture.destroy()

    def _initialize_gpu(self) -> None:
        if self._bundle is None:
            self._bundle = self._device_manager.get_bundle()
            self._textures = WgpuTextureManager(self._bundle)
            self._filters = WgpuFilterProcessor(self._bundle)

    def _create_target(self, width: int, height: int) -> WgpuRenderTargetHandle:
        assert self._bundle is not None
        wgpu = self._bundle.wgpu
        texture = self._bundle.device.create_texture(
            size=(width, height, 1),
            usage=(
                wgpu.TextureUsage.RENDER_ATTACHMENT
                | wgpu.TextureUsage.COPY_SRC
                | wgpu.TextureUsage.TEXTURE_BINDING
            ),
            format=wgpu.TextureFormat.rgba8unorm,
        )
        return WgpuRenderTargetHandle(texture, texture.create_view(), width, height)

    def _read_straight_rgba(self, target: WgpuRenderTargetHandle) -> np.ndarray:
        """Read filter texels; compositor readback would unpremultiply twice.

        WgpuTextureManager and filter passes use straight RGBA; only the final
        compositor output is premultiplied. WgpuReadback is for that output.
        """
        assert self._bundle is not None
        row_bytes = align_bytes_per_row(target.width * 4)
        raw = self._bundle.queue.read_texture(
            {"texture": target.texture, "origin": (0, 0, 0)},
            {"offset": 0, "bytes_per_row": row_bytes, "rows_per_image": target.height},
            (target.width, target.height, 1),
        )
        rows = np.frombuffer(raw, dtype=np.uint8).reshape(target.height, row_bytes)
        return rows[:, : target.width * 4].reshape(target.height, target.width, 4).copy()

    def _release_gpu(self) -> None:
        if self._textures is not None:
            self._textures.release()
        if self._filters is not None:
            self._filters.release()
        self._textures = None
        self._filters = None
        self._bundle = None
        self._device_manager.release()

    def close(self) -> None:
        """Release all owned resources on the worker; closing twice is harmless."""
        if not self._closed:
            self._release_gpu()
            self._closed = True


EffectPenGpuProcessor = FilterPenGpuProcessor
EffectRegistry = FilterRegistry
WgpuEffectProcessor = WgpuFilterProcessor
EffectRasterContext = FilterRasterContext
