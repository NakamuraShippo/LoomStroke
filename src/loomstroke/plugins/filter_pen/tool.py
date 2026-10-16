"""Independent Filter Pen input; pixel editing belongs to the host service."""

from __future__ import annotations

import logging
from copy import deepcopy
from typing import Any

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QWidget
from shiboken6 import isValid as is_valid

from loomstroke.core.filter_pen.compatibility import bind_legacy_names as _bind_legacy_names
from loomstroke.core.filters import FilterRegistry
from loomstroke.host_api import StrokePoint
from loomstroke.plugins.sdk.drawing_assist import ToolAssistBridge
from loomstroke.plugins.sdk.tool import ContextToolbarSpec, ContextWidgetSpec

from .metadata import descriptor, filter_name, metadata, resolve_filter, tr
from .recontour.controller import FilterPenRecontourController
from .settings import FilterPenSettings
from .stroke_input import FilterPenStrokeInput
from .tip_library import TipLibrary

logger = logging.getLogger(__name__)


class FilterPenTool(QObject):
    settings_changed = Signal(dict)
    options_widget_in_properties = True
    options_widget_property_keys = ("effect",)

    def __init__(self, host_api: Any = None) -> None:
        super().__init__()
        self.host_api = host_api
        self.settings = FilterPenSettings()
        self._service: Any = None
        self._input: FilterPenStrokeInput | None = None
        self._assist = ToolAssistBridge("effect_pen")
        self._subscriptions: list[tuple[str, Any, Any]] = []
        self._dialog: Any = None
        self._tip_library: Any = None
        self._stroke_page: Any = None
        self._stroke_canvas: Any = None
        self._stroke_layer: str | None = None
        self._lanes: dict[int, list] = {}
        self._stroke_recontour = False
        self._stroke_smoothness = self.settings.recontour_smoothness
        self.is_stroking = False
        self._recontour = FilterPenRecontourController(self)
        if host_api is not None:
            self.activate(host_api)

    def descriptor(self):
        return descriptor()

    @property
    def tip_library(self):
        if self._tip_library is None:
            self._tip_library = TipLibrary()
        return self._tip_library

    def tool_metadata(self):
        return metadata(self.settings)

    def context_toolbar_spec(self) -> ContextToolbarSpec:
        return ContextToolbarSpec(
            show_trigger="tool_active",
            property_keys=["size", "opacity", "hardness"],
            placement="top_center",
            custom_widgets=[
                ContextWidgetSpec("effect_settings", "button", label=tr("Filter Settings…"))
            ],
        )

    def activate(self, host_api: Any) -> None:
        self.host_api = host_api
        service = host_api.get_service("filter_pen")
        if service is None:
            service = host_api.get_service("effect_pen")
        if service is not self._service:
            if self._service is not None:
                self._service.failed.disconnect(self._failed)
                self._service.finished.disconnect(self._finished)
            self._service = service
            if service is not None:
                service.failed.connect(self._failed)
                service.finished.connect(self._finished)
        saved = host_api.settings.get("tools.effect_pen", {})
        if isinstance(saved, dict):
            self.set_settings(saved)

    def enable(self) -> None:
        bus = getattr(self.host_api, "events", None)
        subscribe = getattr(bus, "subscribe", None)
        if callable(subscribe) and not self._subscriptions:
            for topic in (
                "pages.active.changing",
                "layer.selected",
                "doc.created",
                "doc.loaded",
                "doc.opened",
                "doc.closed",
            ):
                handler = self._layer_selected if topic == "layer.selected" else self._boundary
                token = subscribe(topic, handler)
                self._subscriptions.append((topic, token, handler))
        if self._options_parent() is not None:
            self._show_options(activate=False)

    def disable(self) -> None:
        if self._dialog is not None:
            self._dialog.hide()
        self._recontour.close()
        self._assist.end()
        if self._service is not None:
            self._service.finish()
            self._service.finalize()
        self.is_stroking = False
        self._input = None
        bus = getattr(self.host_api, "events", None)
        unsubscribe = getattr(bus, "off", None)
        if callable(unsubscribe):
            for topic, token, _handler in self._subscriptions:
                unsubscribe(topic, token)
        self._subscriptions.clear()

    def deactivate(self) -> None:
        if self._dialog is not None:
            self._dialog.close()
        self.persist()
        self.disable()
        if self._service is not None:
            self._service.failed.disconnect(self._failed)
            self._service.finished.disconnect(self._finished)
            self._service.close()
        self._service = None
        self.host_api = None

    def persist(self) -> None:
        if self.host_api is not None:
            self.host_api.settings.set("tools.effect_pen", self.settings.to_dict())

    def get_settings(self) -> dict[str, Any]:
        values = self.settings.to_dict()
        values["effect"] = filter_name(self.settings.filter_type)
        return values

    def set_setting(self, key: str, value: Any) -> None:
        self.set_settings({key: value})

    def set_settings(self, values: dict[str, Any]) -> None:
        values = dict(values)
        for canonical, legacy in (
            ("filter", "effect"),
            ("filter_type", "effect_type"),
            ("filter_params", "effect_params"),
            ("params_by_filter", "params_by_effect"),
        ):
            if canonical in values:
                values[legacy] = values.pop(canonical)
        previous = self.get_settings()
        values = dict(values)
        if "effect" in values:
            values["effect_type"] = values.pop("effect")
        if "effect_type" in values:
            filter_id = resolve_filter(str(values["effect_type"]))
            if filter_id is None:
                self._failed("The selected filter is unavailable.")
                values.pop("effect_type")
            else:
                values["effect_type"] = filter_id
                if filter_id != self.settings.filter_type:
                    self.settings.params_by_filter[self.settings.filter_type] = deepcopy(
                        self.settings.filter_params
                    )
                    if "effect_params" not in values:
                        values["effect_params"] = self._filter_defaults(filter_id)
        self.settings.update(values)
        self.settings.params_by_filter[self.settings.filter_type] = deepcopy(
            self.settings.filter_params
        )
        if not self.settings.recontour:
            self._recontour.close()
        current = self.get_settings()
        self.settings_changed.emit(current)
        bus = getattr(self.host_api, "events", None)
        publish = getattr(bus, "publish", None)
        if callable(publish):
            for key, value in current.items():
                if previous.get(key) != value:
                    publish(
                        "tool.settings_changed",
                        {"tool_id": "effect_pen", "key": key, "value": value, "sync_only": True},
                    )

    def _filter_defaults(self, filter_id: str) -> dict[str, Any]:
        filter_cls = FilterRegistry.get(filter_id)
        result = {schema.key: deepcopy(schema.default) for schema in filter_cls().param_schema()}
        result.update(deepcopy(self.settings.params_by_filter.get(filter_id, {})))
        return result

    def set_filter(self, filter_type: str, params: dict[str, Any] | None = None) -> None:
        filter_id = resolve_filter(filter_type)
        if filter_id is None:
            self._failed("The selected filter is unavailable.")
            return
        defaults = self._filter_defaults(filter_id)
        if params is not None:
            defaults.update(deepcopy(params))
        self.set_settings({"effect_type": filter_id, "effect_params": defaults})

    def set_filter_params(self, params: dict[str, Any]) -> None:
        self.set_settings({"effect_params": params})

    def set_effect(self, effect_type: str, params: dict[str, Any] | None = None) -> None:
        """Compatibility entry point for the previous tool API."""
        self.set_filter(effect_type, params)

    set_effect_params = set_filter_params
    _effect_defaults = _filter_defaults

    def set_tip(self, path: str | None, hardness_mode: str = "tip") -> None:
        self.set_settings({"tip_path": path, "hardness_mode": hardness_mode})

    def _page(self):
        pages = getattr(self.host_api, "pages", None)
        getter = getattr(pages, "get_active_page_id", None)
        return getter() if callable(getter) else None

    def _valid_stroke(self) -> bool:
        if not self.is_stroking or self.host_api is None:
            return False
        if self._page() != self._stroke_page or self.host_api.canvas is not self._stroke_canvas:
            self._boundary()
            return False
        return True

    def _append(self, points: list[StrokePoint]) -> None:
        if not points or self._service is None:
            return
        lanes = self._assist.commands(points, FilterPenStrokeInput.command_points)
        for index, lane in enumerate(lanes):
            self._lanes.setdefault(index, []).extend(lane)
            self._service.append(lane)

    def on_pointer_down(self, point: StrokePoint, **_modifiers: Any) -> None:
        if self._recontour.press(point):
            return
        self._recontour.close()
        if self._service is None:
            self._failed("Filter Pen is unavailable.")
            return
        if self.is_stroking:
            self._service.finish()
        self._stroke_page = self._page()
        self._stroke_canvas = self.host_api.canvas
        self._stroke_layer = self.host_api.canvas.current_layer().id
        self._input = FilterPenStrokeInput(deepcopy(self.settings))
        self._stroke_recontour = self.settings.recontour
        self._stroke_smoothness = self.settings.recontour_smoothness
        accepted = self._service.begin(
            self.settings.filter_type,
            deepcopy(self.settings.filter_params),
            self.settings.brush(),
            tip_path=self.settings.tip_path,
            hardness_mode=self.settings.hardness_mode,
            tilt_angle=self.settings.tilt_angle,
            page_id=self._stroke_page,
        )
        if not accepted:
            self._input = None
            self.is_stroking = False
            return
        self.is_stroking = True
        self._lanes = {}
        point = self._assist.begin(self.host_api, point)
        self._append(self._input.begin(point))

    def on_pointer_move(self, points: list[StrokePoint]) -> None:
        if self._recontour.move(points):
            return
        if self._valid_stroke() and self._input is not None:
            self._append(self._input.move(self._assist.points(points)))

    def on_pointer_up(self, point: StrokePoint) -> None:
        if self._recontour.release(point):
            return
        if self._valid_stroke() and self._input is not None:
            self._append(self._input.finish(self._assist.point(point)))
            self.is_stroking = False
            self._assist.end()
            self._service.finish()

    def _finished(self) -> None:
        if (
            self.settings.recontour
            and self._stroke_recontour
            and not self.is_stroking
            and not self._recontour.has_path
        ):
            self._recontour.prepare(self._lanes, self._stroke_smoothness)

    def _layer_selected(self, payload: Any) -> None:
        layer_id = payload.get("layer_id") if isinstance(payload, dict) else None
        if layer_id != self._stroke_layer:
            self._boundary()

    def _boundary(self, *_args: Any, **_kwargs: Any) -> None:
        # The service owns command/history boundaries and validates versions.
        if self._service is not None and getattr(self._service, "updating", False):
            return
        self._recontour.close()
        if self.is_stroking and self._service is not None:
            self._service.finish()
            self.is_stroking = False
            self._assist.end()

    def on_key(self, key: str, pressed: bool) -> bool:
        if self._recontour.key(key, pressed):
            return True
        if pressed and key.lower() in ("esc", "escape") and self.is_stroking:
            self._service.cancel()
            self.is_stroking = False
            self._assist.end()
            return True
        return False

    def build_commands(self) -> list:
        # The service submits its own command-backed stroke updates.
        return []

    def create_options_widget(self):
        from .options_widget import FilterPenOptionsWidget  # noqa: PLC0415 - lazy UI creation

        return FilterPenOptionsWidget(self)

    def execute_context_action(self, action: str, **_kwargs: Any) -> dict:
        if action != "effect_settings":
            return {"handled": False, "status": "unsupported"}
        self._show_options(activate=True)
        return {"handled": True, "status": "ready"}

    def _options_parent(self) -> QWidget | None:
        ui = getattr(self.host_api, "ui", None)
        getter = getattr(ui, "get_main_window", None)
        window = getter() if callable(getter) else None
        return window if isinstance(window, QWidget) and is_valid(window) else None

    def _show_options(self, *, activate: bool) -> None:
        from .options_widget import FilterPenOptionsDialog  # noqa: PLC0415 - lazy UI creation

        if self._dialog is None:
            dialog = FilterPenOptionsDialog(self, self._options_parent())
            self._dialog = dialog
            dialog.finished.connect(lambda _result, target=dialog: self._dialog_closed(target))
            dialog.destroyed.connect(lambda *_args, target=dialog: self._dialog_closed(target))
        self._dialog.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, not activate)
        position = getattr(self._dialog, "_panel_position", None)
        if position is not None and not self._dialog.isVisible():
            position.restore()
        self._dialog.show()
        self._dialog.raise_()
        if activate:
            self._dialog.activateWindow()

    def _dialog_closed(self, dialog: Any) -> None:
        if self._dialog is dialog:
            self._dialog = None

    def _failed(self, message: str) -> None:
        logger.warning("Filter Pen: %s", message)
        ui = getattr(self.host_api, "ui", None)
        notify = getattr(ui, "show_notification", None)
        if callable(notify):
            notify(tr(message), level="warning")


# Compatibility aliases for existing imports.
EffectPenTool = FilterPenTool


_bind_legacy_names(
    __name__,
    {
        "EffectRegistry": "FilterRegistry",
        "effect_name": "filter_name",
        "resolve_effect": "resolve_filter",
        "EffectPenRecontourController": "FilterPenRecontourController",
        "EffectPenSettings": "FilterPenSettings",
        "EffectPenStrokeInput": "FilterPenStrokeInput",
        "EffectPenTool": "FilterPenTool",
    },
)
