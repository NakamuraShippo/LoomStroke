"""Physical dimensions for the legacy built-in A and JIS B paper presets."""

from .startpage_models import CanvasPreset

# Name, millimetres, and legacy pixel dimensions identify existing presets
# without changing persisted preset IDs or interpreting user presets as paper.
_PAPERS = (
    ("A0", 841, 1189, 2384, 3370),
    ("A1", 594, 841, 1684, 2384),
    ("A2", 420, 594, 1191, 1684),
    ("A3", 297, 420, 842, 1191),
    ("A4", 210, 297, 595, 842),
    ("A5", 148, 210, 420, 595),
    ("A6", 105, 148, 298, 420),
    ("A7", 74, 105, 210, 298),
    ("A8", 52, 74, 147, 210),
    ("B0", 1030, 1456, 2920, 4127),
    ("B1", 728, 1030, 2064, 2920),
    ("B2", 515, 728, 1460, 2064),
    ("B3", 364, 515, 1032, 1460),
    ("B4", 257, 364, 729, 1032),
    ("B5", 182, 257, 516, 729),
    ("B6", 128, 182, 363, 516),
    ("B7", 91, 128, 258, 363),
    ("B8", 64, 91, 181, 258),
)


def paper_dimensions(preset: CanvasPreset, dpi: int) -> tuple[int, int] | None:
    """Resolve unchanged built-in entries; arbitrary presets remain pixel-based."""
    for name, width_mm, height_mm, width_px, height_px in _PAPERS:
        if preset.label == f"{name} {width_px} x {height_px} px" and (
            preset.width_px,
            preset.height_px,
        ) == (width_px, height_px):
            return (
                max(1, round(width_mm * dpi / 25.4)),
                max(1, round(height_mm * dpi / 25.4)),
            )
    return None
