"""Comic Tools plugin: panel-layout tool + balloon tool + dialogue panel.

Responsibilities are split (CT-01/CT-04):
- pure structure/geometry: ``layout_tree`` / ``model`` / ``text_frames``
- undoable mutations: ``commands.ComicEditCommand`` via ``service``
- this module: HostAPI integration, pointer/tool state, canvas overlay
- ``panel_widget``: the dock UI (Panels / Balloon / Dialogue tabs)
"""

from __future__ import annotations

import copy
import logging
from collections.abc import Iterable
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from loomstroke.core.canvas_meta_transform_registry import CanvasMetaTransformRegistry
from loomstroke.core.image_size.vector_meta_scaler_registry import VectorMetaScalerRegistry
from loomstroke.plugins.sdk.base import PluginMetadata, PluginType
from loomstroke.plugins.sdk.tool import ToolDescriptor

from . import layout_tree as lt
from .balloonize import BalloonizeError
from .balloonize_preserve import (
    PreserveProposal,
    build_preserve_command,
    build_preserve_record,
    propose_preserve,
)
from .balloonize_service import (
    BalloonizeContext,
    build_confirmed_record,
    build_convert_command,
    propose_from_layer,
    propose_from_selection,
)
from .canvas_balloon_transform import transform_balloon_metadata
from .canvas_layout_transform import transform_layout_metadata
from .canvas_resample_metadata import resample_comic_metadata
from .commands import ComicEditCommand
from .model import BalloonRecord, move_balloon_record
from .service import ComicPageController, ComicPageState

if TYPE_CHECKING:
    from loomstroke.host_api import HostAPI, StrokePoint

logger = logging.getLogger(__name__)

#: sentinel for "argument not given" in keyword-only param setters
_UNSET = object()

PLUGIN_ID = "loomstroke.comic_tools"
TOOL_ID = "comic_tool"
PANEL_ID = "comic_tools_panel"
ASSETS_PANEL_ID = "comic_assets_panel"

MODE_PANELS = "panels"
MODE_BALLOONS = "balloons"

_TAIL_HANDLE_TOLERANCE = 14.0


def _ts(text: str) -> str:
    return text


class ComicToolsPlugin:
    """Tool + dock panel for the minimum manga production workflow."""

    def __init__(self) -> None:
        self.metadata = PluginMetadata(
            id=PLUGIN_ID,
            name=_ts("Comic Tools"),
            version="0.1.0",
            description=_ts("Panel layout, speech balloons and dialogue management"),
            author="LoomStroke Team",
            types=[PluginType.TOOL],
        )
        self.host_api: HostAPI | None = None
        self.controller = ComicPageController()
        self.mode = MODE_PANELS
        self.selected_panel_id: str | None = None
        self.selected_balloon_id: str | None = None
        self._panel_widget: Any = None
        self._drag: dict[str, Any] | None = None
        #: pending balloonize confirmation (BZ semi-automatic flow)
        self.pending_balloonize: BalloonizeContext | None = None
        #: pending 2-layer preserve conversion (BZ2)
        self.pending_preserve: PreserveProposal | None = None
        #: live raster-rig session: either the registration being previewed
        #: before confirm (``_rig_session_balloon_id`` None) or the selected
        #: rig balloon being re-edited (its balloon id). Runtime-only; the
        #: document is written by Commands on confirm / drag release.
        self._rig_proto_session: Any | None = None
        self._rig_session_balloon_id: str | None = None
        self._rig_session_payload: dict[str, Any] | None = None
        self._rig_session_record_key: tuple[Any, ...] | None = None
        self._rig_show_handles: bool = True
        self._rig_proto_hover: str | None = None
        #: comic asset library (Comic Assets panel); the store root is the
        #: plugin's user_data dir unless a test injects another store
        self.asset_store: Any = None
        self._assets_panel_widget: Any = None
        self._drop_handler: Any = None
        self._drop_token: Any = None
        #: transient compositor override (draw-time only, never persisted)
        self._rig_preview: Any | None = None
        self._rig_preview_last_band: tuple[int, int, int, int] | None = None
        self._rig_preview_last_tail: tuple[int, int, int, int] | None = None
        from .frame_workflow import FrameWorkflow  # noqa: PLC0415

        self.frame_workflow = FrameWorkflow(self)

    # ── plugin protocol ────────────────────────────────────────────────

    def descriptor(self) -> ToolDescriptor:
        return ToolDescriptor(
            id=TOOL_ID,
            display_name=_ts("Comic"),
            cursor="arrow",
            category="comic",
            order=40,
        )

    def activate(self, host: HostAPI) -> None:
        from loomstroke.core.comic_panels.page_geometry_registry import (  # noqa: PLC0415
            PanelPageGeometryRegistry,
        )

        from .canvas_frame_geometry import resolve_transformed_panels  # noqa: PLC0415

        PanelPageGeometryRegistry.register(resolve_transformed_panels, owner=self)
        VectorMetaScalerRegistry.shared().register_resampler(resample_comic_metadata, owner=self)
        self.host_api = host
        CanvasMetaTransformRegistry.shared().register(transform_layout_metadata, owner=self)
        CanvasMetaTransformRegistry.shared().register(transform_balloon_metadata, owner=self)
        register_tool = getattr(host, "register_tool", None)
        if callable(register_tool):
            register_tool(self)
        self._register_panel(host)
        self._register_assets_panel(host)
        self._subscribe_canvas_drop(host)

    def _register_panel(self, host: HostAPI) -> None:
        register_panel = getattr(host, "register_panel_widget", None)
        if not callable(register_panel):
            return
        has_panel = getattr(host, "has_panel", None)
        try:
            if callable(has_panel) and has_panel(PANEL_ID):
                return
        except Exception:
            logger.debug("comic panel lookup failed", exc_info=True)
        try:
            from .panel_widget import ComicToolsPanelWidget  # noqa: PLC0415

            self._panel_widget = ComicToolsPanelWidget(self)
            register_panel(
                PANEL_ID,
                _ts("Comic"),
                self._panel_widget,
                area="right",
                default_visible=False,
                owner_plugin_id=PLUGIN_ID,
                default_size=(340, 560),
            )
        except Exception:
            logger.exception("comic panel registration failed")

    def _register_assets_panel(self, host: HostAPI) -> None:
        register_panel = getattr(host, "register_panel_widget", None)
        if not callable(register_panel):
            return
        has_panel = getattr(host, "has_panel", None)
        try:
            if callable(has_panel) and has_panel(ASSETS_PANEL_ID):
                return
        except Exception:
            logger.debug("comic assets panel lookup failed", exc_info=True)
        try:
            from .comic_assets_panel import ComicAssetsPanelWidget  # noqa: PLC0415

            self._assets_panel_widget = ComicAssetsPanelWidget(self)
            register_panel(
                ASSETS_PANEL_ID,
                _ts("Comic Assets"),
                self._assets_panel_widget,
                area="right",
                default_visible=False,
                owner_plugin_id=PLUGIN_ID,
                default_size=(300, 420),
            )
        except Exception:
            logger.exception("comic assets panel registration failed")

    def _subscribe_canvas_drop(self, host: HostAPI) -> None:
        bus = getattr(host, "events", None)
        subscribe = getattr(bus, "on", None) or getattr(bus, "subscribe", None)
        if not callable(subscribe):
            return
        try:
            # strong reference for weakref-based buses
            self._drop_handler = self.on_canvas_drop
            self._drop_token = subscribe("ui.canvas.drop", self._drop_handler)
        except Exception:
            logger.debug("canvas drop subscription failed", exc_info=True)

    def _unsubscribe_canvas_drop(self) -> None:
        bus = getattr(self.host_api, "events", None) if self.host_api else None
        unsubscribe = getattr(bus, "off", None) or getattr(bus, "unsubscribe", None)
        if callable(unsubscribe) and self._drop_token is not None:
            try:
                unsubscribe("ui.canvas.drop", self._drop_token)
            except Exception:
                logger.debug("canvas drop unsubscription failed", exc_info=True)
        self._drop_token = None
        self._drop_handler = None

    def enable(self) -> None:
        self._install_overlay(True)
        self._notify_panel()

    def disable(self) -> None:
        self.frame_workflow.vector_erasure.cancel()
        self._install_overlay(False)
        self._drag = None
        # runtime-only rig session state is discarded on tool deactivation
        self._rig_proto_session = None
        self._rig_session_balloon_id = None
        self._rig_session_payload = None
        self._rig_session_record_key = None
        self._rig_proto_hover = None
        if self._rig_preview is not None:
            from .raster_rig_preview_override import install  # noqa: PLC0415

            install(self._canvas(), None)
            self._rig_preview = None

    def deactivate(self) -> None:
        from loomstroke.core.comic_panels.page_geometry_registry import (  # noqa: PLC0415
            PanelPageGeometryRegistry,
        )

        from .canvas_frame_geometry import resolve_transformed_panels  # noqa: PLC0415

        PanelPageGeometryRegistry.unregister(resolve_transformed_panels, owner=self)
        controls = getattr(self._panel_widget, "frame_controls", None)
        if controls:
            controls.dispose()
        self.frame_workflow.cancel()
        VectorMetaScalerRegistry.shared().unregister_resampler(resample_comic_metadata, owner=self)
        CanvasMetaTransformRegistry.shared().unregister(transform_layout_metadata, owner=self)
        CanvasMetaTransformRegistry.shared().unregister(transform_balloon_metadata, owner=self)
        self.disable()
        self._unsubscribe_canvas_drop()
        if self.host_api is not None:
            unregister = getattr(self.host_api, "unregister_tool", None)
            if callable(unregister):
                unregister(TOOL_ID)
        self.host_api = None

    # ── comic assets (Comic Assets panel + canvas drop) ────────────────
    def _asset_store(self) -> Any:
        if self.asset_store is None:
            from .comic_assets import ComicAssetStore  # noqa: PLC0415

            self.asset_store = ComicAssetStore()
        return self.asset_store

    def register_selected_balloon_as_asset(self, *, name: str = "") -> Any | None:
        """Save the selected balloon (rig / preserve / parametric) as a
        reusable asset in the balloon category. None = nothing selected."""

        from .comic_assets import build_balloon_asset  # noqa: PLC0415

        state = self.read_state()
        record = state.balloons.get(self.selected_balloon_id or "") if state else None
        if record is None:
            return None
        try:
            asset = build_balloon_asset(record, name=name)
            self._asset_store().save(asset)
        except Exception:
            logger.exception("balloon asset registration failed")
            return None
        widget = self._assets_panel_widget
        if widget is not None and hasattr(widget, "refresh"):
            widget.refresh()
        return asset

    def place_asset(
        self, category: str, asset_id: str, center: tuple[float, float] | None
    ) -> str | None:
        """Drop an asset onto the current page: a new balloon (own id, empty
        text) centred at ``center`` (page px; None = page centre). One undo
        unit; raster records get their part layer composed from the record."""

        from .balloon_objects import build_balloon_object  # noqa: PLC0415
        from .comic_assets import place_balloon_record  # noqa: PLC0415
        from .raster_parts import is_raster_preserve  # noqa: PLC0415
        from .text_frames import build_balloon_text_object  # noqa: PLC0415

        state = self.read_state()
        if state is None:
            return None
        asset = self._asset_store().load(category, asset_id)
        if asset is None:
            return None
        if center is None:
            w, h = self._page_size()
            center = (w * 0.5, h * 0.5)
        import uuid as _uuid  # noqa: PLC0415

        balloon_id = f"b{_uuid.uuid4().hex[:8]}"
        record = place_balloon_record(asset, center, balloon_id=balloon_id)
        if record is None:
            return None
        record = replace(record, panel_id=self.controller.panel_at(state, center))
        balloon_obj = build_balloon_object(record)
        if balloon_obj is None:
            return None
        text_obj = build_balloon_text_object(record, "", vertical=True)
        command = ComicEditCommand(
            label="comic_asset_place",
            ensure_layers=self.controller._ensure_balloon_layers(state),  # noqa: SLF001
            additions=[
                (self.controller._balloons_layer_id(state), balloon_obj),  # noqa: SLF001
                (self.controller._text_layer_id(state), text_obj),  # noqa: SLF001
            ],
            raster_parts=[record] if is_raster_preserve(record) else [],
        )
        from .panel_balloon_adoption_plan import adopt_created_balloon  # noqa: PLC0415

        command = adopt_created_balloon(self.controller, state, record, command)
        if not self._submit(command):
            return None
        self.selected_balloon_id = balloon_id
        self.mode = MODE_BALLOONS
        self._refresh_canvas()
        self._notify_panel()
        return balloon_id

    def on_canvas_drop(self, payload: object) -> None:
        """``ui.canvas.drop`` listener: comic asset MIME -> place it."""

        from .comic_assets import ASSET_MIME, parse_drop_payload  # noqa: PLC0415
        from .frame_image_drop import handle_image_drop  # noqa: PLC0415

        if isinstance(payload, dict) and handle_image_drop(self, payload):
            return

        if not isinstance(payload, dict) or payload.get("mime") != ASSET_MIME:
            return
        parsed = parse_drop_payload(str(payload.get("payload_json") or ""))
        pos = payload.get("pos_canvas")
        if parsed is None or not isinstance(pos, (tuple, list)) or len(pos) < 2:
            return
        category, asset_id = parsed
        try:
            self.place_asset(category, asset_id, (float(pos[0]), float(pos[1])))
        except Exception:
            logger.exception("comic asset placement failed")

    # ── canvas access ──────────────────────────────────────────────────

    def _canvas(self) -> Any | None:
        return getattr(self.host_api, "canvas", None) if self.host_api else None

    def _page_size(self) -> tuple[int, int]:
        canvas = self._canvas()
        try:
            width, height = canvas.get_canvas_size()
            return int(width), int(height)
        except Exception:
            return (1000, 1400)

    def read_state(self) -> ComicPageState | None:
        canvas = self._canvas()
        if canvas is None:
            return None
        return self.controller.read_state(canvas, page_size=self._page_size())

    def _submit(self, command: Any) -> bool:
        canvas = self._canvas()
        if canvas is None:
            return False
        ok = bool(canvas.submit(command))
        if ok:
            self._refresh_canvas()
            self._notify_panel()
        return ok

    def _refresh_canvas(self) -> None:
        canvas = self._canvas()
        if self._rig_proto_session is not None:
            self._sync_rig_preview()
        recompose = getattr(canvas, "_recompose", None)
        if callable(recompose):
            recompose()
        widget = self._canvas_widget()
        if widget is not None and hasattr(widget, "update"):
            widget.update()

    def _notify_panel(self) -> None:
        widget = self._panel_widget
        if widget is not None and hasattr(widget, "refresh_from_tool"):
            widget.refresh_from_tool()
            controls = getattr(widget, "frame_controls", None)
            if controls:
                controls.refresh_edges()

    # ── layout operations (called by panel UI and shortcuts) ──────────

    def current_layout(self) -> lt.ComicPageLayout | None:
        state = self.read_state()
        return state.layout if state else None

    def _commit_layout(self, state: ComicPageState, layout: lt.ComicPageLayout) -> bool:
        from .frame_operation_dispatch import preview_layout  # noqa: PLC0415

        if preview_layout(self, state, layout):
            return True
        from .panel_current_format import current_layout_command  # noqa: PLC0415

        command = current_layout_command(
            self.controller, state, layout, page_size=self._page_size()
        )
        return self._submit(command)

    def apply_template(self, template_id: str, *, gutter_px: float | None = None) -> bool:
        state = self.read_state()
        if state is None:
            return False
        gutter = (
            gutter_px
            if gutter_px is not None
            else (state.layout.gutter_px if state.layout else lt.DEFAULT_GUTTER_PX)
        )
        layout = lt.template_layout(template_id, gutter_px=gutter)
        from .frame_operation_dispatch import preview_template  # noqa: PLC0415

        if preview_template(self, state, layout):
            return True
        if any("frame_layer" in record.metadata for record in state.panel_records.values()):
            from .panel_template_plan import PanelTemplatePreview  # noqa: PLC0415

            return self._submit(
                PanelTemplatePreview(state, layout, page_size=self._page_size()).prepare().command()
            )
        self.selected_panel_id = None
        return self._commit_layout(state, layout)

    def split_selected(self, orientation: str, *, ratio: float = 0.5, count: int = 2) -> bool:
        state = self.read_state()
        if state is None:
            return False
        layout = state.layout or lt.create_single_panel_layout()
        panel_id = self.selected_panel_id or (
            layout.reading_order[0] if layout.reading_order else "panel-1"
        )
        from .frame_operation_dispatch import frame_controls  # noqa: PLC0415

        controls = frame_controls(self, state, panel_id)
        if controls:
            return (
                controls.run(lambda: controls.preview(orientation, ratio=ratio, count=count))
                is not False
            )
        try:
            layout = lt.split_panel(
                layout,
                panel_id,
                "vertical" if orientation == "vertical" else "horizontal",
                ratio=ratio,
                count=count,
            )
        except lt.ComicLayoutError:
            return False
        return self._commit_layout(state, layout)

    def merge_selected_with(self, other_panel_id: str) -> bool:
        state = self.read_state()
        if state is None or state.layout is None or not self.selected_panel_id:
            return False
        from .frame_operation_dispatch import run_frame_action  # noqa: PLC0415

        return run_frame_action(
            self, lambda: self.frame_workflow.merge(self.selected_panel_id, other_panel_id)
        )

    def delete_selected_panel(self) -> bool:
        state = self.read_state()
        if state is None or state.layout is None or not self.selected_panel_id:
            return False
        from .frame_operation_dispatch import run_frame_action  # noqa: PLC0415

        return run_frame_action(
            self, lambda: self.frame_workflow.delete_panel(self.selected_panel_id, "keep")
        )

    def set_gutter(self, gutter_px: float) -> bool:
        state = self.read_state()
        if state is None or state.layout is None:
            return False
        layout = lt.set_gutter(state.layout, gutter_px)
        return layout == state.layout or self._commit_layout(state, layout)

    def set_selected_ratio(self, ratio: float) -> bool:
        state = self.read_state()
        if state is None or state.layout is None or not self.selected_panel_id:
            return False
        layout = lt.set_split_ratio(state.layout, self.selected_panel_id, ratio)
        return layout == state.layout or self._commit_layout(state, layout)

    def move_reading_order(self, panel_id: str, delta: int) -> bool:
        state = self.read_state()
        if state is None or state.layout is None:
            return False
        layout = state.layout
        if panel_id not in layout.reading_order:
            layout = replace(layout, reading_order=state.reading_order)
        try:
            layout = lt.move_reading_order(layout, panel_id, delta)
        except lt.ComicLayoutError:
            return False
        return self._commit_layout(state, layout)

    # ── balloon operations ─────────────────────────────────────────────

    def create_balloon(
        self,
        *,
        center: tuple[float, float] | None = None,
        kind: str = "speech_round",
        text: str = "",
        vertical: bool = True,
    ) -> str | None:
        state = self.read_state()
        if state is None:
            return None
        if center is None:
            width, height = self._page_size()
            center = (width * 0.5, height * 0.35)
        command, record = self.controller.create_balloon_command(
            state, center=center, kind=kind, text=text, vertical=vertical
        )
        if not self._submit(command):
            return None
        self.selected_balloon_id = record.balloon_id
        return record.balloon_id

    def update_balloon(self, record: BalloonRecord, *, text: str | None = None) -> bool:
        state = self.read_state()
        if state is None:
            return False
        command = self.controller.update_balloon_command(state, record, text=text)
        return self._submit(command)

    def set_balloon_text(self, balloon_id: str, text: str) -> bool:
        state = self.read_state()
        if state is None:
            return False
        record = state.balloons.get(balloon_id)
        if record is None:
            return False
        return self.update_balloon(record, text=text)

    def set_balloon_speaker(self, balloon_id: str, speaker_id: str, speaker_name: str) -> bool:
        from .speaker_styles import assign_speaker  # noqa: PLC0415

        return assign_speaker(self, balloon_id, speaker_id, speaker_name)

    def set_balloon_kind(self, balloon_id: str, kind: str) -> bool:
        state = self.read_state()
        if state is None:
            return False
        record = state.balloons.get(balloon_id)
        if record is None:
            return False
        return self.update_balloon(replace(record, kind=kind))

    def set_balloon_auto_size(self, balloon_id: str, auto_size: bool) -> bool:
        state = self.read_state()
        if state is None:
            return False
        record = state.balloons.get(balloon_id)
        if record is None:
            return False
        from .raster_parts import is_raster_preserve  # noqa: PLC0415

        if is_raster_preserve(record):
            # raster balloons: body_rect = crop placement, text never
            # resizes it (see update_balloon_command); keep the flag off
            auto_size = False
        return self.update_balloon(replace(record, auto_size=auto_size))

    def set_balloon_body_open(self, balloon_id: str, body_open: bool) -> bool:
        """Procedural opening toggle: open the outline at the tail base
        (True) or keep it closed (False)."""

        state = self.read_state()
        if state is None:
            return False
        record = state.balloons.get(balloon_id)
        if record is None:
            return False
        return self.update_balloon(replace(record, body_open=body_open))

    def set_tail_follow_tip(self, balloon_id: str, follow: bool) -> bool:
        """Root follows the tip (True) or stays pinned on the outline
        (False). Switching never moves the root: the current effective
        centre becomes the pinned value / the offset is re-calibrated."""

        from .raster_parts import effective_tail_root_param  # noqa: PLC0415

        state = self.read_state()
        if state is None:
            return False
        record = state.balloons.get(balloon_id)
        if record is None:
            return False
        centre = effective_tail_root_param(record, record.tail.target)
        record = replace(record, tail_follow_tip=bool(follow))
        if centre is not None:
            record = self._with_root_centre(record, centre)
        return self.update_balloon(record)

    def set_tail_auto_join(self, balloon_id: str, enabled: bool) -> bool:
        """Auto-join toggle: bridge tail stroke ends to the balloon ink."""

        state = self.read_state()
        if state is None:
            return False
        record = state.balloons.get(balloon_id)
        if record is None:
            return False
        return self.update_balloon(replace(record, tail_auto_join=bool(enabled)))

    def set_tail_base_gap(self, balloon_id: str, gap_px: float) -> bool:
        """Distance (page px) between the tail root and the balloon outline;
        kept constant while the tail moves."""

        state = self.read_state()
        if state is None:
            return False
        record = state.balloons.get(balloon_id)
        if record is None:
            return False
        if abs(float(record.tail_base_gap) - float(gap_px)) < 1e-6:
            return True
        return self.update_balloon(replace(record, tail_base_gap=float(gap_px)))

    def delete_balloon(self, balloon_id: str) -> bool:
        state = self.read_state()
        if state is None:
            return False
        if self.selected_balloon_id == balloon_id:
            self.selected_balloon_id = None
        return self._submit(self.controller.delete_balloon_command(state, balloon_id))

    def select_balloon(self, balloon_id: str) -> None:
        self.selected_balloon_id = balloon_id
        self.mode = MODE_BALLOONS
        self._activate_comic_tool()
        self._refresh_canvas()
        self._notify_panel()

    def jump_to_balloon(self, page_id: str, balloon_id: str) -> None:
        """Dialogue-list jump: switch page, then select the balloon."""

        pages = getattr(self.host_api, "pages", None)
        if pages is not None:
            try:
                if pages.get_active_page_id() != page_id:
                    pages.set_active_page(page_id)
            except Exception:
                logger.exception("comic_tools: page switch failed")
        self.select_balloon(balloon_id)

    # ── balloonize (吹き出し化) ───────────────────────────────────────

    def propose_balloonize(self) -> BalloonizeContext | None:
        """Stage 1-8 of the pipeline: build a proposal for confirmation.

        The current selection wins; otherwise the active layer's single
        balloon (vector path preferred when the layer is a vector layer).
        Never auto-commits — the proposal waits for confirm/cancel.
        """

        canvas = self._canvas()
        if canvas is None:
            return None
        try:
            context = propose_from_selection(canvas)
            if context is None:
                context = propose_from_layer(canvas)
        except BalloonizeError as exc:
            logger.info("balloonize proposal failed: %s", exc)
            self.pending_balloonize = None
            self._refresh_canvas()
            self._notify_panel()
            return None
        self.pending_balloonize = context
        self._refresh_canvas()
        self._notify_panel()
        return context

    def confirm_balloonize(
        self,
        *,
        kind: str | None = None,
        body_rect: tuple[float, float, float, float] | None = None,
        tail_enabled: bool | None = None,
        tail_tip: tuple[float, float] | None = None,
        text: str = "",
        vertical: bool = True,
        hide_source_layer: bool = True,
    ) -> str | None:
        """Stage 9-10: user-confirmed conversion into a REGULAR balloon."""

        context = self.pending_balloonize
        state = self.read_state()
        if context is None or state is None:
            return None
        import uuid as _uuid  # noqa: PLC0415

        balloon_id = f"b{_uuid.uuid4().hex[:8]}"
        rect = body_rect or context.proposal.body_rect
        center = (rect[0] + rect[2] * 0.5, rect[1] + rect[3] * 0.5)
        record = build_confirmed_record(
            context,
            balloon_id=balloon_id,
            panel_id=self.controller.panel_at(state, center),
            kind=kind,
            body_rect=body_rect,
            tail_enabled=tail_enabled,
            tail_tip=tail_tip,
        )
        command = build_convert_command(
            self.controller,
            state,
            record,
            text=text,
            vertical=vertical,
            hide_source_layer=hide_source_layer,
        )
        if not self._submit(command):
            return None
        self.pending_balloonize = None
        self.selected_balloon_id = balloon_id
        self.mode = MODE_BALLOONS
        self._activate_comic_tool()
        self._refresh_canvas()
        self._notify_panel()
        return balloon_id

    def cancel_balloonize(self) -> None:
        self.pending_balloonize = None
        self._refresh_canvas()
        self._notify_panel()

    def _activate_comic_tool(self) -> None:
        """Make this tool the active canvas tool (post-conversion UX)."""

        tool_manager = getattr(self.host_api, "_tool_manager", None) or getattr(
            self.host_api, "tool_manager", None
        )
        activate = getattr(tool_manager, "activate_tool", None)
        if callable(activate):
            try:
                activate(TOOL_ID)
            except Exception:
                logger.debug("comic tool activation failed", exc_info=True)

    # -- balloonize preserve (Body/Tail 2 layers, BZ2) ------------------

    def propose_balloonize_preserve(
        self, *, body_layer_id: str, tail_layer_id: str | None
    ) -> PreserveProposal | None:
        """Two explicit sources -> preserve proposal. Body and tail never
        have to touch; a detached tail is a normal proposal, not an error."""

        canvas = self._canvas()
        if canvas is None:
            return None
        try:
            proposal = propose_preserve(
                canvas, body_layer_id=body_layer_id, tail_layer_id=tail_layer_id
            )
        except BalloonizeError as exc:
            logger.info("preserve proposal failed: %s", exc)
            self.pending_preserve = None
            self._refresh_canvas()
            self._notify_panel()
            return None
        self.pending_preserve = proposal
        self._refresh_canvas()
        self._notify_panel()
        return proposal

    def confirm_balloonize_preserve(
        self,
        *,
        text: str = "",
        vertical: bool = True,
        hide_source_layers: bool = True,
    ) -> str | None:
        proposal = self.pending_preserve
        state = self.read_state()
        if proposal is None or state is None:
            return None
        import uuid as _uuid  # noqa: PLC0415

        balloon_id = f"b{_uuid.uuid4().hex[:8]}"
        rect = proposal.body.bbox
        center = (rect[0] + rect[2] * 0.5, rect[1] + rect[3] * 0.5)
        record = build_preserve_record(
            proposal,
            balloon_id=balloon_id,
            panel_id=self.controller.panel_at(state, center),
        )
        try:
            command = build_preserve_command(
                self.controller,
                state,
                record,
                text=text,
                vertical=vertical,
                hide_source_layers=hide_source_layers,
            )
        except BalloonizeError as exc:
            logger.info("preserve conversion failed: %s", exc)
            return None
        if not self._submit(command):
            return None
        self.pending_preserve = None
        self.selected_balloon_id = balloon_id
        self.mode = MODE_BALLOONS
        self._refresh_canvas()
        self._notify_panel()
        return balloon_id

    def propose_balloonize_clean_from_layers(
        self, *, body_layer_id: str, tail_layer_id: str | None
    ) -> BalloonizeContext | None:
        """Clean mode over 2 layers: merge both coverages and delegate to
        the existing Phase BZ single-input pipeline (no re-implementation)."""

        canvas = self._canvas()
        if canvas is None:
            return None
        from loomstroke.ai_selection.runtime_imports import load_numpy  # noqa: PLC0415

        from .balloonize import mask_from_alpha, propose_balloon  # noqa: PLC0415
        from .balloonize_service import (  # noqa: PLC0415
            SOURCE_LAYER,
            BalloonizeContext,
            _qimage_to_rgba,
        )

        np = load_numpy()
        try:
            merged = None
            for layer_id in (body_layer_id, tail_layer_id):
                if not layer_id:
                    continue
                layer = canvas._get_layer_by_id(layer_id)  # noqa: SLF001
                image = getattr(layer, "image", None)
                if image is None or (hasattr(image, "isNull") and image.isNull()):
                    continue
                mask = mask_from_alpha(_qimage_to_rgba(image))
                merged = mask if merged is None else np.maximum(merged, mask)
            if merged is None:
                return None
            proposal = propose_balloon(merged)
        except BalloonizeError as exc:
            logger.info("clean 2-layer proposal failed: %s", exc)
            return None
        context = BalloonizeContext(
            proposal=proposal,
            source=SOURCE_LAYER,
            source_layer_id=str(body_layer_id or ""),
        )
        self.pending_balloonize = context
        self._refresh_canvas()
        self._notify_panel()
        return context

    def cancel_balloonize_preserve(self) -> None:
        self.pending_preserve = None
        self._refresh_canvas()
        self._notify_panel()

    def list_canvas_layers(self) -> list[tuple[str, str]]:
        """(layer_id, name) pairs for the Body/Tail layer selectors."""

        canvas = self._canvas()
        results: list[tuple[str, str]] = []
        stack = list(getattr(canvas, "_layers", None) or [])
        while stack:
            layer = stack.pop(0)
            stack.extend(getattr(layer, "children", None) or [])
            layer_id = str(getattr(layer, "id", "") or "")
            if layer_id:
                results.append((layer_id, str(getattr(layer, "name", "") or layer_id)))
        return results

    # ── pointer handling ───────────────────────────────────────────────

    def on_pointer_down(
        self,
        pt: StrokePoint,
        *,
        alt: bool = False,
        shift: bool = False,
        ctrl: bool = False,
        meta: bool = False,
    ) -> None:
        del alt, shift, ctrl, meta
        if self.frame_workflow.vector_erasure.down(pt):
            return
        point0 = (float(pt.pos[0]), float(pt.pos[1]))
        state = self.read_state()
        self._ensure_rig_edit_session(state)
        if self._rig_proto_pointer_down(point0):
            return
        if state is None:
            return
        point = point0
        # unified hit test (no mode gate): balloon handles/bodies first,
        # panel boundaries/selection otherwise — like any object tool
        if not self._pointer_down_balloons(state, point):
            self._pointer_down_panels(state, point)
        self._refresh_canvas()
        self._notify_panel()

    def _pointer_down_panels(self, state: ComicPageState, point: tuple[float, float]) -> None:
        from .frame_boundary_continuation import panel_interaction_state  # noqa: PLC0415

        interaction = panel_interaction_state(self.frame_workflow, state)
        if interaction is None:
            return
        if interaction.layout is not None:
            hit = lt.boundary_hit_test(interaction.layout, *self._page_size(), point)
            if hit is not None:
                self._drag = {
                    "kind": "boundary",
                    "hit": hit,
                    "state": state,
                    "layout": interaction.layout,
                    "origin": point,
                }
                return
        panel_id = self.controller.panel_at(interaction, point)
        self.selected_panel_id = panel_id or None

    #: handle grab tolerance in SCREEN px; divided by the view zoom so the
    #: hit area is the same size on screen at any magnification
    _HANDLE_SCREEN_TOLERANCE = 12.0

    def _view_zoom(self) -> float:
        widget = self._canvas_widget()
        viewport = getattr(widget, "viewport", None)
        state = getattr(viewport, "state", None)
        try:
            zoom = float(getattr(state, "zoom", 1.0) or 1.0)
        except (TypeError, ValueError):
            zoom = 1.0
        return max(0.05, zoom)

    @property
    def _RESIZE_HANDLE_TOLERANCE(self) -> float:  # noqa: N802 - kept as the shared name
        return self._HANDLE_SCREEN_TOLERANCE / self._view_zoom()

    def _resize_handle_at(
        self, record: BalloonRecord, point: tuple[float, float]
    ) -> tuple[float, float] | None:
        """The FIXED opposite corner when ``point`` grabs a resize handle."""

        x, y, w, h = record.body_rect
        corners = ((x, y), (x + w, y), (x + w, y + h), (x, y + h))
        for index, (cx, cy) in enumerate(corners):
            if (
                abs(point[0] - cx) <= self._RESIZE_HANDLE_TOLERANCE
                and abs(point[1] - cy) <= self._RESIZE_HANDLE_TOLERANCE
            ):
                opposite = corners[(index + 2) % 4]
                return opposite
        return None

    def _text_frame_handle_at(
        self, record: BalloonRecord, point: tuple[float, float]
    ) -> tuple[float, float] | None:
        """The FIXED opposite corner when ``point`` grabs a text-frame
        corner handle (テキストボックスのサイズ変更)."""

        from .text_frames import balloon_text_rect  # noqa: PLC0415

        x, y, w, h = balloon_text_rect(record)
        corners = ((x, y), (x + w, y), (x + w, y + h), (x, y + h))
        tol = self._RESIZE_HANDLE_TOLERANCE * 0.75
        for index, (cx, cy) in enumerate(corners):
            if abs(point[0] - cx) <= tol and abs(point[1] - cy) <= tol:
                return corners[(index + 2) % 4]
        return None

    def _text_frame_resized(
        self, record: BalloonRecord, anchor: tuple[float, float], point: tuple[float, float]
    ) -> BalloonRecord:
        """Record with the text frame spanning ``anchor``..``point`` (stored
        relative to the body rect so it follows moves/resizes)."""

        ax, ay = anchor
        new_x = min(ax, point[0])
        new_y = min(ay, point[1])
        new_w = max(8.0, abs(point[0] - ax))
        new_h = max(8.0, abs(point[1] - ay))
        bx, by, bw, bh = record.body_rect
        rel = (
            (new_x - bx) / max(1e-6, bw),
            (new_y - by) / max(1e-6, bh),
            new_w / max(1e-6, bw),
            new_h / max(1e-6, bh),
        )
        # a manual frame wins over auto sizing (which would re-fit the body
        # to the text and fight the user)
        return replace(record, text_frame_rel=rel, auto_size=False)

    def _tail_base_handle_at(self, record: BalloonRecord, point: tuple[float, float]) -> int | None:
        """0/1 when ``point`` grabs one of the tail base anchors on the
        balloon outline (2 接続点)."""

        if record.tail_base_params is None:
            return None
        from .raster_parts import (  # noqa: PLC0415
            effective_tail_base_params,
            outline_point_at_param,
        )

        params = effective_tail_base_params(record, record.tail.target) or record.tail_base_params
        for index in (0, 1):
            bx, by = outline_point_at_param(record, params[index])
            if (
                abs(point[0] - bx) <= self._RESIZE_HANDLE_TOLERANCE
                and abs(point[1] - by) <= self._RESIZE_HANDLE_TOLERANCE
            ):
                return index
        return None

    def _tail_root_handle_at(self, record: BalloonRecord, point: tuple[float, float]) -> int | None:
        """0/1 when ``point`` grabs one of the tail-side root corners."""

        if record.tail_base_params is None:
            return None
        from .raster_parts import tail_root_corners  # noqa: PLC0415

        corners = tail_root_corners(record, record.tail.target)
        if corners is None:
            return None
        for index, (cx, cy) in enumerate(corners):
            if (
                abs(point[0] - cx) <= self._RESIZE_HANDLE_TOLERANCE
                and abs(point[1] - cy) <= self._RESIZE_HANDLE_TOLERANCE
            ):
                return index
        return None

    def _tail_root_dragged(
        self,
        record: BalloonRecord,
        index: int,
        press: tuple[float, float],
        point: tuple[float, float],
    ) -> BalloonRecord:
        """Record after dragging tail-side root corner ``index`` from
        ``press`` to ``point``: only THAT corner moves (independent blue
        anchors); the drag delta is stored in the straight tail frame so it
        follows the tail wherever it points. Balloon anchors don't move."""

        from .raster_parts import _axis_frame  # noqa: PLC0415

        frame = _axis_frame(record, record.tail.target)
        if frame is None or record.tail_local_base is None:
            return record
        (ux, uy), (vx, vy) = frame
        dx, dy = point[0] - press[0], point[1] - press[1]
        along = dx * ux + dy * uy
        across = dx * vx + dy * vy
        offsets = list(record.tail_root_corner_offsets or ((0.0, 0.0), (0.0, 0.0)))
        oa, ob = offsets[index]
        offsets[index] = (oa + along, ob + across)
        return replace(record, tail_root_corner_offsets=(offsets[0], offsets[1]))

    def _tail_axis(
        self, record: BalloonRecord
    ) -> tuple[tuple[float, float], tuple[float, float]] | None:
        """(base_mid, tip) of the procedural tail, or None."""

        if record.tail_base_params is None:
            return None
        from .raster_parts import (  # noqa: PLC0415
            effective_tail_base_params,
            outline_point_at_param,
        )

        tip = record.tail.target
        params = effective_tail_base_params(record, tip) or record.tail_base_params
        a = outline_point_at_param(record, params[0])
        b = outline_point_at_param(record, params[1])
        return (((a[0] + b[0]) * 0.5, (a[1] + b[1]) * 0.5), tip)

    def _tail_bend_handle_pos(self, record: BalloonRecord) -> tuple[float, float] | None:
        axis = self._tail_axis(record)
        if axis is None:
            return None
        from .raster_parts import tail_bend_handle_point  # noqa: PLC0415

        return tail_bend_handle_point(record, axis[0], axis[1])

    def _tail_base_dragged(
        self, record: BalloonRecord, index: int, point: tuple[float, float]
    ) -> BalloonRecord:
        """Record after dragging balloon-side anchor ``index`` to ``point``.

        閉じモード: 付け根をバルーン外周に沿ってスライド — 2 点の間隔 (px)
        は保ったまま中心だけ動く。開口モード (``body_open``): 掴んだ端だけを
        動かして開く度合いを決める (もう一方の端は据え置き → 中心と半幅が
        変わる)。どちらも正本 (``tail_root_param`` / ``tail_root_half_px``)
        を更新し、先端追従 ON なら offset を「今の先端でその位置になる」よう
        再較正する。
        """

        from .raster_parts import (  # noqa: PLC0415
            effective_tail_base_params,
            effective_tail_root_param,
            nearest_outline_param,
            outline_perimeter,
            tail_root_half_px,
        )

        current = effective_tail_base_params(record, record.tail.target)
        centre_now = effective_tail_root_param(record, record.tail.target)
        half_px = tail_root_half_px(record)
        if current is None or centre_now is None or half_px is None:
            return record
        new_param = nearest_outline_param(record, point)
        perimeter = max(1e-6, outline_perimeter(record))
        if record.body_open:
            other = current[1 - index]
            ends = [0.0, 0.0]
            ends[index] = new_param
            ends[1 - index] = other
            spacing = (ends[1] - ends[0]) % 1.0
            if spacing > 0.5:
                spacing -= 1.0
            spacing = max(-0.45, min(0.45, spacing))
            if abs(spacing) < 0.005:
                spacing = 0.005 if spacing >= 0 else -0.005
            centre = (ends[0] + spacing * 0.5) % 1.0
            half_px = abs(spacing) * 0.5 * perimeter
        else:
            delta = (new_param - current[index] + 0.5) % 1.0 - 0.5
            centre = (centre_now + delta) % 1.0
        return self._with_root_centre(record, centre, half_px)

    def _with_root_centre(
        self, record: BalloonRecord, centre: float, half_px: float | None = None
    ) -> BalloonRecord:
        """Store ``centre`` as the root authority; with follow-tip ON the
        offset is re-calibrated so the tip-derived proposal equals it."""

        from .raster_parts import outline_param_facing  # noqa: PLC0415

        offset = record.tail_base_offset
        if record.tail_follow_tip:
            facing = outline_param_facing(record, record.tail.target)
            offset = (centre - facing + 0.5) % 1.0 - 0.5
        return replace(
            record,
            tail_root_param=centre % 1.0,
            tail_root_half_px=(float(half_px) if half_px is not None else record.tail_root_half_px),
            tail_base_offset=offset,
        )

    def _pointer_down_balloons(  # noqa: PLR0911, PLR0912 - one branch per handle kind
        self, state: ComicPageState, point: tuple[float, float]
    ) -> bool:
        """True when the click engaged a balloon (handle, tail or body)."""

        from .model import is_raster_rig  # noqa: PLC0415

        selected = state.balloons.get(self.selected_balloon_id or "")
        if selected is not None:
            # rig balloons: no resize (pixels are the authority; scale is a
            # rig parameter) — root/tip handles were tried before this
            anchor = None if is_raster_rig(selected) else self._resize_handle_at(selected, point)
            if anchor is not None:
                self._drag = {
                    "kind": "resize",
                    "balloon_id": selected.balloon_id,
                    "state": state,
                    "anchor": anchor,
                }
                return True
            base_index = self._tail_base_handle_at(selected, point)
            if base_index is not None:
                self._drag = {
                    "kind": "tail_base",
                    "balloon_id": selected.balloon_id,
                    "state": state,
                    "base_index": base_index,
                }
                return True
            root_index = self._tail_root_handle_at(selected, point)
            if root_index is not None:
                self._drag = {
                    "kind": "tail_root",
                    "balloon_id": selected.balloon_id,
                    "state": state,
                    "root_index": root_index,
                    "press": point,
                }
                return True
            text_anchor = self._text_frame_handle_at(selected, point)
            if text_anchor is not None:
                self._drag = {
                    "kind": "text_frame",
                    "balloon_id": selected.balloon_id,
                    "state": state,
                    "anchor": text_anchor,
                }
                return True
            bend_pos = self._tail_bend_handle_pos(selected)
            if bend_pos is not None and (
                abs(point[0] - bend_pos[0]) <= self._RESIZE_HANDLE_TOLERANCE
                and abs(point[1] - bend_pos[1]) <= self._RESIZE_HANDLE_TOLERANCE
            ):
                self._drag = {
                    "kind": "tail_bend",
                    "balloon_id": selected.balloon_id,
                    "state": state,
                }
                return True
        for balloon_id, record in state.balloons.items():
            tx, ty = record.tail.target
            tip_tol = _TAIL_HANDLE_TOLERANCE / self._view_zoom()
            if abs(point[0] - tx) <= tip_tol and (abs(point[1] - ty) <= tip_tol):
                self.selected_balloon_id = balloon_id
                if is_raster_rig(record):
                    # the tip of a rig balloon: select it and drag through
                    # its own session (pose + live junction preview)
                    self._ensure_rig_edit_session(state)
                    session = self._rig_proto_session
                    if session is not None and self._rig_session_balloon_id == balloon_id:
                        session.begin_drag("tip")
                        session.drag_to(point)
                    return True
                self._drag = {"kind": "tail", "balloon_id": balloon_id, "state": state}
                return True
        for balloon_id, record in state.balloons.items():
            x, y, w, h = record.body_rect
            if x <= point[0] <= x + w and y <= point[1] <= y + h:
                self.selected_balloon_id = balloon_id
                self._drag = {
                    "kind": "move",
                    "balloon_id": balloon_id,
                    "state": state,
                    "origin": point,
                    "body_rect": record.body_rect,
                    "tail_target": record.tail.target,
                }
                return True
        if self.selected_balloon_id is not None:
            self.selected_balloon_id = None
        return False

    def on_pointer_move(self, pts: Iterable[StrokePoint]) -> None:
        if self.frame_workflow.vector_erasure.points:
            self.frame_workflow.vector_erasure.move(pts)
            return
        last = None
        for pt in pts:
            last = pt
        if last is None:
            return
        session = self._rig_proto_session
        if session is not None and session.dragging:
            session.drag_to((float(last.pos[0]), float(last.pos[1])))
            self._refresh_canvas()
            return
        if self._drag is None:
            return
        self._drag["last_point"] = (float(last.pos[0]), float(last.pos[1]))
        # live ghost preview: repaint the overlay only (no document writes)
        widget = self._canvas_widget()
        if widget is not None and hasattr(widget, "update"):
            widget.update()

    def on_pointer_up(  # noqa: PLR0911, PLR0912, PLR0915 - one branch per drag kind
        self, pt: StrokePoint
    ) -> None:
        if self.frame_workflow.vector_erasure.up(pt):
            return
        session = self._rig_proto_session
        if session is not None and session.dragging:
            session.drag_to((float(pt.pos[0]), float(pt.pos[1])))
            session.end_drag()
            if self._rig_session_balloon_id is not None:
                self._commit_rig_drag()
            self._refresh_canvas()
            self.refresh_rig_proto()
            return
        drag = self._drag
        self._drag = None
        if drag is None:
            return
        point = (float(pt.pos[0]), float(pt.pos[1]))
        state: ComicPageState = drag["state"]
        if drag["kind"] == "boundary" and state.layout is not None:
            if point == drag.get("origin"):
                self._refresh_canvas()
                return
            hit: lt.BoundaryHit = drag["hit"]
            axis_value = point[0] if hit.orientation == "vertical" else point[1]
            position = (axis_value - hit.axis_start) / max(1.0, hit.axis_extent)
            layout = lt.set_boundary_ratio(
                drag["layout"], hit.node_id, hit.boundary_index, position
            )
            self._commit_layout(state, layout)
            return
        balloon_id = drag.get("balloon_id") or ""
        record = state.balloons.get(balloon_id)
        if record is None:
            return
        if drag["kind"] == "resize":
            ax, ay = drag["anchor"]
            new_x = min(ax, point[0])
            new_y = min(ay, point[1])
            new_w = max(24.0, abs(point[0] - ax))
            new_h = max(24.0, abs(point[1] - ay))
            # manual resize wins over auto sizing (手動優先) — the tail and
            # its raster/outline stay exactly where they are
            record = replace(
                record,
                body_rect=(new_x, new_y, new_w, new_h),
                auto_size=False,
            )
            self.update_balloon(record)
            return
        if drag["kind"] == "text_frame":
            self.update_balloon(self._text_frame_resized(record, drag["anchor"], point))
            return
        if drag["kind"] == "tail_base":
            self.update_balloon(self._tail_base_dragged(record, drag["base_index"], point))
            return
        if drag["kind"] == "tail_root":
            self.update_balloon(
                self._tail_root_dragged(record, drag["root_index"], drag["press"], point)
            )
            return
        if drag["kind"] == "tail_bend":
            from .raster_parts import tail_bend_from_handle_point  # noqa: PLC0415

            axis = self._tail_axis(record)
            if axis is not None:
                record = replace(
                    record,
                    tail_bend=tail_bend_from_handle_point(record, axis[0], axis[1], point),
                )
                self.update_balloon(record)
            return
        if drag["kind"] == "tail":
            if record.tail_local_root is not None and record.tail_local_tip is not None:
                # procedural connection (楕円と同じ): the tip follows the
                # handle; root/orientation/stretch re-derive at render time
                record = replace(record, tail=replace(record.tail, target=point))
                self.update_balloon(record)
                return
            if (
                record.tail_geometry_mode in ("hand_drawn", "raster_preserve")
                and record.tail_rect is not None
            ):
                # translate the drawn tail by the nominal-tip delta; the
                # hand-drawn outline itself is preserved verbatim
                dx = point[0] - record.tail.target[0]
                dy = point[1] - record.tail.target[1]
                tx, ty, tw, th = record.tail_rect
                record = replace(
                    record,
                    tail_rect=(tx + dx, ty + dy, tw, th),
                    tail=replace(record.tail, target=point),
                )
            else:
                record = replace(record, tail=replace(record.tail, target=point))
            self.update_balloon(record)
            return
        if drag["kind"] == "move":
            ox, oy = drag["origin"]
            dx, dy = point[0] - ox, point[1] - oy
            if abs(dx) < 1 and abs(dy) < 1:
                return
            # move_balloon_record shifts body, detached hand-drawn tail and
            # nominal tip together (they are one balloon)
            self.update_balloon(move_balloon_record(record, dx, dy))

    def on_pointer_hover(self, pt: StrokePoint) -> None:
        session = self._rig_proto_session
        if session is None:
            return
        hover = session.hit_test((float(pt.pos[0]), float(pt.pos[1])), self._view_zoom())
        if hover != self._rig_proto_hover:
            self._rig_proto_hover = hover
            self._refresh_canvas()

    # ── BZ4R raster-rig canvas prototype (feature-flagged) ─────────────

    # ── raster rig: registration / confirm / re-edit ───────────────────
    @property
    def rig_session(self) -> Any | None:
        """The live rig session (registration preview or re-edit), if any."""

        return self._rig_proto_session

    @property
    def rig_session_balloon_id(self) -> str | None:
        return self._rig_session_balloon_id

    @property
    def rig_registration_pending(self) -> bool:
        return self._rig_proto_session is not None and self._rig_session_balloon_id is None

    def start_rig_registration(self, body_layer_id: str, tail_layer_id: str) -> Any | None:
        """Register Body/Tail layers and show the live preview (no document
        change). None = the layers could not be registered; the reasons are
        in ``last_rig_reasons``."""

        self.last_rig_reasons: list[str] = []
        session = self.build_rig_session(body_layer_id, tail_layer_id)
        if session is None:
            self.last_rig_reasons = ["layers could not be captured"]
            return None
        session.mode = "contour_rebuild"
        if not session.register():
            self.last_rig_reasons = list(getattr(session.report, "reasons", []) or [])
            return None
        self.set_rig_proto_session(session)
        self._notify_panel()
        return session

    def cancel_rig_registration(self) -> None:
        if self.rig_registration_pending:
            self.set_rig_proto_session(None)
            self._notify_panel()

    def confirm_rig(
        self, *, text: str = "", vertical: bool = True, hide_source_layers: bool = True
    ) -> str | None:
        """The pending registration becomes a regular balloon: record +
        text + part layer composed from the record, sources hidden (one
        undo unit). The preview session is dropped; the new balloon is
        selected (and re-editable through its own session)."""

        from .raster_rig_commit import build_rig_command, build_rig_record  # noqa: PLC0415

        session = self._rig_proto_session
        state = self.read_state()
        if session is None or self._rig_session_balloon_id is not None or state is None:
            return None
        if session.pose is None or session.data is None:
            return None
        import uuid as _uuid  # noqa: PLC0415

        balloon_id = f"b{_uuid.uuid4().hex[:8]}"
        bh, bw = session.body_rgba.shape[:2]
        centre = (session.body_origin[0] + bw * 0.5, session.body_origin[1] + bh * 0.5)
        record = build_rig_record(
            session,
            balloon_id=balloon_id,
            panel_id=self.controller.panel_at(state, centre),
            body_source_layer_id=str(getattr(session, "body_layer_id", "") or ""),
            tail_source_layer_id=str(getattr(session, "tail_layer_id", "") or ""),
        )
        try:
            command = build_rig_command(
                self.controller,
                state,
                record,
                text=text,
                vertical=vertical,
                hide_source_layers=hide_source_layers,
            )
        except ValueError as exc:
            logger.info("rig conversion failed: %s", exc)
            return None
        # drop the preview BEFORE the command so the compositor shows the
        # committed part layer (the hidden sources no longer matter)
        self.set_rig_proto_session(None)
        if not self._submit(command):
            return None
        self.selected_balloon_id = balloon_id
        self.mode = MODE_BALLOONS
        self._refresh_canvas()
        self._notify_panel()
        return balloon_id

    def set_rig_params(
        self,
        balloon_id: str,
        *,
        scale: float | None = None,
        mode: str | None = None,
        fidelity: float | None = None,
        tip_fidelity: float | None = None,
        inset_px: float | None = None,
        overlap_px: float | None | object = _UNSET,
        deform_kind: str | None | object = _UNSET,
    ) -> bool:
        """Change compositing parameters of a committed rig balloon (one
        undo unit; the part layer is recomposed by the command)."""

        from .model import is_raster_rig  # noqa: PLC0415

        state = self.read_state()
        record = state.balloons.get(balloon_id) if state else None
        if record is None or not is_raster_rig(record):
            return False
        renderer = dict(record.renderer or {})
        if scale is not None:
            renderer["scale"] = float(scale)
        if mode is not None:
            renderer["mode"] = str(mode)
        if fidelity is not None:
            from .raster_rig.contour_detail import normalized_fidelity  # noqa: PLC0415

            renderer["fidelity"] = normalized_fidelity(fidelity)
        if tip_fidelity is not None:
            from .raster_rig.contour_detail import normalized_fidelity  # noqa: PLC0415

            renderer["tip_fidelity"] = normalized_fidelity(tip_fidelity)
        if inset_px is not None:
            renderer["inset_px"] = float(inset_px)
        if overlap_px is not _UNSET:
            renderer["overlap_px"] = None if overlap_px is None else float(overlap_px)
        if deform_kind is not _UNSET:
            renderer["deform_kind"] = deform_kind
        if renderer == (record.renderer or {}):
            return True
        return self.update_balloon(replace(record, renderer=renderer))

    def _ensure_rig_edit_session(self, state: ComicPageState | None) -> None:
        """Keep a live session for the SELECTED rig balloon (root/tip handles
        + transient preview over its part layer); drop it when something
        else is selected; re-sync it when the record changed underneath
        (undo / redo / parameter edit)."""

        if self._rig_proto_session is not None and self._rig_session_balloon_id is None:
            return  # a registration preview is active
        from .model import is_raster_rig  # noqa: PLC0415

        selected = state.balloons.get(self.selected_balloon_id or "") if state else None
        if selected is None or not is_raster_rig(selected):
            if self._rig_session_balloon_id is not None:
                self.set_rig_proto_session(None)
            return
        session = self._rig_proto_session
        from .raster_rig_commit import _cache_key  # noqa: PLC0415

        w, h = self._page_size()
        # Only source crops, placements and canvas dimensions require a new
        # session. Scale/deform parameters are deliberately re-synced in place.
        record_key = _cache_key(selected, (int(w), int(h)))[:4]
        if (
            session is not None
            and self._rig_session_balloon_id == selected.balloon_id
            and self._rig_session_record_key == record_key
        ):
            if self._rig_session_payload == selected.renderer or session.dragging:
                return
            from .raster_rig_commit import _apply_renderer_params  # noqa: PLC0415

            _apply_renderer_params(session, selected.renderer or {})
            session.preview_quality = "settle"
            session.update_preview()
            self._rig_session_payload = copy.deepcopy(selected.renderer)
            self._sync_rig_preview(full_body=True)
            return
        from .raster_parts import find_part_layer  # noqa: PLC0415
        from .raster_rig_commit import rig_session_from_record  # noqa: PLC0415

        canvas = self._canvas()
        try:
            session = rig_session_from_record(selected, (int(w), int(h)), use_cache=False)
        except Exception:
            logger.exception("rig edit session build failed")
            session = None
        if session is None:
            self.set_rig_proto_session(None)
            return
        session.preview_quality = "settle"
        session.update_preview()
        part = find_part_layer(canvas, selected.balloon_id) if canvas is not None else None
        session.body_layer_id = str(getattr(part, "id", "") or "") or None
        session.tail_layer_id = None
        self._rig_session_payload = copy.deepcopy(selected.renderer)
        self._rig_session_record_key = record_key
        self.set_rig_proto_session(session, balloon_id=selected.balloon_id)

    def _commit_rig_drag(self) -> None:
        """Drag released on a committed rig balloon: one undo unit with the
        new pose (the part layer is recomposed from the record)."""

        from .raster_rig_commit import with_renderer_pose  # noqa: PLC0415

        session = self._rig_proto_session
        balloon_id = self._rig_session_balloon_id
        state = self.read_state()
        record = state.balloons.get(balloon_id or "") if state else None
        if session is None or record is None or session.pose is None:
            return
        updated = with_renderer_pose(record, session)
        self._rig_session_payload = copy.deepcopy(updated.renderer)
        self.update_balloon(updated)

    def build_rig_session(self, body_layer_id: str, tail_layer_id: str) -> Any | None:
        """Session from two raster layers (pixels copied read-only, cropped
        to the drawn pixels + a transparent margin)."""

        from .balloonize_service import _qimage_to_rgba  # noqa: PLC0415
        from .raster_rig_commit import tight_crop_rgba  # noqa: PLC0415
        from .raster_rig_proto import RasterRigProtoSession  # noqa: PLC0415

        canvas = self._canvas()
        if canvas is None:
            return None
        try:
            pairs = []
            for layer_id in (body_layer_id, tail_layer_id):
                layer = canvas._get_layer_by_id(layer_id)  # noqa: SLF001
                image = getattr(layer, "image", None)
                if layer is None or image is None:
                    return None
                rgba = self._rig_proto_apply_layer_mask(layer, _qimage_to_rgba(image))
                placed, origin = self._rig_proto_layer_document_rgba(layer, rgba)
                cropped = tight_crop_rgba(placed, origin)
                if cropped is None:
                    return None
                pairs.append(cropped)
        except Exception:
            logger.exception("rig layer access failed")
            return None
        (body, boff), (tail, toff) = pairs
        w, h = self._page_size()
        backdrop = self._rig_proto_backdrop(
            canvas, (body_layer_id, tail_layer_id), (int(w), int(h))
        )
        session = RasterRigProtoSession(
            backdrop_rgba=backdrop,
            body_rgba=body,
            body_origin=(float(boff[0]), float(boff[1])),
            tail_rgba=tail,
            tail_origin=(float(toff[0]), float(toff[1])),
            canvas_size=(int(w), int(h)),
        )
        session.body_layer_id = str(body_layer_id)
        session.tail_layer_id = str(tail_layer_id)
        return session

    @staticmethod
    def _rig_proto_apply_layer_mask(layer: Any, rgba: Any) -> Any:
        """Bake an enabled layer mask (luminance coverage, the same read the
        compositor uses) into the captured alpha so the transient preview —
        whose snapshot carries no mask — composites like the real layer."""

        mask = getattr(layer, "layer_mask", None)
        if mask is None or not bool(getattr(layer, "mask_enabled", True)):
            return rgba
        try:
            import numpy as np  # noqa: PLC0415

            from loomstroke.core.mask_coverage import mask_coverage_alpha8  # noqa: PLC0415

            if mask.isNull():
                return rgba
            alpha8 = mask_coverage_alpha8(mask)
            h, w = rgba.shape[:2]
            if alpha8.width() != w or alpha8.height() != h:
                return rgba
            ptr = alpha8.constBits()
            cov = np.frombuffer(ptr, np.uint8, count=alpha8.bytesPerLine() * h).reshape(
                h, alpha8.bytesPerLine()
            )[:, :w]
            out = np.array(rgba, copy=True)
            out[:, :, 3] = (out[:, :, 3].astype(np.uint16) * cov.astype(np.uint16) // 255).astype(
                np.uint8
            )
            return out
        except Exception:
            logger.debug("layer mask bake failed; using unmasked capture", exc_info=True)
            return rgba

    @staticmethod
    def _rig_proto_layer_document_rgba(layer: Any, rgba: Any) -> tuple[Any, tuple[float, float]]:
        """(RGBA in DOCUMENT space, origin) for a raster layer — the same
        placement the renderer uses: a non-identity layer transform (scale /
        rotation / translation) is baked via its affine, otherwise the
        integer layer offset applies. Without this, a moved/scaled layer's
        capture would sit a few px away from what the user sees."""

        import numpy as np  # noqa: PLC0415

        from loomstroke.core.command_layer_access import _layer_offset  # noqa: PLC0415
        from loomstroke.ui.canvas.wgpu_backend.transform_processor import (  # noqa: PLC0415
            WgpuTransformProcessor,
        )

        affine = None
        try:
            affine = WgpuTransformProcessor._explicit_affine(layer)  # noqa: SLF001
        except Exception:
            affine = None
        if affine is None:
            ox, oy = _layer_offset(layer)
            return np.ascontiguousarray(rgba), (float(ox), float(oy))
        import cv2  # noqa: PLC0415

        a, b, c, d, tx, ty = affine
        h, w = rgba.shape[:2]
        corners = np.array(
            [
                [tx, ty],
                [a * w + tx, c * w + ty],
                [b * h + tx, d * h + ty],
                [a * w + b * h + tx, c * w + d * h + ty],
            ]
        )
        x0, y0 = float(np.floor(corners[:, 0].min())), float(np.floor(corners[:, 1].min()))
        x1, y1 = float(np.ceil(corners[:, 0].max())), float(np.ceil(corners[:, 1].max()))
        out_w, out_h = max(1, int(x1 - x0)), max(1, int(y1 - y0))
        # premultiplied warp (no colour bleed), straight result
        src = rgba.astype(np.float32)
        alpha = src[:, :, 3:4] / 255.0
        premul = np.concatenate([src[:, :, :3] * alpha, src[:, :, 3:4]], axis=2)
        mat = np.array([[a, b, tx - x0], [c, d, ty - y0]], np.float64)
        warped = cv2.warpAffine(
            premul, mat, (out_w, out_h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT
        )
        wa = warped[:, :, 3:4]
        rgb = np.where(wa > 0.5, warped[:, :, :3] * (255.0 / np.maximum(wa, 1e-3)), 0.0)
        out = np.empty((out_h, out_w, 4), np.uint8)
        out[:, :, :3] = np.clip(np.rint(rgb), 0, 255)
        out[:, :, 3] = np.clip(np.rint(wa[:, :, 0]), 0, 255)
        return out, (x0, y0)

    def _rig_proto_backdrop(
        self, canvas: Any, exclude_ids: tuple[str, str], size: tuple[int, int]
    ) -> Any | None:
        """Approximate composite of every OTHER raster layer (bottom-up
        source-over, blend modes ignored) — what a transparent opening
        reveals in the preview. Best effort; None on any failure."""

        try:
            import numpy as np  # noqa: PLC0415

            from .balloonize_service import _qimage_to_rgba  # noqa: PLC0415

            w, h = size
            out = np.zeros((h, w, 4), np.float32)
            stack = list(getattr(canvas, "_layers", None) or [])
            flat = []
            while stack:
                layer = stack.pop(0)
                stack.extend(getattr(layer, "children", None) or [])
                flat.append(layer)
            for layer in flat:
                lid = str(getattr(layer, "id", "") or "")
                if lid in exclude_ids or getattr(layer, "image", None) is None:
                    continue
                if getattr(layer, "visible", True) is False:
                    continue
                rgba, (fx, fy) = self._rig_proto_layer_document_rgba(
                    layer, _qimage_to_rgba(layer.image)
                )
                ox, oy = round(fx), round(fy)
                x0, y0 = max(0, ox), max(0, oy)
                x1 = min(w, ox + rgba.shape[1])
                y1 = min(h, oy + rgba.shape[0])
                if x1 <= x0 or y1 <= y0:
                    continue
                src = rgba[y0 - oy : y1 - oy, x0 - ox : x1 - ox].astype(np.float32)
                sa = src[:, :, 3:4] / 255.0
                dst = out[y0:y1, x0:x1]
                dst[:, :, :3] = src[:, :, :3] * sa + dst[:, :, :3] * (1.0 - sa)
                dst[:, :, 3:4] = np.maximum(dst[:, :, 3:4], src[:, :, 3:4])
            return np.clip(np.rint(out), 0, 255).astype(np.uint8)
        except Exception:
            logger.debug("rig proto backdrop capture failed", exc_info=True)
            return None

    def set_rig_proto_session(self, session: Any | None, *, balloon_id: str | None = None) -> None:
        self._rig_proto_session = session
        self._rig_session_balloon_id = balloon_id if session is not None else None
        if session is None:
            self._rig_session_payload = None
        self._rig_proto_hover = None
        self._install_rig_preview(session)
        self._refresh_canvas()

    # ── transient compositor preview (real layer order / blend / opacity) ──
    def _install_rig_preview(self, session: Any | None) -> None:
        from .raster_rig_preview_override import (  # noqa: PLC0415
            TransientRasterRigPreview,
            install,
        )

        canvas = self._canvas()
        install(canvas, None)
        self._rig_preview = None
        self._rig_preview_last_band = None
        self._rig_preview_last_tail = None
        if session is None or canvas is None or session.composer is None:
            return
        body_id = getattr(session, "body_layer_id", None)
        if not body_id or not hasattr(canvas, "_layers"):
            return  # no document scene -> the overlay fallback draws the patch
        try:
            preview = TransientRasterRigPreview(
                body_layer_id=str(body_id),
                canvas_size=tuple(session.canvas_size),
                z_order=str(session.composer.recipe.z_order),
            )
            preview.set_body_full(session.composer.body_canvas)
            install(canvas, preview)
            self._rig_preview = preview
            self._sync_rig_preview(full_body=True)
        except Exception:
            logger.exception("rig preview override install failed")
            install(canvas, None)
            self._rig_preview = None

    def _sync_rig_preview(self, *, full_body: bool = False) -> None:
        """Push the composer's current body region WITH the posed tail patch
        (plus what its junction compositing paints over the tail) composited
        in — ONE virtual layer in document space — into the transient
        preview (dirty rect = opening band + tail rects, old and new).

        A separate tail layer at the body's opacity / blend would stack with
        the body where they overlap (50 % body + 50 % tail = 75 % at the
        junction, QA-3 finding 2026-08-23); composited into the body buffer
        the preview follows the layer's opacity / blend exactly."""

        preview, session = self._rig_preview, self._rig_proto_session
        if preview is None or session is None or session.composer is None:
            return
        enabled = self._rig_session_balloon_id is None or session.dragging
        if enabled and not preview.enabled:
            full_body = True  # rebuild the complete preview when a new drag starts
        preview.enabled = enabled
        if not enabled:
            return  # selection/cancel must display the committed document pixels
        composer = session.composer
        if composer.recipe.z_order != preview.z_order:
            preview.z_order = str(composer.recipe.z_order)
        from .raster_rig.compose import _clip_rect, _over, _rect_union  # noqa: PLC0415

        band = composer.opening_rect()
        patch_fn = getattr(composer, "preview_tail_patch", None)
        patch = patch_fn() if callable(patch_fn) else None
        tail_rect = patch[0] if patch is not None else None
        canvas_w, canvas_h = session.canvas_size

        def _merged(rect: tuple[int, int, int, int]) -> Any:
            region = session.body_preview_region(rect)
            if patch is None:
                return region
            (tx0, ty0, tw, th), trgba = patch
            x0, y0, w, h = rect
            ix0, iy0 = max(tx0, x0), max(ty0, y0)
            ix1, iy1 = min(tx0 + tw, x0 + w), min(ty0 + th, y0 + h)
            if ix1 <= ix0 or iy1 <= iy0:
                return region
            sub = region[iy0 - y0 : iy1 - y0, ix0 - x0 : ix1 - x0]
            tsub = trgba[iy0 - ty0 : iy1 - ty0, ix0 - tx0 : ix1 - tx0]
            if composer.recipe.z_order == "tail_under":
                under = tsub.copy()
                _over(under, sub)  # body over the tail
                sub[...] = under
            else:
                _over(sub, tsub)  # tail over the body
            return region

        if full_body:
            preview.set_body_full(_merged((0, 0, canvas_w, canvas_h)))
        else:
            rect = _clip_rect(
                _rect_union(
                    band, self._rig_preview_last_band, tail_rect, self._rig_preview_last_tail
                ),
                canvas_w,
                canvas_h,
            )
            if rect is not None:
                preview.update_body_region(rect, _merged(rect))
        self._rig_preview_last_band = band
        self._rig_preview_last_tail = tail_rect
        preview.set_tail(None, None)  # no separate tail layer (see above)
        preview.last_tail_bytes = 0 if patch is None else int(patch[1].nbytes)

    def refresh_rig_proto(self) -> None:
        self._refresh_canvas()
        widget = self._panel_widget
        if widget is not None and hasattr(widget, "refresh_rig_perf"):
            widget.refresh_rig_perf()

    def _rig_proto_pointer_down(self, point: tuple[float, float]) -> bool:
        session = self._rig_proto_session
        if session is None:
            return False
        kind = session.hit_test(point, self._view_zoom())
        if kind is None:
            return False
        session.begin_drag(kind)
        session.drag_to(point)
        self._refresh_canvas()
        return True

    def _paint_rig_proto(self, painter: Any, scale: float) -> None:
        session = self._rig_proto_session
        if session is None or session.pose is None:
            return
        import time as _time  # noqa: PLC0415

        import numpy as np  # noqa: PLC0415
        from PySide6.QtCore import QPointF, QRectF  # noqa: PLC0415
        from PySide6.QtGui import QColor, QImage, QPen, QPolygonF  # noqa: PLC0415

        # with the compositor override active the canvas itself shows the
        # preview (real layer order / blend / opacity); the overlay draws
        # only the handles. Without it (no document scene) fall back to the
        # approximate patch overlay.
        if self._rig_preview is None:
            patch = session.preview_patch()
            if patch is not None:
                t0 = _time.perf_counter()
                (x, y, w, h), rgba = patch
                arr = np.ascontiguousarray(rgba)
                image = QImage(arr.data, w, h, w * 4, QImage.Format.Format_RGBA8888)
                painter.drawImage(QRectF(x, y, w, h), image)
                if session.perf:
                    session.perf[-1].convert_ms = (_time.perf_counter() - t0) * 1000.0
        if not self._rig_show_handles:
            return
        from .contour_handle_overlay import paint_middle_handle  # noqa: PLC0415

        paint_middle_handle(painter, session, scale, self._rig_proto_hover)
        root = session.root_point()
        tip = session.tip_point()
        drag_kind = session._drag["kind"] if session._drag else None  # noqa: SLF001
        r = 7.0 / scale
        if root is not None:
            # root: CIRCLE on the outline (orange); hover/active states
            hover = self._rig_proto_hover == "root"
            state = "active" if drag_kind == "root" else ("hover" if hover else "idle")
            pen = QPen(QColor(255, 128, 0, 255 if state != "idle" else 200))
            pen.setWidthF((3.0 if state == "active" else 2.0) / scale)
            painter.setPen(pen)
            painter.setBrush(QColor(255, 128, 0, {"idle": 60, "hover": 130, "active": 200}[state]))
            painter.drawEllipse(QPointF(*root), r, r)
        if tip is not None:
            # tip: DIAMOND (blue) — distinguishable by shape, not colour only
            hover = self._rig_proto_hover == "tip"
            state = "active" if drag_kind == "tip" else ("hover" if hover else "idle")
            pen = QPen(QColor(30, 120, 255, 255 if state != "idle" else 200))
            pen.setWidthF((3.0 if state == "active" else 2.0) / scale)
            painter.setPen(pen)
            painter.setBrush(QColor(30, 120, 255, {"idle": 60, "hover": 130, "active": 200}[state]))
            tx, ty = tip
            painter.drawPolygon(
                QPolygonF(
                    [
                        QPointF(tx, ty - r * 1.2),
                        QPointF(tx + r * 1.2, ty),
                        QPointF(tx, ty + r * 1.2),
                        QPointF(tx - r * 1.2, ty),
                    ]
                )
            )

    def build_commands(self) -> list[Any]:
        """ToolManager hook: comic edits submit their own single-undo
        commands inside the pointer handlers, so nothing is queued here."""

        return []

    def on_key(self, key: str, pressed: bool) -> bool:
        if (
            pressed
            and key.lower() in ("escape", "esc")
            and self.frame_workflow.vector_erasure.target
        ):
            self.frame_workflow.vector_erasure.cancel()
            return True
        session = self._rig_proto_session
        escape = pressed and key.lower() in ("escape", "esc")
        if escape and session is not None and session.dragging:
            # pointer cancel: ephemeral preview reverts to the drag-start pose
            session.cancel_drag()
            self._refresh_canvas()
            return True
        if pressed and key.lower() in ("escape", "esc") and self._drag is not None:
            self._drag = None
            self._refresh_canvas()
            return True
        return False

    # ── overlay ────────────────────────────────────────────────────────

    def _canvas_widget(self) -> Any:
        ui = getattr(self.host_api, "ui", None) if self.host_api else None
        getter = getattr(ui, "get_main_window", None) if ui else None
        if not callable(getter):
            return None
        try:
            main_window = getter()
        except Exception:
            return None
        return getattr(main_window, "canvas", None)

    def _install_overlay(self, enabled: bool) -> None:
        widget = self._canvas_widget()
        if widget is None or not hasattr(widget, "set_tool_overlay_fn"):
            return
        widget.set_tool_overlay_fn(self._paint_overlay if enabled else None)
        if hasattr(widget, "update"):
            widget.update()

    def _paint_overlay(self, painter: Any) -> None:
        try:
            self._paint_overlay_impl(painter)
        except Exception:
            logger.exception("comic overlay paint failed")

    def _paint_balloonize_preview(self, painter: Any, scale: float) -> None:
        context = self.pending_balloonize
        if context is None:
            return
        from PySide6.QtCore import QPointF, QRectF  # noqa: PLC0415
        from PySide6.QtGui import QColor, QPen  # noqa: PLC0415

        proposal = context.proposal
        source_pen = QPen(QColor(150, 150, 150, 180))
        source_pen.setWidthF(1.5 / scale)
        source_pen.setStyle(Qt_DashLine())
        painter.setPen(source_pen)
        painter.drawRect(QRectF(*proposal.source_bbox))

        body_pen = QPen(QColor(30, 160, 90, 235))
        body_pen.setWidthF(3.0 / scale)
        painter.setPen(body_pen)
        painter.drawRect(QRectF(*proposal.body_rect))

        text_pen = QPen(QColor(160, 90, 220, 220))
        text_pen.setWidthF(2.0 / scale)
        text_pen.setStyle(Qt_DashLine())
        painter.setPen(text_pen)
        painter.drawRect(QRectF(*proposal.text_frame_rect))

        if proposal.tail.confidence != "none":
            tail_pen = QPen(QColor(230, 60, 60, 235))
            tail_pen.setWidthF(3.0 / scale)
            painter.setPen(tail_pen)
            painter.drawLine(QPointF(*proposal.tail.connect_point), QPointF(*proposal.tail.tip))
            radius = 6.0 / scale
            painter.drawEllipse(QPointF(*proposal.tail.tip), radius, radius)

    def _paint_preserve_preview(self, painter: Any, scale: float) -> None:
        proposal = self.pending_preserve
        if proposal is None:
            return
        from PySide6.QtCore import QPointF, QRectF  # noqa: PLC0415
        from PySide6.QtGui import QColor, QPen, QPolygonF  # noqa: PLC0415

        body_pen = QPen(QColor(30, 160, 90, 235))
        body_pen.setWidthF(2.5 / scale)
        painter.setPen(body_pen)
        painter.drawPolygon(QPolygonF([QPointF(x, y) for x, y in proposal.body.points]))

        if proposal.tail is not None:
            tail_pen = QPen(QColor(230, 60, 60, 235))
            tail_pen.setWidthF(2.5 / scale)
            painter.setPen(tail_pen)
            painter.drawPolygon(QPolygonF([QPointF(x, y) for x, y in proposal.tail.points]))

        frame_pen = QPen(QColor(160, 90, 220, 220))
        frame_pen.setWidthF(2.0 / scale)
        frame_pen.setStyle(Qt_DashLine())
        painter.setPen(frame_pen)
        painter.drawRect(QRectF(*proposal.text_frame_rect))

        # overall bounds (body + detached tail together)
        xs = [x for x, _ in proposal.body.points]
        ys = [y for _, y in proposal.body.points]
        if proposal.tail is not None:
            xs += [x for x, _ in proposal.tail.points]
            ys += [y for _, y in proposal.tail.points]
        bounds_pen = QPen(QColor(120, 120, 120, 160))
        bounds_pen.setWidthF(1.5 / scale)
        bounds_pen.setStyle(Qt_DashLine())
        painter.setPen(bounds_pen)
        painter.drawRect(QRectF(min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys)))

    def _paint_drag_ghost(  # noqa: PLR0911, PLR0912, PLR0915 - one branch per drag kind
        self, painter: Any, scale: float
    ) -> None:
        """Live preview while dragging: what release will commit."""

        drag = self._drag
        if drag is None:
            return
        point = drag.get("last_point")
        if point is None:
            return
        from PySide6.QtCore import QPointF, QRectF  # noqa: PLC0415
        from PySide6.QtGui import QColor, QPen, QPolygonF  # noqa: PLC0415

        pen = QPen(QColor(255, 160, 0, 230))
        pen.setWidthF(2.0 / scale)
        pen.setStyle(Qt_DashLine())
        painter.setPen(pen)
        kind = drag.get("kind")
        state: ComicPageState = drag.get("state")
        if kind == "resize":
            ax, ay = drag["anchor"]
            painter.drawRect(
                QRectF(
                    min(ax, point[0]),
                    min(ay, point[1]),
                    max(24.0, abs(point[0] - ax)),
                    max(24.0, abs(point[1] - ay)),
                )
            )
            return
        if kind == "boundary":
            hit = drag.get("hit")
            if hit is not None:
                if hit.orientation == "vertical":
                    painter.drawLine(QPointF(point[0], 0.0), QPointF(point[0], 1e6))
                else:
                    painter.drawLine(QPointF(0.0, point[1]), QPointF(1e6, point[1]))
            return
        record = state.balloons.get(drag.get("balloon_id") or "") if state else None
        if record is None:
            return
        if kind == "move":
            ox, oy = drag["origin"]
            dx, dy = point[0] - ox, point[1] - oy
            x, y, w, h = drag["body_rect"]
            painter.drawRect(QRectF(x + dx, y + dy, w, h))
            if record.tail_rect is not None:
                tx, ty, tw, th = record.tail_rect
                painter.drawRect(QRectF(tx + dx, ty + dy, tw, th))
            return
        if kind == "tail_bend":
            axis = self._tail_axis(record)
            if axis is not None:
                painter.drawLine(QPointF(*axis[0]), QPointF(*point))
                painter.drawLine(QPointF(*point), QPointF(*axis[1]))
            radius = 5.0 / scale
            painter.drawEllipse(QPointF(*point), radius, radius)
            return
        if kind == "text_frame":
            ax, ay = drag["anchor"]
            painter.drawRect(
                QRectF(
                    min(ax, point[0]),
                    min(ay, point[1]),
                    max(8.0, abs(point[0] - ax)),
                    max(8.0, abs(point[1] - ay)),
                )
            )
            return
        if kind in ("tail_base", "tail_root"):
            from .raster_parts import (  # noqa: PLC0415
                effective_tail_base_params,
                outline_point_at_param,
                tail_render_transform,
            )

            if kind == "tail_root":
                preview = self._tail_root_dragged(record, drag["root_index"], drag["press"], point)
            else:
                preview = self._tail_base_dragged(record, drag["base_index"], point)
            eff = effective_tail_base_params(preview, preview.tail.target) or (
                preview.tail_base_params or (0.0, 0.0)
            )
            radius = 5.0 / scale
            for param in eff:
                px_, py_ = outline_point_at_param(preview, param)
                painter.drawEllipse(QPointF(px_, py_), radius, radius)
            if len(preview.tail_outline) >= 3 and preview.tail_rect is not None:
                transform = tail_render_transform(preview, preview.tail.target)
                if transform is not None:
                    from .model import denormalize_outline  # noqa: PLC0415

                    tx0, ty0 = preview.tail_rect[0], preview.tail_rect[1]
                    ghost_points = [
                        transform.map(QPointF(px - tx0, py - ty0))
                        for px, py in denormalize_outline(preview.tail_outline, preview.tail_rect)
                    ]
                    painter.drawPolygon(QPolygonF(ghost_points))
            return
        if kind == "tail":
            if record.tail_local_root is not None and len(record.tail_outline) >= 3:
                from .model import denormalize_outline  # noqa: PLC0415
                from .raster_parts import tail_render_transform  # noqa: PLC0415

                transform = tail_render_transform(record, point)
                if transform is not None and record.tail_rect is not None:
                    tx, ty = record.tail_rect[0], record.tail_rect[1]
                    ghost_points = []
                    for px, py in denormalize_outline(record.tail_outline, record.tail_rect):
                        mapped = transform.map(QPointF(px - tx, py - ty))
                        ghost_points.append(mapped)
                    painter.drawPolygon(QPolygonF(ghost_points))
                    return
            # fallback: root->tip line
            painter.drawLine(QPointF(*record.tail.target), QPointF(*point))

    def _paint_overlay_impl(self, painter: Any) -> None:
        from PySide6.QtCore import QPointF, QRectF  # noqa: PLC0415
        from PySide6.QtGui import QColor, QPen  # noqa: PLC0415

        from .frame_view_geometry import panel_view_rects  # noqa: PLC0415

        state = self.read_state()
        if state is None:
            return
        transform = painter.transform()
        scale = max(0.05, float(transform.m11()))
        self._ensure_rig_edit_session(state)
        self._paint_rig_proto(painter, scale)
        self._paint_balloonize_preview(painter, scale)
        self._paint_preserve_preview(painter, scale)
        self._paint_drag_ghost(painter, scale)
        pen = QPen(QColor(30, 120, 255, 220))
        pen.setWidthF(2.0 / scale)
        painter.setPen(pen)
        rects = panel_view_rects(self.frame_workflow, state)
        for panel_id, (x, y, w, h) in rects.items():
            painter.drawRect(QRectF(x, y, w, h))
            order = (
                state.reading_order.index(panel_id) + 1 if panel_id in state.reading_order else 0
            )
            painter.drawText(QPointF(x + 8 / scale, y + 22 / scale), str(order))
        if self.selected_panel_id and self.selected_panel_id in rects:
            x, y, w, h = rects[self.selected_panel_id]
            selected_pen = QPen(QColor(255, 128, 0, 235))
            selected_pen.setWidthF(3.0 / scale)
            painter.setPen(selected_pen)
            painter.drawRect(QRectF(x, y, w, h))
        self._paint_balloonize_preview(painter, scale)
        from .model import is_raster_rig  # noqa: PLC0415

        for balloon_id, record in state.balloons.items():
            if is_raster_rig(record):
                # root/tip handles come from the rig session; only the
                # selection rect is drawn here
                if balloon_id == self.selected_balloon_id:
                    x, y, w, h = record.body_rect
                    sel_pen = QPen(QColor(255, 60, 60, 235))
                    sel_pen.setWidthF(2.0 / scale)
                    painter.setPen(sel_pen)
                    painter.drawRect(QRectF(x, y, w, h))
                    self._paint_text_frame_handles(painter, record, scale)
                continue
            tx, ty = record.tail.target
            handle_pen = QPen(
                QColor(255, 60, 60, 235)
                if balloon_id == self.selected_balloon_id
                else QColor(120, 120, 120, 200)
            )
            handle_pen.setWidthF(2.0 / scale)
            painter.setPen(handle_pen)
            radius = 5.0 / scale
            painter.drawEllipse(QPointF(tx, ty), radius, radius)
            if balloon_id == self.selected_balloon_id:
                x, y, w, h = record.body_rect
                painter.drawRect(QRectF(x, y, w, h))
                handle = 4.0 / scale
                for cx, cy in ((x, y), (x + w, y), (x + w, y + h), (x, y + h)):
                    painter.drawRect(QRectF(cx - handle, cy - handle, handle * 2, handle * 2))
                self._paint_text_frame_handles(painter, record, scale)
                self._paint_tail_handles(painter, record, scale)

    def _paint_text_frame_handles(self, painter: Any, record: BalloonRecord, scale: float) -> None:
        """Text box of the selected balloon: dashed purple rect + 4 corner
        handles (drag to resize the text frame)."""

        from PySide6.QtCore import QRectF  # noqa: PLC0415
        from PySide6.QtGui import QColor, QPen  # noqa: PLC0415

        from .text_frames import balloon_text_rect  # noqa: PLC0415

        x, y, w, h = balloon_text_rect(record)
        painter.save()
        pen = QPen(QColor(160, 80, 220, 200))
        pen.setWidthF(1.0 / scale)
        pen.setStyle(Qt_DashLine())
        painter.setPen(pen)
        painter.drawRect(QRectF(x, y, w, h))
        pen.setStyle(Qt_SolidLine())
        pen.setWidthF(1.5 / scale)
        painter.setPen(pen)
        handle = 3.0 / scale
        for cx, cy in ((x, y), (x + w, y), (x + w, y + h), (x, y + h)):
            painter.drawRect(QRectF(cx - handle, cy - handle, handle * 2, handle * 2))
        painter.restore()

    def _paint_tail_handles(self, painter: Any, record: BalloonRecord, scale: float) -> None:
        """Procedural-tail anchors of the selected balloon: green base
        anchors on the outline, dashed axis, orange mid bend anchor."""

        if record.tail_base_params is None or not record.tail.enabled:
            return
        from PySide6.QtCore import QPointF  # noqa: PLC0415
        from PySide6.QtGui import QColor, QPen  # noqa: PLC0415

        from .raster_parts import (  # noqa: PLC0415
            effective_tail_base_params,
            outline_point_at_param,
        )

        tip = record.tail.target
        params = effective_tail_base_params(record, tip) or record.tail_base_params
        base_a = outline_point_at_param(record, params[0])
        base_b = outline_point_at_param(record, params[1])
        axis = self._tail_axis(record)
        bend = self._tail_bend_handle_pos(record)
        radius = 5.0 / scale
        painter.save()
        if axis is not None and bend is not None:
            axis_pen = QPen(QColor(255, 160, 0, 160))
            axis_pen.setWidthF(1.0 / scale)
            axis_pen.setStyle(Qt_DashLine())
            painter.setPen(axis_pen)
            painter.drawLine(QPointF(*axis[0]), QPointF(*bend))
            painter.drawLine(QPointF(*bend), QPointF(*tip))
        base_pen = QPen(QColor(40, 190, 90, 235))
        base_pen.setWidthF(2.0 / scale)
        painter.setPen(base_pen)
        for bx, by in (base_a, base_b):
            painter.drawEllipse(QPointF(bx, by), radius, radius)
        # tail-side connection anchors (root corners, blue)
        from .raster_parts import tail_root_corners  # noqa: PLC0415

        corners = tail_root_corners(record, tip)
        if corners is not None:
            root_pen = QPen(QColor(50, 130, 255, 235))
            root_pen.setWidthF(2.0 / scale)
            painter.setPen(root_pen)
            for cx, cy in corners:
                painter.drawEllipse(QPointF(cx, cy), radius, radius)
        if bend is not None:
            bend_pen = QPen(QColor(255, 160, 0, 235))
            bend_pen.setWidthF(2.0 / scale)
            painter.setPen(bend_pen)
            painter.drawEllipse(QPointF(*bend), radius, radius)
        painter.restore()


def create_plugin() -> ComicToolsPlugin:
    return ComicToolsPlugin()


__all__ = ["PANEL_ID", "PLUGIN_ID", "TOOL_ID", "ComicToolsPlugin", "create_plugin"]


def Qt_DashLine():  # noqa: N802 - tiny Qt enum accessor kept lazy
    from PySide6.QtCore import Qt  # noqa: PLC0415

    return Qt.PenStyle.DashLine


def Qt_SolidLine():  # noqa: N802 - tiny Qt enum accessor kept lazy
    from PySide6.QtCore import Qt  # noqa: PLC0415

    return Qt.PenStyle.SolidLine
