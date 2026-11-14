"""Publish an extracted ABR tip without truncating the registered PNG."""

from __future__ import annotations

import shutil
from io import BytesIO
from pathlib import Path

from PIL import Image

from loomstroke.io.atomic_export_writer import write_atomically


def publish_abr_tip(source: Path, destination: Path) -> None:
    expected = source.read_bytes()
    with Image.open(BytesIO(expected)) as image:
        if image.format != "PNG":
            raise ValueError("Extracted ABR tip is not a PNG")
        image.load()

    # Retry can reuse a verified tip left behind by a failed registration.
    if destination.is_file() and destination.read_bytes() == expected:
        return

    def copy_tip(temporary: Path) -> None:
        shutil.copyfile(source, temporary)
        if temporary.read_bytes() != expected:
            raise OSError("ABR tip copy differs from its extracted PNG")

    write_atomically(destination, copy_tip)
