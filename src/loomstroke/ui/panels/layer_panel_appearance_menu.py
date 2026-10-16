"""Layer panel menus for CT-21: Adjustment / Filter grouping, Fill Layer, Layer Styles.

Thin delegate kept out of ``layer_panel.py``; dialogs are injectable for tests.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtWidgets import QMenu

from loomstroke.core.appearance import AppearanceRegistry, FillSpec, LayerStyleStack
from loomstroke.ui.appearance_service import AppearanceService
from loomstroke.ui.translations import translated_literal_domain

__all__ = ["LayerPanelAppearanceMenuBuilder"]


def _ts(text: str) -> str:
    return translated_literal_domain("AppearancePanel", text, use_fallback=True)


class LayerPanelAppearanceMenuBuilder:
    def __init__(
        self,
        panel: Any,
        *,
        fill_dialog: Callable[..., FillSpec | None] | None = None,
        styles_dialog: Callable[..., Any] | None = None,
    ) -> None:
        self._panel = panel
        self._fill_dialog = fill_dialog or self._default_fill_dialog
        self._styles_dialog = styles_dialog or self._default_styles_dialog
        self._service: AppearanceService | None = None
        self._registry = AppearanceRegistry()

    # -- accessors ---------------------------------------------------------------------

    def service(self) -> AppearanceService | None:
        host = getattr(self._panel, "_host_api", None)
        if host is None:
            return None
        if self._service is None or self._service._host is not host:  # noqa: SLF001
            self._service = AppearanceService(host, registry=self._registry)
        return self._service

    @staticmethod
    def _filters() -> list[tuple[str, str, str]]:
        try:
            from loomstroke.core.filters import FilterRegistry  # noqa: PLC0415

            return list(FilterRegistry.list_filters())
        except Exception:
            return []

    # -- menus -------------------------------------------------------------------------

    def populate_filter_layer_menu(self, menu: QMenu) -> None:
        """Footer ``Add Filter Layer`` popup: Adjustment / Filter / Fill Layer groups."""
        filters = self._filters()
        adjustments = [e for e in filters if e[2] == "adjustment"]
        live_filters = [e for e in filters if e[2] != "adjustment"]
        adjustment_menu = QMenu(_ts("Adjustment Layer"), menu)
        menu.addMenu(adjustment_menu)
        self._fill_filter_entries(adjustment_menu, adjustments, self._standalone_filter_action())
        filter_menu = QMenu(_ts("Filter Layer"), menu)
        menu.addMenu(filter_menu)
        self._fill_filter_entries(filter_menu, live_filters, self._standalone_filter_action())
        fill_menu = QMenu(_ts("Fill Layer"), menu)
        menu.addMenu(fill_menu)
        for kind in self._registry.kinds("fill"):
            action = fill_menu.addAction(_ts(kind.title))
            action.triggered.connect(
                lambda _checked=False, kind_id=kind.kind_id: self.add_fill_layer(kind_id)
            )

    def populate_layer_filter_menu(self, menu: QMenu, layer_id: str) -> None:
        """Per-layer ``Add Filter`` submenu: adjustments first, then live filters."""
        filters = self._filters()
        if not filters:
            action = menu.addAction(_ts("No filters available"))
            action.setEnabled(False)
            return
        adjustments = [e for e in filters if e[2] == "adjustment"]
        live_filters = [e for e in filters if e[2] != "adjustment"]
        on_pick = lambda tid: self._layer_filter_action()(layer_id, tid)  # noqa: E731
        self._fill_filter_entries(menu, adjustments, on_pick)
        if adjustments and live_filters:
            menu.addSeparator()
        self._fill_filter_entries(menu, live_filters, on_pick)

    def _standalone_filter_action(self) -> Callable[[str], None]:
        return getattr(self._panel, "_add_standalone_filter_layer", None) or (
            self._panel._add_standalone_effect_layer
        )

    def _layer_filter_action(self) -> Callable[[str, str], None]:
        return (
            getattr(self._panel, "_add_filter_to_layer", None) or self._panel._add_effect_to_layer
        )

    def populate_effect_layer_menu(self, menu: QMenu) -> None:
        """Compatibility delegate for the former effect-layer menu."""
        self.populate_filter_layer_menu(menu)

    def populate_layer_effect_menu(self, menu: QMenu, layer_id: str) -> None:
        """Compatibility delegate for the former per-layer effect menu."""
        self.populate_layer_filter_menu(menu, layer_id)

    def add_context_actions(self, menu: QMenu, layer_id: str) -> None:
        service = self.service()
        if service is None:
            return
        menu.addSeparator()
        styles_action = menu.addAction(_ts("Layer Styles..."))
        styles_action.triggered.connect(lambda: self.edit_styles(layer_id))
        if service.fill_for(layer_id) is not None:
            fill_action = menu.addAction(_ts("Edit Fill..."))
            fill_action.triggered.connect(lambda: self.edit_fill(layer_id))
        if not service.styles_for(layer_id).is_empty:
            raster_action = menu.addAction(_ts("Rasterize Appearance"))
            raster_action.triggered.connect(lambda: self.rasterize(layer_id))

    @staticmethod
    def _fill_filter_entries(
        menu: QMenu, filters: list[tuple[str, str, str]], on_pick: Callable[[str], None]
    ) -> None:
        if not filters:
            action = menu.addAction(_ts("No filters available"))
            action.setEnabled(False)
            return
        for type_id, name, _category in filters:
            action = menu.addAction(name)
            action.triggered.connect(lambda _checked=False, tid=type_id: on_pick(tid))

    # -- actions -----------------------------------------------------------------------

    def add_fill_layer(self, kind_id: str) -> str | None:
        service = self.service()
        if service is None:
            return None
        initial = FillSpec("preview", kind_id, self._registry.defaults("fill", kind_id))
        spec = self._fill_dialog(self._registry, initial=initial, parent=self._panel)
        if spec is None:
            return None
        spec = FillSpec(service.new_fill_spec(kind_id, {}).fill_id, spec.kind, dict(spec.params))
        layer_id = service.add_fill_layer(spec)
        self._refresh_panel()
        return layer_id

    def edit_fill(self, layer_id: str) -> bool:
        service = self.service()
        current = service.fill_for(layer_id) if service else None
        if service is None or current is None:
            return False
        spec = self._fill_dialog(self._registry, initial=current, parent=self._panel)
        if spec is None:
            return False
        ok = service.update_fill(layer_id, spec)
        self._refresh_panel()
        return ok

    def edit_styles(self, layer_id: str) -> None:
        service = self.service()
        if service is None:
            return
        stack = service.styles_for(layer_id)
        dialog = self._styles_dialog(
            self._registry,
            stack,
            on_apply=lambda new_stack: self._apply_styles(layer_id, new_stack),
            on_rasterize=lambda: self.rasterize(layer_id),
            parent=self._panel,
        )
        exec_ = getattr(dialog, "exec", None)
        if callable(exec_):
            exec_()
        delete = getattr(dialog, "deleteLater", None)
        if callable(delete):
            delete()

    def _apply_styles(self, layer_id: str, stack: LayerStyleStack) -> bool:
        service = self.service()
        ok = bool(service and service.set_styles(layer_id, stack))
        self._refresh_panel()
        return ok

    def rasterize(self, layer_id: str) -> str | None:
        service = self.service()
        new_id = service.rasterize_appearance(layer_id) if service else None
        self._refresh_panel()
        return new_id

    def _refresh_panel(self) -> None:
        refresh = getattr(self._panel, "_add_default_layers", None)
        if callable(refresh):
            refresh()

    # -- default dialogs ---------------------------------------------------------------

    @staticmethod
    def _default_fill_dialog(
        registry: AppearanceRegistry, *, initial: FillSpec | None, parent: Any
    ):
        from loomstroke.ui.dialogs.fill_layer_dialog import FillLayerDialog  # noqa: PLC0415

        return FillLayerDialog.ask(registry, initial=initial, parent=parent)

    @staticmethod
    def _default_styles_dialog(registry: AppearanceRegistry, stack: LayerStyleStack, **kwargs: Any):
        from loomstroke.ui.dialogs.layer_styles_dialog import LayerStylesDialog  # noqa: PLC0415

        return LayerStylesDialog(registry, stack, **kwargs)
