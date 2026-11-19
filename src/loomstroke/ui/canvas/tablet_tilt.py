"""Map the two physical stylus inclination angles into the axes of the document."""

import math


def document_tilt(viewport, x_degrees: float, y_degrees: float) -> tuple[float, float]:
    if x_degrees == y_degrees == 0 or not (math.isfinite(x_degrees) and math.isfinite(y_degrees)):
        return 0.0, 0.0
    transform = viewport.get_transform()
    if transform.m12() == transform.m21() == 0 and transform.m11() > 0 and transform.m22() > 0:
        return x_degrees, y_degrees
    # Qt's two tilt angles are projections from the normal, so rotate the slopes in
    # the tablet plane. Mapping two points removes translation; normalization removes zoom.
    tx = math.tan(math.radians(max(-89.9, min(89.9, x_degrees))))
    ty = math.tan(math.radians(max(-89.9, min(89.9, y_degrees))))
    ox, oy = viewport.widget_to_canvas(0, 0)
    x, y = viewport.widget_to_canvas(tx, ty)
    dx, dy = x - ox, y - oy
    norm = math.hypot(dx, dy)
    if not norm:
        return 0.0, 0.0
    scale = math.hypot(tx, ty) / norm
    return math.degrees(math.atan(dx * scale)), math.degrees(math.atan(dy * scale))
