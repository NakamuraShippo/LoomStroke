from __future__ import annotations

import json
import logging
import uuid
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter, QRadialGradient

from loomstroke.platform.user_dirs import ensure_directory, plugin_data_dir

logger = logging.getLogger(__name__)

_BUILTIN_PRESETS: list[dict[str, Any]] = [
    {
        "label": "Round",
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
    },
    {
        "label": "Sketch",
        "params": {
            "size": 6.0,
            "opacity": 0.7,
            "flow": 0.8,
            "hardness": 0.45,
            "spacing": 0.2,
            "tone_range": 1.0,
            "tone_gamma": 0.6,
            "color_mode": "fg",
            "smoothing": 0.1,
        },
    },
    {
        "label": "Inking",
        "params": {
            "size": 12.0,
            "opacity": 1.0,
            "flow": 1.0,
            "hardness": 0.9,
            "spacing": 0.05,
            "tone_range": 1.0,
            "tone_gamma": 0.6,
            "color_mode": "fg",
            "smoothing": 0.35,
        },
    },
    {
        "label": "Marker",
        "params": {
            "size": 20.0,
            "opacity": 0.8,
            "flow": 0.6,
            "hardness": 0.25,
            "spacing": 0.12,
            "tone_range": 1.0,
            "tone_gamma": 0.6,
            "color_mode": "fg",
            "smoothing": 0.05,
        },
    },
]


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class BrushStore:
    """Persist brush swatches and panel settings under user_data/plugins/basic_pen."""

    def __init__(self, root_dir: Path | None = None) -> None:
        self.root_dir = Path(root_dir) if root_dir else plugin_data_dir("basic_pen")
        self.brush_manager_dir = self.root_dir / "brush_manager"
        self.brush_tips_dir = self.root_dir / "brush_tips"
        self.abr_imports_dir = self.root_dir / "abr_imports"
        self.abr_cache_dir = self.root_dir / "abr_cache"
        self.db_path = self.brush_manager_dir / "brushes.json"
        self.settings_path = self.brush_manager_dir / "settings.json"
        self._db: dict[str, Any] | None = None
        self._settings: dict[str, Any] | None = None

    # --- Directory management -------------------------------------------------
    def ensure_dirs(self) -> None:
        ensure_directory(self.root_dir)
        ensure_directory(self.brush_manager_dir)
        ensure_directory(self.brush_tips_dir)
        ensure_directory(self.abr_imports_dir)
        ensure_directory(self.abr_cache_dir)

    # --- DB helpers -----------------------------------------------------------
    def _default_db(self) -> dict[str, Any]:
        return {"schema_version": 1, "swatches": []}

    def _default_settings(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "last_selected_swatch_id": None,
            "ui": {"grid_icon_size": 64, "show_labels": True},
        }

    def load_db(self) -> dict[str, Any]:
        if self._db is not None:
            return self._db
        self.ensure_dirs()
        if not self.db_path.exists():
            self._db = self._default_db()
            self.save_db(self._db)
            return self._db
        try:
            self._db = json.loads(self.db_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            backup = self.db_path.with_suffix(f".broken.{int(datetime.now().timestamp())}.json")
            try:
                self.db_path.replace(backup)
            except OSError:
                logger.warning("Failed to backup corrupt brush DB: %s", self.db_path)
            logger.warning("Brush DB corrupted, reset to defaults: %s", exc)
            self._db = self._default_db()
            self.save_db(self._db)
        return self._db

    def save_db(self, db: dict[str, Any]) -> None:
        self.ensure_dirs()
        self.db_path.write_text(json.dumps(db, indent=2, sort_keys=True), encoding="utf-8")

    def load_settings(self) -> dict[str, Any]:
        if self._settings is not None:
            return self._settings
        self.ensure_dirs()
        if not self.settings_path.exists():
            self._settings = self._default_settings()
            self.save_settings(self._settings)
            return self._settings
        try:
            self._settings = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            backup = self.settings_path.with_suffix(
                f".broken.{int(datetime.now().timestamp())}.json"
            )
            try:
                self.settings_path.replace(backup)
            except OSError:
                logger.warning("Failed to backup corrupt brush settings: %s", self.settings_path)
            logger.warning("Brush settings corrupted, reset to defaults: %s", exc)
            self._settings = self._default_settings()
            self.save_settings(self._settings)
        return self._settings

    def save_settings(self, settings: dict[str, Any]) -> None:
        self.ensure_dirs()
        self.settings_path.write_text(
            json.dumps(settings, indent=2, sort_keys=True), encoding="utf-8"
        )

    # --- Swatch CRUD ----------------------------------------------------------
    def list_swatches(self) -> list[dict[str, Any]]:
        db = self.load_db()
        swatches = db.get("swatches", [])
        if not isinstance(swatches, list):
            return []
        return deepcopy(swatches)

    def get_swatch(self, swatch_id: str) -> dict[str, Any] | None:
        for swatch in self.list_swatches():
            if swatch.get("id") == swatch_id:
                return swatch
        return None

    def add_swatch(self, swatch: dict[str, Any]) -> None:
        db = self.load_db()
        swatches = db.setdefault("swatches", [])
        if any(item.get("id") == swatch.get("id") for item in swatches):
            raise ValueError(f"Duplicate swatch id: {swatch.get('id')}")
        swatches.append(deepcopy(swatch))
        self.save_db(db)

    def update_swatch_params(self, swatch_id: str, params: dict[str, Any]) -> None:
        db = self.load_db()
        swatches = db.get("swatches", [])
        for swatch in swatches:
            if swatch.get("id") == swatch_id:
                swatch["params"] = deepcopy(params)
                self.save_db(db)
                return

    def rename_swatch(self, swatch_id: str, label: str) -> None:
        db = self.load_db()
        swatches = db.get("swatches", [])
        for swatch in swatches:
            if swatch.get("id") == swatch_id:
                swatch["label"] = str(label)
                self.save_db(db)
                return

    def delete_swatch(self, swatch_id: str) -> None:
        db = self.load_db()
        swatches = db.get("swatches", [])
        new_swatches = [swatch for swatch in swatches if swatch.get("id") != swatch_id]
        if len(new_swatches) != len(swatches):
            db["swatches"] = new_swatches
            self.save_db(db)

    # --- Paths ----------------------------------------------------------------
    def resolve_tip_path(self, tip_path: str | None) -> Path | None:
        if not tip_path:
            return None
        return (self.brush_manager_dir / tip_path).resolve()

    def tip_path_relative(self, filename: str) -> str:
        return str(Path("..") / "brush_tips" / filename)

    # --- Defaults -------------------------------------------------------------
    def ensure_default_swatch(self) -> str:
        db = self.load_db()
        swatches = db.get("swatches", [])
        if isinstance(swatches, list) and swatches:
            return str(swatches[0].get("id"))

        tip_path = self._ensure_round_tip()
        now = _now_iso()
        first_id: str | None = None

        for preset in _BUILTIN_PRESETS:
            swatch_id = f"b_{uuid.uuid4().hex[:8]}"
            if first_id is None:
                first_id = swatch_id
            swatch = {
                "id": swatch_id,
                "label": preset["label"],
                "tip_path": tip_path,
                "params": {**preset["params"]},
                "meta": {
                    "source": "native",
                    "tags": ["default", "builtin"],
                    "created_at": now,
                },
            }
            self.add_swatch(swatch)

        return first_id or ""

    def _ensure_round_tip(self) -> str:
        filename = "round.png"
        abs_path = self.brush_tips_dir / filename
        if abs_path.exists():
            return self.tip_path_relative(filename)

        self.ensure_dirs()
        size = 256
        image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        gradient = QRadialGradient(size / 2, size / 2, size / 2)
        gradient.setColorAt(0.0, QColor(255, 255, 255, 255))
        gradient.setColorAt(1.0, QColor(255, 255, 255, 0))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        painter.drawEllipse(0, 0, size, size)
        painter.end()
        image.save(str(abs_path))
        return self.tip_path_relative(filename)
