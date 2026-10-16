"""Read font requirements for the package without collecting font binaries."""

from loomstroke.io.layer_io import DocumentMetadata

from .preflight_snapshot_builder import DocumentPreflightSnapshotBuilder


def package_font_families(window):
    host = getattr(window, "_host_api", None) or getattr(window, "host", None)
    pages = getattr(host, "pages", None)
    if pages is None:
        return None
    snapshot = DocumentPreflightSnapshotBuilder(pages, DocumentMetadata(width=1, height=1)).build()
    return tuple(
        sorted(
            {
                family
                for page in snapshot.pages
                for text in page.texts
                for family in text.font_families
            }
        )
    )
