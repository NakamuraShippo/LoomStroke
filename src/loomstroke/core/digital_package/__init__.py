"""Digital comic package: CBZ / fixed-layout EPUB 3 / image manifest (CT-26).

Pure, Qt-free.  One class per module:

- :mod:`publication_metadata` / :mod:`publication_item` /
  :mod:`digital_package_request` — the publication model (spec §データモデル)
- :mod:`publication_planner` — logical order -> items (cover first, zero-padded paths)
- :mod:`package_issue` / :mod:`publication_package_validator` — member / archive checks
- :mod:`comic_info_writer` — ``ComicInfo.xml`` (de facto schema)
- :mod:`publication_manifest_writer` — ``publication_manifest.json`` (v1)
- :mod:`cbz_package_writer` / :mod:`fixed_layout_epub_writer` /
  :mod:`image_manifest_package_writer` — format writers (atomic)
- :mod:`epub_check_adapter` — optional external EPUBCheck (never assumed)
- :mod:`digital_package_result`
"""

from .cbz_package_writer import CbzPackageWriter
from .comic_info_writer import ComicInfoWriter
from .digital_package_request import DigitalComicPackageRequest
from .digital_package_result import DigitalPackageResult
from .epub_check_adapter import EpubCheckAdapter
from .fixed_layout_epub_writer import FixedLayoutEpubWriter
from .image_manifest_package_writer import ImageManifestPackageWriter
from .package_issue import PackageIssue
from .publication_item import PublicationItem
from .publication_manifest_writer import PublicationManifestWriter
from .publication_metadata import PublicationMetadata
from .publication_package_validator import PublicationPackageValidator
from .publication_planner import DigitalComicPackagePlanner

__all__ = [
    "CbzPackageWriter",
    "ComicInfoWriter",
    "DigitalComicPackagePlanner",
    "DigitalComicPackageRequest",
    "DigitalPackageResult",
    "EpubCheckAdapter",
    "FixedLayoutEpubWriter",
    "ImageManifestPackageWriter",
    "PackageIssue",
    "PublicationItem",
    "PublicationManifestWriter",
    "PublicationMetadata",
    "PublicationPackageValidator",
]
