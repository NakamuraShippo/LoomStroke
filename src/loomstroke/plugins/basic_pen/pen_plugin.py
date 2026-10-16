"""Pen plugin lifecycle and brush-library UI integration."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QWidget

from ...host_api import StrokePoint as HostStrokePoint
from ..sdk import ToolDescriptor
from ..sdk.base import PluginBase
from . import pen_tool as facade
from .pen_settings import PenSettings
from .pen_tool import (
    PLUGIN_METADATA,
    ABRImporter,
    BasicPenTool,
    BrushManagerPanel,
    Path,
    PenPresetController,
    QDockWidget,
    QFileDialog,
    QImage,
    QMessageBox,
    TipCache,
    _DockLayoutWatcher,
    _now_iso,
    _ts,
    invalidate_tip_cache,
    is_valid,
    logger,
    shutil,
    uuid,
)


class BasicPenPlugin(PluginBase):
    """Basic pen tool plugin."""

    def __init__(self) -> None:

        super().__init__(PLUGIN_METADATA)

        self._store = facade.BrushStore()

        self._tip_cache = TipCache()

        self._abr_importer = ABRImporter()

        self._panel: BrushManagerPanel | None = None

        self._pen_presets: PenPresetController | None = None

        self._panel_dock: QDockWidget | None = None

        self._panel_dock_filter: _DockLayoutWatcher | None = None

        self.pen_tool = BasicPenTool()

        self._applying_swatch = False

        self._panel_retry_scheduled = False

        self._panel_retry_attempts = 0

        logger.info("BasicPenPlugin initialized")

    def descriptor(self) -> ToolDescriptor:
        """Return tool descriptor for legacy compatibility."""

        return self.pen_tool.descriptor()

    def activate(self, host_api) -> None:
        """Activate plugin and underlying tool."""

        super().activate(host_api)

        self.pen_tool.activate(host_api)

        register_tool = getattr(host_api, "register_tool", None)

        if callable(register_tool):

            register_tool(self.pen_tool)

        self._store.ensure_dirs()

        self._store.ensure_default_swatch()

        self._import_existing_abrs(force_extract=False)

        self._ensure_panel()

        self._refresh_panel()

        self._apply_initial_swatch()

        # Re-apply user preferences AFTER swatch apply. _apply_initial_swatch
        # above overwrites size/opacity/flow/hardness/spacing/tone_range/
        # tone_gamma with the swatch's params; our persisted tool settings
        # must win so per-user tweaks survive app restart.
        self._reapply_saved_tool_settings()

        if self._pen_presets is not None:
            self._pen_presets.restore_tip()
            self._pen_presets.bind()

    def _reapply_saved_tool_settings(self) -> None:
        """Load persisted tool settings and push them to the UI.

        Mirrors the eraser tool's restore-on-activate pattern, but runs
        after the initial swatch to override swatch defaults. Missing /
        empty / malformed values are silently skipped.
        """

        host = self.host_api

        if host is None:

            return

        settings_mgr = getattr(host, "settings", None)

        getter = getattr(settings_mgr, "get", None) if settings_mgr else None

        if not callable(getter):

            return

        try:

            saved = getter("tools.basic_pen", {})

        except Exception:

            logger.warning(
                "basic_pen: settings load failed on activate; tool will start "
                "with defaults / swatch values",
                exc_info=True,
            )

            return

        if not isinstance(saved, dict) or not saved:

            return

        self.pen_tool._load_settings(saved)

        self._sync_tool_properties(saved)

    def enable(self) -> None:
        """Enable underlying tool (legacy API)."""

        self.pen_tool.enable()

    def disable(self) -> None:
        """Disable underlying tool (legacy API)."""

        self.pen_tool.disable()

    def deactivate(self) -> None:
        """Deactivate plugin and tool."""

        if self._pen_presets is not None:
            self._pen_presets.unbind()

        self.pen_tool.deactivate()

        if self._panel_dock is not None:

            try:

                self._panel_dock.hide()

            except Exception:

                logger.warning(
                    "basic_pen: Brush Manager dock.hide() failed on deactivate; "
                    "panel may remain visible after plugin unload",
                    exc_info=True,
                )

        super().deactivate()

    async def initialize(self, host_api) -> None:
        """Async initialization hook for new SDK path."""

        self.activate(host_api)

        logger.info("BasicPenPlugin initialized and tool registered")

    async def cleanup(self) -> None:
        """Async cleanup hook for new SDK path."""

        if self.host_api and hasattr(self.host_api, "unregister_tool"):

            self.host_api.unregister_tool(self.pen_tool.descriptor().id)

        self.deactivate()

        logger.info("BasicPenPlugin cleaned up")

    def _ensure_panel(self) -> None:  # noqa: PLR0912, PLR0915

        if self._panel_dock is not None:

            if not is_valid(self._panel_dock):

                self._panel_dock = None

            else:

                try:

                    self._panel_dock.show()

                    self._panel_dock.raise_()

                except Exception:

                    logger.warning(
                        "basic_pen: Brush Manager dock.show/raise failed on "
                        "re-activate; panel may not appear",
                        exc_info=True,
                    )

                return

        if self._panel is not None and not is_valid(self._panel):

            self._panel = None

        if self._panel is None:

            self._panel = BrushManagerPanel(self._tip_cache)

            self._panel.swatchSelected.connect(self._on_swatch_selected)

            self._panel.requestCreateTipFromSelection.connect(self._on_create_tip_from_selection)

            self._panel.requestSaveParamsToSelected.connect(self._on_save_params_to_swatch)

            self._panel.requestImportABR.connect(self._on_import_abr)

            self._panel.requestRescanABR.connect(self._on_rescan_abrs)

            self._panel.requestImportImages.connect(self._on_import_tip_images)

            self._panel.requestDeleteSelected.connect(self._on_delete_selected_swatch)

            self._panel.swatchRenamed.connect(self._on_rename_swatch)

            self._pen_presets = PenPresetController(self, self._panel.pen_panel)

        host = self.host_api

        if not host:

            return

        ui = getattr(host, "ui", None)

        window_getter = getattr(ui, "get_main_window", None) if ui else None

        window = window_getter() if callable(window_getter) else None

        if window is None or not hasattr(window, "addDockWidget"):

            self._schedule_panel_retry()

            return

        dock = QDockWidget(_ts("Brush Manager"), window)

        dock.setObjectName("ui.brush_manager")

        dock.setWidget(self._panel)

        self._panel_dock = dock

        if self._panel_dock_filter is None:

            self._panel_dock_filter = _DockLayoutWatcher(self._panel)

        else:

            self._panel_dock_filter.set_panel(self._panel)

        if not dock.property("_brush_manager_layout_watch"):

            dock.setProperty("_brush_manager_layout_watch", True)

            dock.installEventFilter(self._panel_dock_filter)

            dock.topLevelChanged.connect(self._panel_dock_filter.on_dock_changed)

            dock.dockLocationChanged.connect(self._panel_dock_filter.on_dock_changed)

        try:

            from PySide6.QtCore import Qt  # noqa: PLC0415

            window.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, dock)

        except Exception:

            try:

                window.addDockWidget(2, dock)  # type: ignore[arg-type]  # Qt enum int fallback

            except Exception:

                logger.warning(
                    "basic_pen: window.addDockWidget failed for Brush Manager; "
                    "panel will not be visible this session",
                    exc_info=True,
                )

        try:

            dock.show()

            dock.raise_()

        except Exception:

            logger.warning(
                "basic_pen: Brush Manager dock.show/raise failed after creation; "
                "panel may not appear",
                exc_info=True,
            )

        payload = {
            "id": "ui.brush_manager",
            "title": _ts("Brush Manager"),
            "title_context": "BasicPen",
            "dock": dock,
            "default_visible": True,
        }

        register_panel = getattr(window, "register_panel", None)

        if callable(register_panel):

            try:

                register_panel(
                    payload["id"],
                    payload["title"],
                    dock,
                    default_visible=payload["default_visible"],
                    title_context=payload.get("title_context"),
                )

            except Exception:

                logger.warning(
                    "basic_pen: Brush Manager register_panel failed; panel may "
                    "not appear in the Window menu",
                    exc_info=True,
                )

        events = getattr(host, "events", None)

        publish = getattr(events, "publish", None) if events else None

        emit = getattr(events, "emit", None) if events else None

        if callable(publish):

            publish("ui.panel.register", payload)

        elif callable(emit):

            emit("ui.panel.register", payload)

    def _schedule_panel_retry(self) -> None:

        if self._panel_retry_scheduled:

            return

        if self._panel_retry_attempts >= 30:

            logger.warning(
                "basic_pen: Brush Manager panel retry exhausted after %d attempts; "
                "main window was not available. Panel will not appear this session.",
                self._panel_retry_attempts,
            )

            return

        self._panel_retry_scheduled = True

        try:

            from PySide6.QtCore import QTimer  # noqa: PLC0415

        except Exception:

            return

        def _retry() -> None:

            self._panel_retry_scheduled = False

            self._panel_retry_attempts += 1

            self._ensure_panel()

            if self._panel_dock is None:

                self._schedule_panel_retry()

        QTimer.singleShot(200, _retry)

    def _refresh_panel(self) -> None:

        if not self._panel:

            return

        swatches = self._store.list_swatches()

        resolved = []

        for swatch in swatches:

            tip_path = swatch.get("tip_path")

            abs_tip = self._store.resolve_tip_path(tip_path) if isinstance(tip_path, str) else None

            swatch_copy = dict(swatch)

            swatch_copy["_abs_tip_path"] = str(abs_tip) if abs_tip else None

            resolved.append(swatch_copy)

        self._panel.set_swatches(resolved)

    def _apply_initial_swatch(self) -> None:

        settings = self._store.load_settings()

        swatches = self._store.list_swatches()

        if not swatches:

            return

        last_selected = settings.get("last_selected_swatch_id")

        swatch_id = last_selected if isinstance(last_selected, str) else swatches[0].get("id")

        if isinstance(swatch_id, str):

            self._apply_swatch(swatch_id, update_panel=True)

    def _apply_swatch(self, swatch_id: str, *, update_panel: bool) -> None:

        swatch = self._store.get_swatch(swatch_id)

        if not swatch:

            return

        params = swatch.get("params", {})

        tip_path = swatch.get("tip_path")

        abs_tip = self._store.resolve_tip_path(tip_path) if isinstance(tip_path, str) else None

        meta = swatch.get("meta")

        hardness_mode = "radial"

        if abs_tip:

            mode_value = meta.get("hardness_mode") if isinstance(meta, dict) else None

            if isinstance(mode_value, str) and mode_value:

                hardness_mode = mode_value

            else:

                source = meta.get("source") if isinstance(meta, dict) else None

                if source in {"abr", "import"} or swatch_id.startswith(("abr_", "img_")):

                    hardness_mode = "texture"

        self._applying_swatch = True

        try:

            if not isinstance(params, dict):

                params = {}

            self.pen_tool.apply_params(params)

            self.pen_tool.set_tip_path(str(abs_tip) if abs_tip else None)

            self.pen_tool.set_hardness_mode(hardness_mode)

            self._sync_tool_properties(params)

            if update_panel and self._panel is not None:

                self._panel.select_swatch(swatch_id)

        finally:

            self._applying_swatch = False

        settings = self._store.load_settings()

        settings["last_selected_swatch_id"] = swatch_id

        self._store.save_settings(settings)

    def _sync_tool_properties(self, params: dict[str, Any]) -> None:

        if self._pen_presets is not None:
            self._pen_presets.refresh_settings()

        host = self.host_api

        if host is None:

            return

        tool_id = self.pen_tool.descriptor().id

        payload = dict(params)

        if "spacing" in params and "stamp_density" not in params:

            payload["stamp_density"] = self.pen_tool._density_from_spacing(params["spacing"])

        tool_manager = getattr(host, "_tool_manager", None) or getattr(host, "tool_manager", None)

        sync_tool_setting = (
            getattr(tool_manager, "sync_tool_setting", None) if tool_manager else None
        )

        if callable(sync_tool_setting):

            for key, value in payload.items():

                try:

                    sync_tool_setting(tool_id, key, value)

                except Exception:

                    logger.warning(
                        "basic_pen: Brush Manager sync_tool_setting failed on "
                        "swatch apply; UI sliders may not match swatch values",
                        exc_info=True,
                    )

            return

        events = getattr(host, "events", None)

        publish = getattr(events, "publish", None) if events else None

        if not callable(publish):

            return

        for key, value in payload.items():

            try:

                publish(
                    "tool.settings_changed",
                    {
                        "tool_id": tool_id,
                        "key": key,
                        "value": value,
                        "source": "brush_manager",
                        "sync_only": True,
                    },
                )

            except Exception:

                logger.warning(
                    "basic_pen: Brush Manager events.publish(tool.settings_changed) "
                    "failed; UI sliders may not match swatch values",
                    exc_info=True,
                )

    def _on_swatch_selected(self, swatch_id: str) -> None:

        if self._applying_swatch:

            return

        self._apply_swatch(swatch_id, update_panel=False)

        if self._pen_presets is not None:
            self._pen_presets.swatch_selected()

    def _on_save_params_to_swatch(self) -> None:

        if not self._panel:

            return

        swatch_id = self._panel.get_selected_swatch_id()

        if not swatch_id:

            self._notify(_ts("No swatch selected"), level="warning")

            return

        params = self.pen_tool.get_params()

        self._store.update_swatch_params(swatch_id, params)

        self._refresh_panel()

        self._panel.select_swatch(swatch_id)

    def _on_rename_swatch(self, swatch_id: str, label: str) -> None:

        self._store.rename_swatch(swatch_id, label)

    def _on_delete_selected_swatch(self) -> None:

        if not self._panel:

            return

        swatch_id = self._panel.get_selected_swatch_id()

        if not swatch_id:

            return

        self._store.delete_swatch(swatch_id)

        self._refresh_panel()

        swatches = self._store.list_swatches()

        if swatches:

            next_id = swatches[0].get("id")

            if isinstance(next_id, str):

                self._apply_swatch(next_id, update_panel=True)

    def _on_create_tip_from_selection(self) -> None:

        host = self.host_api

        if not host:

            return

        canvas = getattr(host, "canvas", None)

        getter = getattr(canvas, "get_selection_snapshot", None) if canvas else None

        if not callable(getter):

            self._notify(_ts("Selection snapshot is not available"), level="error")

            return

        payload = getter()

        if payload is None:

            self._notify(_ts("No active selection"), level="warning")

            return

        image = getattr(payload, "image", None)

        if image is None or image.isNull():

            self._notify(_ts("Selection snapshot failed"), level="error")

            return

        max_dim = 512

        if image.width() > max_dim or image.height() > max_dim:

            from PySide6.QtCore import Qt  # noqa: PLC0415

            image = image.scaled(
                max_dim,
                max_dim,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )

        swatch_id = f"b_{uuid.uuid4().hex[:8]}"

        filename = f"tip_{swatch_id}.png"

        abs_path = self._store.brush_tips_dir / filename

        image.save(str(abs_path))

        swatch = {
            "id": swatch_id,
            "label": f"{_ts('Tip')} {swatch_id[-4:]}",
            "tip_path": self._store.tip_path_relative(filename),
            "params": self.pen_tool.get_params(),
            "meta": {
                "source": "native",
                "created_at": _now_iso(),
                "hardness_mode": "texture",
            },
        }

        self._store.add_swatch(swatch)

        self._refresh_panel()

        self._apply_swatch(swatch_id, update_panel=True)

    def _on_import_abr(self) -> None:

        host = self.host_api

        if not host:

            return

        file_path, _ = QFileDialog.getOpenFileName(
            None,
            _ts("Import ABR"),
            str(self._store.root_dir),
            _ts("Photoshop Brush (*.abr)"),
        )

        if not file_path:

            return

        dest = self._store.abr_imports_dir / Path(file_path).name

        if not dest.exists():

            try:

                shutil.copy(file_path, dest)

            except Exception as exc:

                self._notify(_ts("Failed to copy ABR: {error}").format(error=exc), level="error")

                return

        created, error = self._import_abr_file(dest, notify_errors=True)

        if not created:

            if error:

                return

            self._notify(_ts("No tips extracted"), level="warning")

            return

        self._refresh_panel()

        self._apply_swatch(created[0], update_panel=True)

    def _on_import_tip_images(self) -> None:

        host = self.host_api

        if not host:

            return

        file_paths, _ = QFileDialog.getOpenFileNames(
            None,
            _ts("Import Tip Images"),
            str(self._store.root_dir),
            _ts(
                "PNG Images (*.png);;Image Files (*.png *.jpg *.jpeg *.bmp *.gif);;All Files (*.*)"
            ),
        )

        if not file_paths:

            return

        created: list[str] = []

        skipped = 0

        max_dim = 512

        for file_path in file_paths:

            image = QImage(file_path)

            if image.isNull():

                skipped += 1

                continue

            if image.width() > max_dim or image.height() > max_dim:

                from PySide6.QtCore import Qt  # noqa: PLC0415

                image = image.scaled(
                    max_dim,
                    max_dim,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )

            swatch_id = f"img_{uuid.uuid4().hex[:8]}"

            filename = f"tip_{swatch_id}.png"

            abs_path = self._store.brush_tips_dir / filename

            try:

                if not image.save(str(abs_path)):

                    skipped += 1

                    continue

            except Exception:

                skipped += 1

                continue

            label = Path(file_path).stem or f"{_ts('Tip')} {swatch_id[-4:]}"

            swatch = {
                "id": swatch_id,
                "label": label,
                "tip_path": self._store.tip_path_relative(filename),
                "params": self.pen_tool.get_params(),
                "meta": {
                    "source": "import",
                    "created_at": _now_iso(),
                    "hardness_mode": "texture",
                },
            }

            try:

                self._store.add_swatch(swatch)

            except Exception:

                skipped += 1

                continue

            created.append(swatch_id)

        if not created:

            message = _ts("No PNG tips imported")

            if skipped:

                message = _ts("No PNG tips imported ({skipped} skipped)").format(skipped=skipped)

            self._notify(message, level="warning")

            return

        self._refresh_panel()

        self._apply_swatch(created[0], update_panel=True)

        if skipped:

            self._notify(
                _ts("Imported {count} PNG tips ({skipped} skipped)").format(
                    count=len(created),
                    skipped=skipped,
                )
            )

        else:

            self._notify(_ts("Imported {count} PNG tips").format(count=len(created)))

    def _notify(self, message: str, *, level: str = "info") -> None:

        host = self.host_api

        if not host:

            return

        ui = getattr(host, "ui", None)

        if ui and hasattr(ui, "show_message"):

            try:

                ui.show_message(message)

                return

            except Exception:

                logger.warning(
                    "basic_pen: host_api.ui.show_message failed; falling back to "
                    "QMessageBox dialog",
                    exc_info=True,
                )

        # Anchor the dialog to the main window so it inherits modality,
        # stays grouped with the app in the taskbar, and cannot be called
        # from a non-main thread without an explicit parent (Qt enforces
        # same-thread parent). Fall back to None only if unavailable.
        main_window = None
        window_getter = getattr(ui, "get_main_window", None) if ui else None
        if callable(window_getter):
            try:
                main_window = window_getter()
            except Exception:
                logger.warning(
                    "basic_pen: host_api.ui.get_main_window() failed; notification "
                    "dialog will have no parent window",
                    exc_info=True,
                )
                main_window = None

        title = self._ts("Brush Manager")

        if level == "error":

            QMessageBox.critical(main_window, title, message)

        elif level == "warning":

            QMessageBox.warning(main_window, title, message)

        else:

            QMessageBox.information(main_window, title, message)

    @staticmethod
    def _ts(text: str) -> str:
        return _ts(text)

    def _import_existing_abrs(self, *, force_extract: bool) -> tuple[int, list[str]]:

        abr_dir = self._store.abr_imports_dir

        if not abr_dir.exists():

            return 0, []

        created_total = 0

        errors: list[str] = []

        for abr_file in sorted(abr_dir.glob("*.abr")):

            created, error = self._import_abr_file(
                abr_file,
                notify_errors=False,
                force_extract=force_extract,
            )

            created_total += len(created)

            if error:

                errors.append(f"{abr_file.name}: {error}")

        if created_total:

            self._refresh_panel()

        return created_total, errors

    def _on_rescan_abrs(self) -> None:

        created_total, errors = self._import_existing_abrs(force_extract=True)

        if errors:

            summary = "\n".join(errors[:5])

            if len(errors) > 5:

                summary += "\n" + self._ts("...and {more_count} more").format(
                    more_count=len(errors) - 5
                )

            self._notify(
                f"{self._ts('ABR import errors:')}\n{summary}",
                level="warning",
            )

            return

        if created_total:

            self._notify(self._ts("Imported {count} ABR tips").format(count=created_total))

        else:

            self._notify(self._ts("No new ABR tips found"))

    def _import_abr_file(
        self,
        abr_path: Path,
        *,
        notify_errors: bool,
        force_extract: bool = False,
    ) -> tuple[list[str], str | None]:

        existing_ids = {
            swatch.get("id")
            for swatch in self._store.list_swatches()
            if isinstance(swatch.get("id"), str)
        }

        out_dir = self._store.abr_cache_dir / abr_path.stem / "tips"

        tips, error = self._load_abr_tips(
            abr_path,
            out_dir,
            notify_errors=notify_errors,
            force_extract=force_extract,
        )

        if not tips:

            return [], error

        created: list[str] = []

        for idx, tip in enumerate(tips):

            swatch_id = f"abr_{abr_path.stem}_{idx}"

            filename = f"tip_{swatch_id}.png"

            target = self._store.brush_tips_dir / filename

            if force_extract or not target.exists():

                try:

                    shutil.copy(tip, target)

                    invalidate_tip_cache(str(target))

                    self._tip_cache.invalidate(str(target))

                except Exception:

                    continue

            if swatch_id in existing_ids:

                continue

            swatch = {
                "id": swatch_id,
                "label": f"{_ts('ABR')} {abr_path.stem} {idx}",
                "tip_path": self._store.tip_path_relative(filename),
                "params": {
                    "size": 12.0,
                    "opacity": 1.0,
                    "flow": 1.0,
                    "hardness": 0.8,
                    "spacing": 0.12,
                    "tone_range": 1.0,
                    "tone_gamma": 0.6,
                    "color_mode": "fg",
                },
                "meta": {
                    "source": "abr",
                    "abr_origin": abr_path.name,
                    "created_at": _now_iso(),
                    "hardness_mode": "texture",
                },
            }

            try:

                self._store.add_swatch(swatch)

            except Exception:

                continue

            created.append(swatch_id)

            existing_ids.add(swatch_id)

        return created, None

    def _load_abr_tips(
        self,
        abr_path: Path,
        out_dir: Path,
        *,
        notify_errors: bool,
        force_extract: bool,
    ) -> tuple[list[str], str | None]:

        if not force_extract:

            cached = self._cached_tips(out_dir)

            if cached:

                return cached, None

        else:

            self._clear_cached_tips(out_dir)

        try:

            return self._abr_importer.extract_tips(str(abr_path), str(out_dir)), None

        except Exception as exc:

            if notify_errors:

                self._notify(str(exc), level="error")

            else:

                logger.warning("ABR import failed for %s: %s", abr_path, exc)

            return [], str(exc)

    @staticmethod
    def _cached_tips(out_dir: Path) -> list[str]:

        if not out_dir.exists():

            return []

        tips = sorted(out_dir.glob("*.png"))

        return [str(path) for path in tips]

    @staticmethod
    def _clear_cached_tips(out_dir: Path) -> None:

        if not out_dir.exists():

            return

        for path in out_dir.glob("*.png"):

            try:

                path.unlink()

            except Exception:

                continue

    def get_tools(self) -> list[BasicPenTool]:
        """Return tools exposed by this plugin (new SDK)."""

        return [self.pen_tool]

    # --- Legacy compatibility surface ---

    @property
    def settings(self) -> PenSettings:

        return self.pen_tool.settings

    @settings.setter
    def settings(self, value: PenSettings) -> None:

        self.pen_tool.settings = value

    @property
    def stroke_points(self) -> list[HostStrokePoint]:

        return self.pen_tool.stroke_points

    @stroke_points.setter
    def stroke_points(self, points: list[HostStrokePoint]) -> None:

        self.pen_tool.stroke_points = list(points)

    def get_settings(self) -> dict[str, Any]:

        return self.pen_tool.get_settings()

    def set_settings(self, settings: dict[str, Any]) -> None:

        self.pen_tool.set_settings(settings)

    def on_pointer_down(
        self,
        pt: HostStrokePoint,
        *,
        alt: bool = False,
        shift: bool = False,
        ctrl: bool = False,
        meta: bool = False,
    ) -> None:

        self.pen_tool.on_pointer_down(pt, alt=alt, shift=shift, ctrl=ctrl, meta=meta)

    def on_pointer_move(self, points: list[HostStrokePoint]) -> None:

        self.pen_tool.on_pointer_move(points)

    def on_pointer_up(self, pt: HostStrokePoint) -> None:

        self.pen_tool.on_pointer_up(pt)

    def on_key(self, key: str, pressed: bool) -> bool:

        return self.pen_tool.on_key(key, pressed)

    def build_commands(self):

        return self.pen_tool.build_commands()

    def create_options_widget(self) -> QWidget | None:
        # BasicPenTool does not implement this surface; options are provided
        # via the Brush Manager dock panel. Returning None matches the SDK
        # base behavior instead of raising AttributeError.
        return None
