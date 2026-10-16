"""PNG, Photoshop ABR and selection snapshots into an filter-pen tip library."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from loomstroke.plugins.basic_pen.abr_extraction_budget import ABRExtractionBudget
from loomstroke.plugins.basic_pen.abr_importer import ABRImporter, ABRParseError

from .tip_library import TipEntry, TipLibrary

MAX_TIP_DIMENSION = 512


def prepare_tip(image: QImage) -> QImage:
    if image.isNull():
        raise ValueError("Brush tip image is empty")
    if max(image.width(), image.height()) > MAX_TIP_DIMENSION:
        return image.scaled(
            MAX_TIP_DIMENSION,
            MAX_TIP_DIMENSION,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    return image.copy()


def import_png(library: TipLibrary, path: str | Path) -> TipEntry:
    source = Path(path)
    return library.add_image(prepare_tip(QImage(str(source))), source.stem, source="png")


def import_abr(library: TipLibrary, path: str | Path) -> list[TipEntry]:
    source = Path(path)
    library.root_dir.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="abr_", dir=library.root_dir) as extract_dir:
        paths = ABRImporter().extract_tips(str(source), extract_dir, budget=ABRExtractionBudget())
        if not paths:
            raise ABRParseError("No tips were found in this ABR file")
        images = [prepare_tip(QImage(tip_path)) for tip_path in paths]
        return library.add_images(
            [(image, f"{source.stem} {index + 1}", "abr") for index, image in enumerate(images)]
        )


def import_selection(library: TipLibrary, canvas: Any, label: str) -> TipEntry:
    getter = getattr(canvas, "get_selection_snapshot", None)
    if not callable(getter):
        raise ValueError("Selection snapshot is not available")
    snapshot = getter()
    image = getattr(snapshot, "image", None)
    if not isinstance(image, QImage) or image.isNull():
        raise ValueError("No active selection")
    return library.add_image(prepare_tip(image), label, source="selection")
