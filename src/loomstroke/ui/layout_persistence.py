"""Utilities for persisting and restoring UI layouts as JSON."""

from __future__ import annotations

import base64
import json
import logging
import re
from collections.abc import Callable
from importlib.resources import files
from pathlib import Path
from typing import Any, Protocol

from ..core.layout_serialization import payload_to_snapshot, snapshot_to_payload
from ..host_api import PanelLayout, UILayoutService, UILayoutSnapshot
from ..platform.user_dirs import default_layout_directory, ensure_directory
from .panel_ids import canonical_panel_id

logger = logging.getLogger(__name__)

_SAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")
_INITIAL_LAYOUT_RESOURCE = "resources/layouts/initial_layout.json"
_INITIAL_LAYOUT_AREAS = frozenset({"left", "right", "top", "bottom"})
_INITIAL_LAYOUT_TAB_GROUP_KEY = "layout_tab_group"
_INITIAL_LAYOUT_TAB_ORDER_KEY = "layout_tab_order"
_TRANSIENT_DOCK_SPACER_MARKERS = (
    b"sr_spacer",
    "sr_spacer".encode("utf-16-be"),
    "sr_spacer".encode("utf-16-le"),
)


class SettingsAccessor(Protocol):
    """Protocol capturing the minimal settings API used by layout persistence."""

    def get(self, key: str, default: Any | None = None) -> Any: ...


class LayoutPersistence:
    """Persist UILayout snapshots to JSON files under a configurable directory."""

    def __init__(
        self,
        layout_service_getter: Callable[[], UILayoutService | None],
        settings: SettingsAccessor,
        *,
        default_name: str = "default",
        namespace: str | None = None,
    ) -> None:
        self._layout_service_getter = layout_service_getter
        self._settings = settings
        self._default_name = default_name
        self._namespace = namespace.strip().strip("/\\") if namespace else None
        self._logger = logging.getLogger(self.__class__.__name__)

    # ------------------------------------------------------------------
    def save_current_layout(self, *, name: str | None = None) -> Path | None:
        """Capture the current layout and persist it to disk."""

        service = self._layout_service_getter()
        if service is None:
            self._logger.debug("Layout service unavailable; skipping layout save")
            return None
        try:
            snapshot = service.snapshot(include_panels=True)
        except Exception as exc:
            self._logger.warning("Failed to snapshot UI layout: %s", exc)
            return None

        payload = snapshot_to_payload(snapshot)
        path = self.layout_path(name=name)
        try:
            path.write_text(
                json.dumps(payload, indent=2, sort_keys=True),
                encoding="utf-8",
            )
        except OSError as exc:
            self._logger.error("Failed to write layout file %s: %s", path, exc)
            return None
        return path

    def load_layout(
        self,
        *,
        name: str | None = None,
        apply: bool = False,
        clear_missing: bool = False,
    ) -> UILayoutSnapshot | None:
        """Load a layout snapshot from disk, optionally applying it immediately."""

        path = self.layout_path(name=name)
        if not path.exists():
            self._logger.debug("Layout file %s not found", path)
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            self._logger.error("Failed to read layout file %s: %s", path, exc)
            return None

        snapshot = payload_to_snapshot(payload)
        if self._should_inherit_initial_layout_tabs(name):
            snapshot = self._with_initial_layout_tab_metadata(snapshot)
        if apply:
            service = self._layout_service_getter()
            if service is None:
                self._logger.debug("Layout service unavailable; skipping apply")
            else:
                try:
                    service.apply(
                        snapshot,
                        allow_restore_state=True,
                        clear_missing=clear_missing,
                    )
                except Exception as exc:
                    self._logger.warning("Failed to apply persisted layout: %s", exc)
        return snapshot

    def layout_path(self, *, name: str | None = None) -> Path:
        """Return the final JSON path for the given layout name."""

        directory = self._resolve_layout_directory()
        slug = self._slug(name or self._default_name)
        return directory / f"{slug}.json"

    def layout_exists(self, *, name: str | None = None) -> bool:
        """Return whether a saved layout file already exists."""

        try:
            return self.layout_path(name=name).exists()
        except OSError as exc:
            self._logger.warning("Failed to check layout file existence: %s", exc)
            return False

    # ------------------------------------------------------------------
    def _resolve_layout_directory(self) -> Path:
        raw_directory = self._settings.get("io/layout_directory", "")
        candidate = raw_directory.strip() if isinstance(raw_directory, str) else ""
        if candidate:
            try:
                base = ensure_directory(candidate)
            except OSError as exc:
                self._logger.warning(
                    "Configured layout directory is unavailable; using default: %s (%s)",
                    candidate,
                    exc,
                )
                base = ensure_directory(default_layout_directory())
        else:
            base = ensure_directory(default_layout_directory())
        if self._namespace:
            return ensure_directory(base / self._namespace)
        return base

    def _slug(self, name: str) -> str:
        candidate = _SAFE_NAME.sub("_", name).strip("._")
        return candidate or "default"

    def _should_inherit_initial_layout_tabs(self, name: str | None) -> bool:
        return self._namespace is None and (name is None or name == self._default_name)

    def _with_initial_layout_tab_metadata(
        self,
        snapshot: UILayoutSnapshot,
    ) -> UILayoutSnapshot:
        initial = load_bundled_initial_layout()
        if initial is None:
            return snapshot

        tab_panels = {
            canonical_panel_id(panel.panel_id): panel
            for panel in initial.panels
            if _has_layout_tab_group(panel)
        }
        eligible = _eligible_initial_tab_panel_ids(snapshot, tab_panels)
        if not eligible:
            return snapshot

        changed = False
        panels: list[PanelLayout] = []
        for panel in snapshot.panels:
            panel_id = canonical_panel_id(panel.panel_id)
            reference = tab_panels.get(panel_id)
            if reference is None or panel_id not in eligible:
                panels.append(panel)
                continue

            metadata = dict(panel.metadata or {})
            reference_metadata = reference.metadata or {}
            for key in (_INITIAL_LAYOUT_TAB_GROUP_KEY, _INITIAL_LAYOUT_TAB_ORDER_KEY):
                if key not in metadata and key in reference_metadata:
                    metadata[key] = reference_metadata[key]
                    changed = True
            panels.append(
                PanelLayout(
                    panel_id=panel.panel_id,
                    geometry=panel.geometry,
                    dock_area=panel.dock_area,
                    floating=panel.floating,
                    visible=reference.visible if reference.visible else panel.visible,
                    z_index=panel.z_index,
                    metadata=metadata,
                )
            )

        if not changed:
            return snapshot
        return UILayoutSnapshot(
            window_state=snapshot.window_state,
            window_geometry=snapshot.window_geometry,
            panels=tuple(panels),
        )


def load_bundled_initial_layout() -> UILayoutSnapshot | None:
    """Load the first-run panel layout bundled with the application."""

    try:
        resource = files("loomstroke.ui").joinpath(_INITIAL_LAYOUT_RESOURCE)
        payload = json.loads(resource.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, TypeError, json.JSONDecodeError) as exc:
        logger.warning("Failed to load bundled initial layout: %s", exc)
        return None
    return initial_layout_payload_to_snapshot(payload)


def initial_layout_payload_to_snapshot(payload: Any) -> UILayoutSnapshot | None:
    """Convert a bundled initial-layout payload to a first-run snapshot."""

    if not isinstance(payload, dict):
        logger.warning("Bundled initial layout ignored: root payload is not an object")
        return None

    raw_snapshot = payload_to_snapshot(payload)
    panels: list[PanelLayout] = []
    for panel in raw_snapshot.panels:
        panel_id = canonical_panel_id(panel.panel_id.strip())
        if not panel_id:
            continue
        dock_area = _initial_layout_area(panel.dock_area)
        panels.append(
            PanelLayout(
                panel_id=panel_id,
                geometry=_initial_layout_geometry(panel.geometry),
                dock_area=dock_area,
                floating=panel.floating,
                visible=panel.visible,
                z_index=panel.z_index,
                metadata=dict(panel.metadata) if isinstance(panel.metadata, dict) else {},
            )
        )

    if not panels and not (raw_snapshot.window_state or raw_snapshot.window_geometry):
        logger.warning("Bundled initial layout contains no usable panels")
        return None
    return UILayoutSnapshot(
        window_state=_initial_layout_window_state(raw_snapshot.window_state),
        window_geometry=raw_snapshot.window_geometry,
        panels=tuple(panels),
    )


def _initial_layout_area(raw: str | None) -> str | None:
    if not isinstance(raw, str):
        return None
    area = raw.strip().lower()
    return area if area in _INITIAL_LAYOUT_AREAS else None


def _initial_layout_geometry(raw: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    x, y, width, height = raw
    return x, y, max(1, width), max(1, height)


def _initial_layout_window_state(raw_state: Any) -> str | None:
    if not isinstance(raw_state, str) or not raw_state:
        return None
    try:
        decoded = base64.b64decode(raw_state.encode("ascii"), validate=True)
    except (ValueError, TypeError):
        return raw_state
    if any(marker in decoded for marker in _TRANSIENT_DOCK_SPACER_MARKERS):
        logger.warning(
            "Bundled initial layout window_state contains transient dock spacers; "
            "attempting Qt state restore before panel metadata fallback"
        )
    return raw_state


def _has_layout_tab_group(panel: PanelLayout) -> bool:
    return _has_layout_tab_group_metadata(panel.metadata)


def _has_layout_tab_group_metadata(raw_metadata: Any) -> bool:
    if not isinstance(raw_metadata, dict):
        return False
    metadata = raw_metadata or {}
    group = metadata.get(_INITIAL_LAYOUT_TAB_GROUP_KEY)
    return isinstance(group, str) and bool(group.strip())


def _eligible_initial_tab_panel_ids(
    snapshot: UILayoutSnapshot,
    tab_panels: dict[str, PanelLayout],
) -> set[str]:
    snapshot_panels = {canonical_panel_id(panel.panel_id): panel for panel in snapshot.panels}
    groups: dict[str, list[tuple[str, PanelLayout]]] = {}
    for panel_id, panel in tab_panels.items():
        metadata = panel.metadata or {}
        group = metadata.get(_INITIAL_LAYOUT_TAB_GROUP_KEY)
        if isinstance(group, str) and group.strip():
            groups.setdefault(group.strip(), []).append((panel_id, panel))

    eligible: set[str] = set()
    for panels in groups.values():
        if all(
            _saved_panel_matches_initial_tab_area(snapshot_panels.get(panel_id), panel)
            for panel_id, panel in panels
        ):
            eligible.update(panel_id for panel_id, _panel in panels)
    return eligible


def _saved_panel_matches_initial_tab_area(
    saved: PanelLayout | None,
    initial: PanelLayout,
) -> bool:
    if saved is None or saved.floating:
        return False
    return _normalise_area(saved.dock_area) == _normalise_area(initial.dock_area)


def _normalise_area(area: str | None) -> str | None:
    return area.strip().lower() if isinstance(area, str) else None


__all__ = [
    "LayoutPersistence",
    "initial_layout_payload_to_snapshot",
    "load_bundled_initial_layout",
]
