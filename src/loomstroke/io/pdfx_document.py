"""Flattened CMYK PDF/X page writer, separate from the standard Qt PDF path.

This low-level writer deliberately has no UI, rendering, file replacement, or
conformance decision. The caller must label output as a candidate until an
independent PDF/X validator has checked the generated file.
"""

from __future__ import annotations

import zlib
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import numpy as np
import pikepdf
from numpy.typing import NDArray

from ..core.print_page_geometry import PrintPageGeometry
from .color_profile_registry import ColorProfileRegistry, RenderingIntent
from .print_color_output import rgba_to_cmyk

_VERSIONS = {"pdfx1a": ("PDF/X-1a:2003", "1.4"), "pdfx4": ("PDF/X-4", "1.6")}
_TITLE = "LoomStroke print export"
_CREATOR = "LoomStroke"


class PdfXDocument:
    """Build an image-only, CMYK PDF/X candidate with explicit page boxes."""

    def __init__(
        self,
        registry: ColorProfileRegistry,
        *,
        conformance: str,
        cmyk_profile_id: str,
        source_profile_id: str,
        intent: RenderingIntent = "relative",
        black_point_compensation: bool = True,
    ) -> None:
        if conformance not in _VERSIONS:
            raise ValueError("Unsupported PDF/X conformance")
        info = registry.info(cmyk_profile_id)
        profile = registry.profile_bytes(cmyk_profile_id)
        if info is None or profile is None or info.colorspace != "CMYK":
            raise ValueError("A registered CMYK output profile is required")
        if registry.profile_bytes(source_profile_id) is None:
            raise ValueError("A registered source profile is required")
        self._registry = registry
        self._conformance = conformance
        self._cmyk_profile_id = cmyk_profile_id
        self._source_profile_id = source_profile_id
        self._intent = intent
        self._black_point_compensation = black_point_compensation
        self._pdf = pikepdf.Pdf.new()
        created = datetime.now(UTC)
        pdf_date = created.strftime("D:%Y%m%d%H%M%SZ")
        self._pdf.docinfo["/Title"] = _TITLE
        self._pdf.docinfo["/Creator"] = _CREATOR
        self._pdf.docinfo["/CreationDate"] = pdf_date
        self._pdf.docinfo["/ModDate"] = pdf_date
        self._pdf.docinfo["/Trapped"] = pikepdf.Name("/False")
        self._pdf.docinfo["/GTS_PDFXVersion"] = _VERSIONS[conformance][0]
        icc = self._pdf.make_stream(profile)
        icc.N = 4
        output_intent = self._pdf.make_indirect(
            pikepdf.Dictionary(
                Type=pikepdf.Name("/OutputIntent"),
                S=pikepdf.Name("/GTS_PDFX"),
                OutputConditionIdentifier="Custom",
                Info=info.name,
                DestOutputProfile=icc,
            )
        )
        self._pdf.Root.OutputIntents = pikepdf.Array([output_intent])
        self._pdf.Root.Metadata = self._pdf.make_stream(
            _xmp(_VERSIONS[conformance][0], created),
            Type=pikepdf.Name("/Metadata"),
            Subtype=pikepdf.Name("/XML"),
        )

    def add_page(self, rgba: NDArray[np.uint8], geometry: PrintPageGeometry) -> None:
        if rgba.ndim != 3 or rgba.shape[2] != 4 or rgba.dtype != np.uint8:
            raise ValueError("PDF/X page must be RGBA8")
        height, width = rgba.shape[:2]
        media_w, media_h = geometry.media_box_pt[2:]
        if width <= 0 or height <= 0 or media_w <= 0 or media_h <= 0:
            raise ValueError("Invalid PDF/X page dimensions")
        cmyk = rgba_to_cmyk(
            rgba,
            self._registry,
            source_profile_id=self._source_profile_id,
            cmyk_profile_id=self._cmyk_profile_id,
            intent=self._intent,
            black_point_compensation=self._black_point_compensation,
        )
        page = self._pdf.add_blank_page(page_size=(media_w, media_h))
        page.obj.TrimBox = pikepdf.Array(_pdf_box(geometry.trim_box_pt, media_h))
        page.obj.BleedBox = pikepdf.Array(_pdf_box(geometry.bleed_box_pt, media_h))
        page.obj.CropBox = pikepdf.Array([0, 0, media_w, media_h])
        image = self._pdf.make_stream(
            zlib.compress(np.ascontiguousarray(cmyk).tobytes()),
            Type=pikepdf.Name("/XObject"),
            Subtype=pikepdf.Name("/Image"),
            Width=width,
            Height=height,
            ColorSpace=pikepdf.Name("/DeviceCMYK"),
            BitsPerComponent=8,
            Filter=pikepdf.Name("/FlateDecode"),
        )
        page.obj.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im0=image))
        page.obj.Contents = self._pdf.make_stream(_content(geometry, media_h))

    def save(self, destination: Path) -> None:
        if not self._pdf.pages:
            raise ValueError("PDF/X document has no pages")
        # Keep the caller-owned inode intact for atomic_export_writer's identity check.
        with Path(destination).open("wb") as stream:
            self._pdf.save(stream, force_version=_VERSIONS[self._conformance][1])


def _pdf_box(box: tuple[float, float, float, float], media_h: float) -> tuple[float, ...]:
    x, y, width, height = box
    return (x, media_h - y - height, x + width, media_h - y)


def _content(geometry: PrintPageGeometry, media_h: float) -> bytes:
    x, y, width, height = geometry.content_rect_pt
    commands = [f"q {width:.6f} 0 0 {height:.6f} {x:.6f} {media_h-y-height:.6f} cm /Im0 Do Q"]
    if geometry.marks or geometry.circle_marks:
        options = geometry.mark_options
        color = "1 1 1 1" if options.color == "registration" else "0 0 0 1"
        commands.append(f"q {color} K {options.width_pt:.6f} w")
        for mark in geometry.marks:
            commands.append(
                f"{mark.x1:.6f} {media_h-mark.y1:.6f} m " f"{mark.x2:.6f} {media_h-mark.y2:.6f} l S"
            )
        for cx, cy, radius in geometry.circle_marks:
            bottom = media_h - cy
            control = radius * 0.552284749831
            commands.append(
                f"{cx+radius:.6f} {bottom:.6f} m "
                f"{cx+radius:.6f} {bottom+control:.6f} "
                f"{cx+control:.6f} {bottom+radius:.6f} {cx:.6f} {bottom+radius:.6f} c "
                f"{cx-control:.6f} {bottom+radius:.6f} "
                f"{cx-radius:.6f} {bottom+control:.6f} {cx-radius:.6f} {bottom:.6f} c "
                f"{cx-radius:.6f} {bottom-control:.6f} "
                f"{cx-control:.6f} {bottom-radius:.6f} {cx:.6f} {bottom-radius:.6f} c "
                f"{cx+control:.6f} {bottom-radius:.6f} "
                f"{cx+radius:.6f} {bottom-control:.6f} {cx+radius:.6f} {bottom:.6f} c S"
            )
        commands.append("Q")
    return ("\n".join(commands) + "\n").encode("ascii")


def _xmp(version: str, created: datetime) -> bytes:
    timestamp = created.strftime("%Y-%m-%dT%H:%M:%SZ")
    document_id = f"uuid:{uuid4()}"
    instance_id = f"uuid:{uuid4()}"
    return (
        '<?xpacket begin="\ufeff" id="W5M0MpCehiHzreSzNTczkc9d"?>\n'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">\n'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">\n'
        '<rdf:Description rdf:about="" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" '
        'xmlns:xmp="http://ns.adobe.com/xap/1.0/" '
        'xmlns:xmpMM="http://ns.adobe.com/xap/1.0/mm/" '
        'xmlns:pdf="http://ns.adobe.com/pdf/1.3/" '
        'xmlns:pdfxid="http://www.npes.org/pdfx/ns/id/" '
        f'pdfxid:GTS_PDFXVersion="{version}" '
        f'xmp:CreateDate="{timestamp}" xmp:ModifyDate="{timestamp}" '
        f'xmp:MetadataDate="{timestamp}" xmp:CreatorTool="{_CREATOR}" '
        f'xmpMM:DocumentID="{document_id}" xmpMM:InstanceID="{instance_id}" '
        'xmpMM:RenditionClass="default" xmpMM:VersionID="1" '
        'pdf:Trapped="False">\n'
        f'<dc:title><rdf:Alt><rdf:li xml:lang="x-default">{_TITLE}</rdf:li>'
        "</rdf:Alt></dc:title>\n"
        "</rdf:Description>\n"
        "</rdf:RDF></x:xmpmeta>\n"
        '<?xpacket end="w"?>'
    ).encode()
