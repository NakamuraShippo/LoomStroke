"""Canvas coverage windows for retained rectangular edge fragments."""

from .clip_mask import union_clip_mask
from .models import PanelFrameError


def span_window(page_size, rect, side, span, *, window_ends=(False, False), width=0):
    x, y, w, h = rect
    axis = 0 if side in {"top", "bottom"} else 1
    start, extent = (x, w) if axis == 0 else (y, h)
    lo, hi = span if side in {"top", "right"} else (1 - span[1], 1 - span[0])
    lo, hi = start + extent * lo, start + extent * hi
    low_outer = span[0] == 0 if side in {"top", "right"} else span[1] == 1
    high_outer = span[1] == 1 if side in {"top", "right"} else span[0] == 0
    low_cut, high_cut = window_ends if side in {"top", "right"} else window_ends[::-1]
    if low_outer and low_cut:
        lo -= width / 2
        low_outer = False
    if high_outer and high_cut:
        hi += width / 2
        high_outer = False
    lo = 0 if low_outer else round(lo)
    hi = page_size[axis] if high_outer else round(hi)
    if hi <= lo:
        raise PanelFrameError("edge fragment is smaller than one pixel")
    bounds = (lo, 0, hi - lo, page_size[1]) if axis == 0 else (0, lo, page_size[0], hi - lo)
    return union_clip_mask(tuple(map(int, page_size)), [bounds])
