"""Fixed-layout EPUB 3 writer (CT-26 spec §Fixed-Layout EPUB 3).

OCF: ``mimetype`` first and stored, ``META-INF/container.xml``, then
``OEBPS/content.opf`` (``rendition:layout`` = pre-paginated,
``page-progression-direction`` from the binding, spread sides mapped to
``page-spread-*`` properties, cover image marked), ``nav.xhtml`` and one
viewport XHTML wrapper + one image per page.  Image-only: no fonts,
scripts or remote resources.  Alt text only when the user supplied it.
All text is XML-escaped.  EPUB conformance is never *claimed* here — the
EPUBCheck adapter reports ``not_checked`` unless it actually ran.
"""

from __future__ import annotations

import zipfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar
from xml.sax.saxutils import escape, quoteattr

from loomstroke.io.atomic_export_writer import write_atomically

from .digital_package_request import DigitalComicPackageRequest
from .publication_item import PublicationItem

__all__ = ["FixedLayoutEpubWriter"]

ImageSource = Callable[[PublicationItem], Path]


class FixedLayoutEpubWriter:
    """Write ``request.destination`` as an image-only fixed-layout EPUB."""

    ROOT: ClassVar[str] = "OEBPS/"
    IMAGE_DIR: ClassVar[str] = "images"
    PAGE_DIR: ClassVar[str] = "pages"
    SPREAD_PROPERTIES: ClassVar[dict[str, str]] = {
        "left": "page-spread-left",
        "right": "page-spread-right",
        "center": "rendition:page-spread-center",
    }

    def __init__(self, *, clock: Callable[[], str] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))

    # -- paths ---------------------------------------------------------------------

    @classmethod
    def image_member(cls, item: PublicationItem) -> str:
        return f"{cls.ROOT}{cls.IMAGE_DIR}/{item.logical_index + 1:06d}.{item.extension}"

    @classmethod
    def page_member(cls, item: PublicationItem) -> str:
        return f"{cls.ROOT}{cls.PAGE_DIR}/{item.logical_index + 1:06d}.xhtml"

    # -- documents -----------------------------------------------------------------

    @staticmethod
    def container_xml() -> bytes:
        return (
            b'<?xml version="1.0" encoding="UTF-8"?>\n'
            b'<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">\n'
            b"  <rootfiles>\n"
            b'    <rootfile full-path="OEBPS/content.opf"'
            b' media-type="application/oebps-package+xml"/>\n'
            b"  </rootfiles>\n"
            b"</container>\n"
        )

    def package_document(self, request: DigitalComicPackageRequest) -> bytes:
        metadata = request.metadata
        lang = escape(metadata.language)
        lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="pub-id"'
            ' prefix="rendition: http://www.idpf.org/vocab/rendition/#"'
            f" xml:lang={quoteattr(metadata.language)}>",
            '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">',
            f'    <dc:identifier id="pub-id">{escape(metadata.identifier)}</dc:identifier>',
            f"    <dc:title>{escape(metadata.title)}</dc:title>",
            f"    <dc:language>{lang}</dc:language>",
        ]
        for creator in metadata.creators:
            if creator.strip():
                lines.append(f"    <dc:creator>{escape(creator.strip())}</dc:creator>")
        if metadata.description:
            lines.append(f"    <dc:description>{escape(metadata.description)}</dc:description>")
        if metadata.series:
            lines.append(
                '    <meta property="belongs-to-collection" id="series">'
                f"{escape(metadata.series)}</meta>"
            )
            lines.append('    <meta refines="#series" property="collection-type">series</meta>')
            if metadata.volume:
                lines.append(
                    '    <meta refines="#series" property="group-position">'
                    f"{escape(metadata.volume)}</meta>"
                )
        lines.extend(
            [
                f'    <meta property="dcterms:modified">{escape(self._clock())}</meta>',
                '    <meta property="rendition:layout">pre-paginated</meta>',
                '    <meta property="rendition:orientation">auto</meta>',
                '    <meta property="rendition:spread">auto</meta>',
            ]
        )
        cover = request.cover
        if cover is not None:
            lines.append(f'    <meta name="cover" content="img-{cover.logical_index + 1:06d}"/>')
        lines.append("  </metadata>")
        lines.append("  <manifest>")
        lines.append(
            '    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml"'
            ' properties="nav"/>'
        )
        for item in request.items:
            number = f"{item.logical_index + 1:06d}"
            lines.append(
                f'    <item id="page-{number}" href="{self.PAGE_DIR}/{number}.xhtml"'
                ' media-type="application/xhtml+xml"/>'
            )
            properties = ' properties="cover-image"' if item.role == "cover" else ""
            lines.append(
                f'    <item id="img-{number}" href="{self.IMAGE_DIR}/{number}.{item.extension}"'
                f" media-type={quoteattr(item.media_type)}{properties}/>"
            )
        lines.append("  </manifest>")
        lines.append(f'  <spine page-progression-direction="{request.progression}">')
        for item in request.items:
            number = f"{item.logical_index + 1:06d}"
            spread = self.SPREAD_PROPERTIES.get(item.spread_side)
            properties = f" properties={quoteattr(spread)}" if spread else ""
            lines.append(f'    <itemref idref="page-{number}"{properties}/>')
        lines.append("  </spine>")
        lines.append("</package>")
        return ("\n".join(lines) + "\n").encode("utf-8")

    def nav_document(self, request: DigitalComicPackageRequest) -> bytes:
        lang = quoteattr(request.metadata.language)
        lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            "<!DOCTYPE html>",
            f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops"'
            f" xml:lang={lang} lang={lang}>",
            f'<head><meta charset="utf-8"/><title>{escape(request.metadata.title)}</title></head>',
            "<body>",
            '  <nav epub:type="toc" id="toc"><h1>Contents</h1><ol>',
        ]
        for index, item in enumerate(request.items):
            label = (
                request.page_labels[index]
                if request.page_labels
                else f"Page {item.logical_index + 1}"
            )
            if item.role == "cover" and not request.page_labels:
                label = "Cover"
            lines.append(
                f'    <li><a href="{self.PAGE_DIR}/{item.logical_index + 1:06d}.xhtml">'
                f"{escape(label)}</a></li>"
            )
        lines.extend(["  </ol></nav>", "</body>", "</html>"])
        return ("\n".join(lines) + "\n").encode("utf-8")

    def page_document(self, request: DigitalComicPackageRequest, item: PublicationItem) -> bytes:
        width, height = item.pixel_size
        width = max(1, int(width))
        height = max(1, int(height))
        lang = quoteattr(request.metadata.language)
        alt = escape(item.alt_text or "")
        number = f"{item.logical_index + 1:06d}"
        return (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            "<!DOCTYPE html>\n"
            f'<html xmlns="http://www.w3.org/1999/xhtml" xml:lang={lang} lang={lang}>\n'
            "<head>\n"
            '  <meta charset="utf-8"/>\n'
            f"  <title>{escape(request.metadata.title)} - {number}</title>\n"
            f'  <meta name="viewport" content="width={width}, height={height}"/>\n'
            "  <style>html,body{margin:0;padding:0}"
            "img{display:block;width:100%;height:100%}</style>\n"
            "</head>\n"
            "<body>\n"
            f'  <img src="../{self.IMAGE_DIR}/{number}.{item.extension}" alt={quoteattr(alt)}'
            f' width="{width}" height="{height}"/>\n'
            "</body>\n"
            "</html>\n"
        ).encode()

    # -- archive -------------------------------------------------------------------

    def write(self, request: DigitalComicPackageRequest, image_source: ImageSource) -> int:
        package = self.package_document(request)
        nav = self.nav_document(request)

        def encode(tmp: Path) -> None:
            with zipfile.ZipFile(tmp, "w") as zf:
                zf.writestr(
                    zipfile.ZipInfo("mimetype"),
                    b"application/epub+zip",
                    compress_type=zipfile.ZIP_STORED,
                )
                zf.writestr("META-INF/container.xml", self.container_xml(), zipfile.ZIP_DEFLATED)
                zf.writestr(f"{self.ROOT}content.opf", package, zipfile.ZIP_DEFLATED)
                zf.writestr(f"{self.ROOT}nav.xhtml", nav, zipfile.ZIP_DEFLATED)
                for item in request.items:
                    zf.writestr(
                        self.page_member(item),
                        self.page_document(request, item),
                        zipfile.ZIP_DEFLATED,
                    )
                    zf.write(image_source(item), self.image_member(item), zipfile.ZIP_STORED)

        return write_atomically(Path(request.destination), encode)
