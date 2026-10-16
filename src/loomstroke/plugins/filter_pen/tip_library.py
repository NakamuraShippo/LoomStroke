"""Filter-pen-owned brush-tip storage, independent of Basic Pen swatches."""

from __future__ import annotations

import json
import threading
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from PySide6.QtGui import QImage

from loomstroke.platform.user_dirs import plugin_data_dir


@dataclass(frozen=True)
class TipEntry:
    id: str
    label: str
    filename: str
    source: str


class TipLibrary:
    def __init__(self, root_dir: Path | None = None) -> None:
        self.root_dir = Path(root_dir) if root_dir is not None else plugin_data_dir("effect_pen")
        self.images_dir = self.root_dir / "brush_tips"
        self.catalog_path = self.root_dir / "tips.json"
        self._lock = threading.RLock()

    def list_tips(self) -> list[TipEntry]:
        if not self.catalog_path.exists():
            return []
        catalog = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        entries = catalog.get("tips") if isinstance(catalog, dict) else None
        if not isinstance(entries, list):
            raise ValueError("Invalid filter pen tip catalog")
        result = []
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("Invalid filter pen tip entry")
            tip = TipEntry(**entry)
            self.path_for(tip)
            result.append(tip)
        return result

    def path_for(self, entry: TipEntry) -> Path:
        if Path(entry.filename).name != entry.filename or not entry.filename.endswith(".png"):
            raise ValueError("Invalid filter pen tip filename")
        return self.images_dir / entry.filename

    def add_image(self, image: QImage, label: str, *, source: str) -> TipEntry:
        return self.add_images([(image, label, source)])[0]

    def add_images(self, images: list[tuple[QImage, str, str]]) -> list[TipEntry]:
        """Commit one import batch, rolling back its files if the catalog cannot be saved."""
        if not images or any(image.isNull() for image, _label, _source in images):
            raise ValueError("Brush tip image is empty")
        with self._lock:
            tips = self.list_tips()
            self.images_dir.mkdir(parents=True, exist_ok=True)
            entries: list[TipEntry] = []
            targets: list[Path] = []
            temporary = self.catalog_path.with_suffix(".json.tmp")
            try:
                for image, label, source in images:
                    tip_id = uuid.uuid4().hex
                    entry = TipEntry(tip_id, label, f"tip_{tip_id}.png", source)
                    target = self.path_for(entry)
                    targets.append(target)
                    if not image.save(str(target), "PNG"):
                        raise OSError("Could not save brush tip image")
                    entries.append(entry)
                content = {"schema_version": 1, "tips": [asdict(tip) for tip in [*tips, *entries]]}
                temporary.write_text(
                    json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                temporary.replace(self.catalog_path)
            except (OSError, ValueError):
                for target in targets:
                    target.unlink(missing_ok=True)
                temporary.unlink(missing_ok=True)
                raise
            return entries
