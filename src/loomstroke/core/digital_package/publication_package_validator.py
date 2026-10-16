"""Member and archive validation (CT-26 spec §Package validation).

Before writing: cover uniqueness / position, duplicate logical indices,
member-path collisions (case-folded), traversal / absolute / reserved
names, member count.  After writing: the archive is reopened, CRCs are
tested, the expected members are present and each item's bytes hash to
the recorded ``content_hash``; for EPUB the OCF ``mimetype`` rule is
checked too.  External conformance (EPUBCheck) is *not* part of this —
it stays ``not_checked`` unless the adapter actually ran.
"""

from __future__ import annotations

import hashlib
import re
import zipfile
from collections.abc import Callable, Sequence
from pathlib import Path, PurePosixPath
from typing import ClassVar

from .digital_package_request import DigitalComicPackageRequest
from .package_issue import PackageIssue
from .publication_item import PublicationItem

__all__ = ["PublicationPackageValidator"]

_RESERVED = (
    {"con", "prn", "aux", "nul"}
    | {f"com{d}" for d in range(1, 10)}
    | {f"lpt{d}" for d in range(1, 10)}
)
_INVALID = re.compile(r'[<>:"\\|?*\x00-\x1f]')


class PublicationPackageValidator:
    """Deterministic checks; issues are sorted so reports compare equal."""

    MAX_MEMBERS: ClassVar[int] = 5000
    MAX_TOTAL_BYTES: ClassVar[int] = 4 * 1024 * 1024 * 1024

    def validate_items(self, items: Sequence[PublicationItem]) -> tuple[PackageIssue, ...]:
        issues: list[PackageIssue] = []
        if not items:
            issues.append(PackageIssue("no_items"))
        covers = [item for item in items if item.role == "cover"]
        if len(covers) > 1:
            issues.append(PackageIssue("multiple_covers", detail=str(len(covers))))
        if covers and covers[0].logical_index != 0:
            issues.append(PackageIssue("cover_not_first", detail=covers[0].package_path))
        if not covers and items:
            issues.append(PackageIssue("no_cover", severity="warning"))
        indices = [item.logical_index for item in items]
        if len(set(indices)) != len(indices):
            issues.append(PackageIssue("duplicate_logical_index"))
        if indices != sorted(indices):
            issues.append(PackageIssue("unsorted_logical_index"))
        if len(items) > self.MAX_MEMBERS:
            issues.append(PackageIssue("too_many_members", detail=str(len(items))))
        seen: dict[str, str] = {}
        for item in items:
            issues.extend(self._path_issues(item.package_path))
            folded = item.package_path.casefold()
            if folded in seen:
                issues.append(PackageIssue("member_path_collision", detail=item.package_path))
            seen[folded] = item.package_path
        total = sum(max(0, item.byte_size) for item in items)
        if total > self.MAX_TOTAL_BYTES:
            issues.append(PackageIssue("package_too_large", detail=str(total)))
        return tuple(sorted(issues, key=lambda issue: (issue.code, issue.detail)))

    @staticmethod
    def _path_issues(path: str) -> list[PackageIssue]:
        issues: list[PackageIssue] = []
        pure = PurePosixPath(path)
        if not path or path.startswith("/") or "\\" in path or re.match(r"^[A-Za-z]:", path):
            issues.append(PackageIssue("absolute_member_path", detail=path))
        if any(part in {"..", ".", ""} for part in pure.parts):
            issues.append(PackageIssue("member_path_traversal", detail=path))
        for part in pure.parts:
            if _INVALID.search(part) or part.rstrip(" .") != part:
                issues.append(PackageIssue("invalid_member_name", detail=path))
                break
            if part.rsplit(".", 1)[0].lower() in _RESERVED:
                issues.append(PackageIssue("reserved_member_name", detail=path))
                break
        return issues

    def validate_request(self, request: DigitalComicPackageRequest) -> tuple[PackageIssue, ...]:
        issues = list(self.validate_items(request.items))
        if request.format_id == "epub3_fxl":
            for item in request.items:
                if item.media_type == "image/webp":
                    issues.append(
                        PackageIssue(
                            "epub_webp_not_core", severity="warning", detail=item.package_path
                        )
                    )
        return tuple(sorted(set(issues), key=lambda issue: (issue.code, issue.detail)))

    # -- archive ---------------------------------------------------------------------

    def validate_archive(
        self,
        archive: Path,
        request: DigitalComicPackageRequest,
        *,
        member_prefix: str = "",
        member_for: Callable[[PublicationItem], str] | None = None,
    ) -> tuple[PackageIssue, ...]:
        """Reopen ``archive`` and verify CRCs, members and hashes.

        ``member_for`` maps an item to its archive member name (EPUB keeps
        images under ``OEBPS/images/``); default is ``prefix + package_path``.
        """
        issues: list[PackageIssue] = []
        try:
            with zipfile.ZipFile(archive) as zf:
                bad = zf.testzip()
                if bad is not None:
                    issues.append(PackageIssue("archive_crc_mismatch", detail=str(bad)))
                names = zf.namelist()
                if request.format_id == "epub3_fxl":
                    issues.extend(self._epub_ocf_issues(zf, names))
                present = set(names)
                for item in request.items:
                    member = (
                        member_for(item)
                        if member_for is not None
                        else f"{member_prefix}{item.package_path}"
                    )
                    if member not in present:
                        issues.append(PackageIssue("member_missing", detail=item.package_path))
                        continue
                    if item.content_hash:
                        digest = hashlib.sha256(zf.read(member)).hexdigest()
                        if digest != item.content_hash:
                            issues.append(
                                PackageIssue("member_hash_mismatch", detail=item.package_path)
                            )
        except (OSError, zipfile.BadZipFile) as error:
            issues.append(PackageIssue("archive_unreadable", detail=type(error).__name__))
        return tuple(sorted(issues, key=lambda issue: (issue.code, issue.detail)))

    def validate_directory(
        self, root: Path, request: DigitalComicPackageRequest
    ) -> tuple[PackageIssue, ...]:
        """Image-manifest packages: every item present under ``root`` with its hash."""
        issues: list[PackageIssue] = []
        for item in request.items:
            target = Path(root, *item.package_path.split("/"))
            if not target.is_file():
                issues.append(PackageIssue("member_missing", detail=item.package_path))
                continue
            if item.content_hash:
                digest = hashlib.sha256(target.read_bytes()).hexdigest()
                if digest != item.content_hash:
                    issues.append(PackageIssue("member_hash_mismatch", detail=item.package_path))
        return tuple(sorted(issues, key=lambda issue: (issue.code, issue.detail)))

    @staticmethod
    def _epub_ocf_issues(zf: zipfile.ZipFile, names: list[str]) -> list[PackageIssue]:
        issues: list[PackageIssue] = []
        if not names or names[0] != "mimetype":
            issues.append(PackageIssue("epub_mimetype_not_first"))
            return issues
        info = zf.getinfo("mimetype")
        if info.compress_type != zipfile.ZIP_STORED:
            issues.append(PackageIssue("epub_mimetype_compressed"))
        if zf.read("mimetype") != b"application/epub+zip":
            issues.append(PackageIssue("epub_mimetype_content"))
        for required in ("META-INF/container.xml",):
            if required not in names:
                issues.append(PackageIssue("epub_member_missing", detail=required))
        return issues
