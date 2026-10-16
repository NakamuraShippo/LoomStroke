"""``ComicInfo.xml`` for CBZ (CT-26 spec §CBZ — de facto Anansi schema).

Only fields the schema can express are emitted; everything else lives in
the LoomStroke manifest.  Strings are XML-escaped, never interpolated.
"""

from __future__ import annotations

from typing import ClassVar
from xml.sax.saxutils import escape, quoteattr

from .digital_package_request import DigitalComicPackageRequest

__all__ = ["ComicInfoWriter"]


class ComicInfoWriter:
    """Serialize publication metadata as ComicInfo.xml bytes."""

    FILE_NAME: ClassVar[str] = "ComicInfo.xml"
    PAGE_TYPES: ClassVar[dict[str, str]] = {
        "cover": "FrontCover",
        "front_matter": "InnerCover",
        "body": "Story",
        "back_matter": "Other",
    }

    def xml(self, request: DigitalComicPackageRequest) -> bytes:
        metadata = request.metadata
        lines = [
            '<?xml version="1.0" encoding="utf-8"?>',
            "<ComicInfo"
            ' xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"'
            ' xmlns:xsd="http://www.w3.org/2001/XMLSchema">',
            f"  <Title>{escape(metadata.title)}</Title>",
        ]
        if metadata.series:
            lines.append(f"  <Series>{escape(metadata.series)}</Series>")
        if metadata.volume:
            lines.append(f"  <Number>{escape(metadata.volume)}</Number>")
        if metadata.description:
            lines.append(f"  <Summary>{escape(metadata.description)}</Summary>")
        if metadata.creator_line:
            lines.append(f"  <Writer>{escape(metadata.creator_line)}</Writer>")
        lines.append(f"  <LanguageISO>{escape(metadata.language)}</LanguageISO>")
        lines.append(f"  <PageCount>{len(request.items)}</PageCount>")
        lines.append(
            "  <Manga>YesAndRightToLeft</Manga>"
            if request.binding == "right"
            else "  <Manga>Yes</Manga>"
        )
        lines.append("  <Pages>")
        for item in request.items:
            attributes = [
                f"Image={quoteattr(str(item.logical_index))}",
                f"Type={quoteattr(self.PAGE_TYPES.get(item.role, 'Story'))}",
            ]
            if item.pixel_size != (0, 0):
                attributes.append(f"ImageWidth={quoteattr(str(item.pixel_size[0]))}")
                attributes.append(f"ImageHeight={quoteattr(str(item.pixel_size[1]))}")
            if item.byte_size:
                attributes.append(f"ImageSize={quoteattr(str(item.byte_size))}")
            lines.append(f"    <Page {' '.join(attributes)} />")
        lines.append("  </Pages>")
        lines.append("</ComicInfo>")
        return ("\n".join(lines) + "\n").encode("utf-8")
