"""Image Package + Manifest writer (CT-26 spec §Image Package + Manifest).

A directory (``destination``) holding the images under their package
paths plus ``publication_manifest.json``.  Each file lands atomically;
this format claims neither CBZ nor EPUB conformance.
"""

from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path

from loomstroke.io.atomic_export_writer import write_atomically

from .digital_package_request import DigitalComicPackageRequest
from .publication_item import PublicationItem
from .publication_manifest_writer import PublicationManifestWriter

__all__ = ["ImageManifestPackageWriter"]

ImageSource = Callable[[PublicationItem], Path]


class ImageManifestPackageWriter:
    def __init__(self, *, manifest: PublicationManifestWriter | None = None) -> None:
        self._manifest = manifest or PublicationManifestWriter()

    def write(self, request: DigitalComicPackageRequest, image_source: ImageSource) -> int:
        """Return the total bytes written (images + manifest)."""
        root = Path(request.destination)
        root.mkdir(parents=True, exist_ok=True)
        total = 0
        for item in request.items:
            target = root / Path(*item.package_path.split("/"))
            source = image_source(item)
            total += write_atomically(target, lambda tmp, src=source: shutil.copyfile(src, tmp))
        payload = self._manifest.json_bytes(request)
        total += write_atomically(
            root / PublicationManifestWriter.FILE_NAME, lambda tmp: tmp.write_bytes(payload)
        )
        return total
