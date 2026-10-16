"""CBZ writer (CT-26 spec §CBZ).

Images go to ``pages/000001.<ext>`` in logical order (zero-padded, so
lexical order matches), then ``ComicInfo.xml`` and — unless the privacy
option turns it off — ``META-INF/loomstroke-publication.json``.  The
archive is written to a sibling temp file and moved into place
atomically; no executables, scripts or external references are ever
added.
"""

from __future__ import annotations

import zipfile
from collections.abc import Callable
from pathlib import Path

from loomstroke.io.atomic_export_writer import write_atomically

from .comic_info_writer import ComicInfoWriter
from .digital_package_request import DigitalComicPackageRequest
from .publication_item import PublicationItem
from .publication_manifest_writer import PublicationManifestWriter

__all__ = ["CbzPackageWriter"]

ImageSource = Callable[[PublicationItem], Path]


class CbzPackageWriter:
    """Write ``request.destination`` as a CBZ from already encoded images."""

    def __init__(
        self,
        *,
        comic_info: ComicInfoWriter | None = None,
        manifest: PublicationManifestWriter | None = None,
    ) -> None:
        self._comic_info = comic_info or ComicInfoWriter()
        self._manifest = manifest or PublicationManifestWriter()

    def write(self, request: DigitalComicPackageRequest, image_source: ImageSource) -> int:
        """Return the archive size in bytes."""
        comic_info = self._comic_info.xml(request)
        manifest = (
            self._manifest.json_bytes(request) if request.include_loomstroke_manifest else None
        )

        def encode(tmp: Path) -> None:
            with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for item in request.items:
                    # Images are already compressed; store them as-is.
                    zf.write(
                        image_source(item), item.package_path, compress_type=zipfile.ZIP_STORED
                    )
                zf.writestr(ComicInfoWriter.FILE_NAME, comic_info)
                if manifest is not None:
                    zf.writestr(PublicationManifestWriter.CBZ_MEMBER, manifest)

        return write_atomically(Path(request.destination), encode)
