"""Shared snap helpers for grid and guide snapping.

Usage::

    from ..sdk.snap import snap_position, is_snap_enabled, get_guide_controller

These are standalone functions that accept a ``host_api`` (HostAPI) instance
so that any tool can reuse them without duplicating the lookup logic.
"""

from __future__ import annotations

from typing import Any

from ...core.layout_grid_settings import read_layout_grid
from ...core.layout_grid_snap import nearest_grid_point


def _canvas_widget(host_api: Any) -> Any:
    if host_api is None:
        return None
    ui = getattr(host_api, "ui", None)
    if ui is None:
        return None
    mw = getattr(ui, "get_main_window", lambda: None)()
    if mw is None:
        return None
    return getattr(mw, "canvas", None)


def get_guide_controller(host_api: Any) -> Any:
    """Return the *GuideController* from ``CanvasWidget``, or ``None``."""
    return getattr(_canvas_widget(host_api), "_guide_controller", None)


def is_snap_enabled(
    host_api: Any,
    *,
    grid_snap: bool | None = None,
) -> bool:
    """Return ``True`` when grid snap or guide snap is active."""
    settings = getattr(host_api, "settings", None) if host_api else None
    grid_snap_enabled = bool(settings.get("view/grid_snap", False)) if settings else False
    if grid_snap is not None:
        grid_snap_enabled = bool(grid_snap)
    if grid_snap_enabled:
        return True
    if (
        settings
        and settings.get("view/guides_visible", True)
        and settings.get("view/composition_overlay_kinds", [])
    ):
        return True
    controller = get_guide_controller(host_api)
    return controller is not None and bool(getattr(controller, "guides_visible", True))


def _guide_snap(
    gc: Any,
    x: float,
    y: float,
    w: float,
    h: float,
    sx: float,
    sy: float,
) -> tuple[float, float]:
    """Apply guide snap checking all edges, returning adjusted *(sx, sy)*.

    *sx*/*sy* are the pre-snapped coordinates (e.g. after grid snap).
    Guide snap overrides them when a guide hit is closer.
    """
    snap_fn = getattr(gc, "snap_position", None)
    if not callable(snap_fn):
        return sx, sy
    gx, gy, gx_hit, gy_hit = snap_fn(x, y)
    best_dx: float | None = (gx - x) if gx_hit else None
    best_dy: float | None = (gy - y) if gy_hit else None
    if w > 0 or h > 0:
        gx2, gy2, gx2_hit, gy2_hit = snap_fn(x + w, y + h)
        if gx2_hit:
            dx2 = gx2 - (x + w)
            if best_dx is None or abs(dx2) < abs(best_dx):
                best_dx = dx2
        if gy2_hit:
            dy2 = gy2 - (y + h)
            if best_dy is None or abs(dy2) < abs(best_dy):
                best_dy = dy2
    if best_dx is not None:
        sx = x + best_dx
    if best_dy is not None:
        sy = y + best_dy
    return sx, sy


def snap_position(
    host_api: Any,
    x: float,
    y: float,
    w: float = 0.0,
    h: float = 0.0,
    *,
    grid_snap: bool | None = None,
    grid_size: int | None = None,
) -> tuple[float, float]:
    """Snap *(x, y)* using grid and/or guide snap.

    When *w* / *h* are given (> 0) all four edges are tested against guides
    and the closest match per axis wins.
    ``grid_snap`` / ``grid_size`` can override persisted settings for tools
    that expose local property-bar snap controls.
    """
    sx, sy = x, y

    # --- Grid snap (origin) ---
    settings = getattr(host_api, "settings", None) if host_api else None
    grid_snap_enabled = bool(settings.get("view/grid_snap", False)) if settings else False
    if grid_snap is not None:
        grid_snap_enabled = bool(grid_snap)
    if grid_snap_enabled:
        default_grid_size = settings.get("view/grid_size", 10) if settings else 10
        gs = max(1, int(grid_size if grid_size is not None else default_grid_size))
        origin = (0.0, 0.0)
        controller = get_guide_controller(host_api)
        if (
            controller is not None
            and settings is not None
            and settings.get("view/grid_follow_ruler_origin", False)
        ):
            point = controller.ruler_state().origin
            origin = point.x(), point.y()
        pages = getattr(host_api, "pages", None)
        dpi = (
            pages.get_active_canvas_spec().dpi
            if pages is not None
            and settings is not None
            and settings.get("view/grid_kind") == "baseline"
            else (72.0, 72.0)
        )
        grid = read_layout_grid(settings, size=gs, origin=origin, dpi=dpi)
        sx, sy = nearest_grid_point(grid, (sx, sy))

    # --- Guide snap (all edges, closest wins) ---
    gc = get_guide_controller(host_api)
    if gc is not None:
        sx, sy = _guide_snap(gc, x, y, w, h, sx, sy)

    composition = getattr(_canvas_widget(host_api), "_composition_interaction", None)
    if composition is not None:
        cx, cy, hit = composition.snap_position(x, y, w, h)
        if hit and (
            (sx, sy) == (x, y) or (cx - x) ** 2 + (cy - y) ** 2 < (sx - x) ** 2 + (sy - y) ** 2
        ):
            sx, sy = cx, cy

    return sx, sy
