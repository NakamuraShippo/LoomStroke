"""Pure-Python polygon triangulation (port of mapbox/earcut).

Ported from earcut v2.2.4 (https://github.com/mapbox/earcut),
Copyright (c) 2016 Mapbox, ISC License.  The port keeps the original
algorithm structure so upstream fixes stay easy to cross-check.

Input is a flat ``[x0, y0, x1, y1, ...]`` coordinate list where the first
ring is the outer polygon and ``hole_indices`` marks the vertex index at
which each hole ring starts.  Output is a flat list of vertex indices
forming triangles.  Degenerate or self-intersecting input produces a
best-effort triangulation instead of raising.
"""

from __future__ import annotations

import math


class _Node:
    __slots__ = ("i", "next", "next_z", "prev", "prev_z", "steiner", "x", "y", "z")

    def __init__(self, i: int, x: float, y: float) -> None:
        self.i = i
        self.x = x
        self.y = y
        self.prev: _Node | None = None
        self.next: _Node | None = None
        self.z = 0
        self.prev_z: _Node | None = None
        self.next_z: _Node | None = None
        self.steiner = False


def earcut(data: list[float], hole_indices: list[int] | None = None) -> list[int]:
    """Triangulate a 2D polygon with optional holes; returns triangle indices."""

    hole_indices = hole_indices or []
    has_holes = bool(hole_indices)
    outer_len = hole_indices[0] * 2 if has_holes else len(data)
    outer_node = _linked_list(data, 0, outer_len, True)
    triangles: list[int] = []

    if outer_node is None or outer_node.next is outer_node.prev:
        return triangles

    min_x = min_y = 0.0
    inv_size = 0.0

    if has_holes:
        outer_node = _eliminate_holes(data, hole_indices, outer_node)
        if outer_node is None:
            return triangles

    # When the polygon is large enough, use z-order curve hashing.
    if len(data) > 80 * 2:
        min_x = max_x = data[0]
        min_y = max_y = data[1]
        for i in range(2, outer_len, 2):
            x = data[i]
            y = data[i + 1]
            min_x = min(min_x, x)
            min_y = min(min_y, y)
            max_x = max(max_x, x)
            max_y = max(max_y, y)
        inv_size = max(max_x - min_x, max_y - min_y)
        inv_size = 32767.0 / inv_size if inv_size != 0.0 else 0.0

    _earcut_linked(outer_node, triangles, min_x, min_y, inv_size, 0)
    return triangles


def _linked_list(data: list[float], start: int, end: int, clockwise: bool) -> _Node | None:
    last: _Node | None = None
    if clockwise == (_signed_area(data, start, end) > 0.0):
        for i in range(start, end, 2):
            last = _insert_node(i // 2, data[i], data[i + 1], last)
    else:
        for i in range(end - 2, start - 2, -2):
            last = _insert_node(i // 2, data[i], data[i + 1], last)

    if last is not None and last.next is not None and _equals(last, last.next):
        _remove_node(last)
        last = last.next
    return last


def _filter_points(start: _Node | None, end: _Node | None = None) -> _Node | None:
    if start is None:
        return start
    if end is None:
        end = start

    p = start
    while True:
        again = False
        next_node = p.next
        prev_node = p.prev
        assert next_node is not None and prev_node is not None
        if not p.steiner and (_equals(p, next_node) or _area(prev_node, p, next_node) == 0.0):
            _remove_node(p)
            p = end = prev_node
            if p is p.next:
                break
            again = True
        else:
            p = next_node
        if not (again or p is not end):
            break
    return end


def _earcut_linked(
    ear: _Node | None,
    triangles: list[int],
    min_x: float,
    min_y: float,
    inv_size: float,
    pass_number: int,
) -> None:
    if ear is None:
        return

    if pass_number == 0 and inv_size != 0.0:
        _index_curve(ear, min_x, min_y, inv_size)

    stop = ear
    while ear is not None and ear.prev is not ear.next:
        assert ear.prev is not None and ear.next is not None
        prev = ear.prev
        next_node = ear.next

        is_ear = _is_ear_hashed(ear, min_x, min_y, inv_size) if inv_size != 0.0 else _is_ear(ear)
        if is_ear:
            triangles.extend((prev.i, ear.i, next_node.i))
            _remove_node(ear)
            assert next_node.next is not None
            ear = next_node.next
            stop = next_node.next
            continue

        ear = next_node

        if ear is stop:
            if pass_number == 0:
                _earcut_linked(_filter_points(ear), triangles, min_x, min_y, inv_size, 1)
            elif pass_number == 1:
                filtered = _filter_points(ear)
                if filtered is not None:
                    ear = _cure_local_intersections(filtered, triangles)
                    _earcut_linked(ear, triangles, min_x, min_y, inv_size, 2)
            elif pass_number == 2:
                _split_earcut(ear, triangles, min_x, min_y, inv_size)
            break


def _is_ear(ear: _Node) -> bool:
    a = ear.prev
    b = ear
    c = ear.next
    assert a is not None and c is not None

    if _area(a, b, c) >= 0.0:
        return False

    p = c.next
    while p is not a:
        assert p is not None and p.prev is not None and p.next is not None
        if (
            _point_in_triangle(a.x, a.y, b.x, b.y, c.x, c.y, p.x, p.y)
            and _area(p.prev, p, p.next) >= 0.0
        ):
            return False
        p = p.next
    return True


def _is_ear_hashed(ear: _Node, min_x: float, min_y: float, inv_size: float) -> bool:
    a = ear.prev
    b = ear
    c = ear.next
    assert a is not None and c is not None

    if _area(a, b, c) >= 0.0:
        return False

    tri_min_x = min(a.x, b.x, c.x)
    tri_min_y = min(a.y, b.y, c.y)
    tri_max_x = max(a.x, b.x, c.x)
    tri_max_y = max(a.y, b.y, c.y)

    min_z = _z_order(tri_min_x, tri_min_y, min_x, min_y, inv_size)
    max_z = _z_order(tri_max_x, tri_max_y, min_x, min_y, inv_size)

    p = ear.prev_z
    n = ear.next_z

    while p is not None and p.z >= min_z and n is not None and n.z <= max_z:
        if (
            p is not ear.prev
            and p is not ear.next
            and _point_in_triangle(a.x, a.y, b.x, b.y, c.x, c.y, p.x, p.y)
            and _area(p.prev, p, p.next) >= 0.0  # type: ignore[arg-type]
        ):
            return False
        p = p.prev_z
        if (
            n is not ear.prev
            and n is not ear.next
            and _point_in_triangle(a.x, a.y, b.x, b.y, c.x, c.y, n.x, n.y)
            and _area(n.prev, n, n.next) >= 0.0  # type: ignore[arg-type]
        ):
            return False
        n = n.next_z

    while p is not None and p.z >= min_z:
        if (
            p is not ear.prev
            and p is not ear.next
            and _point_in_triangle(a.x, a.y, b.x, b.y, c.x, c.y, p.x, p.y)
            and _area(p.prev, p, p.next) >= 0.0  # type: ignore[arg-type]
        ):
            return False
        p = p.prev_z

    while n is not None and n.z <= max_z:
        if (
            n is not ear.prev
            and n is not ear.next
            and _point_in_triangle(a.x, a.y, b.x, b.y, c.x, c.y, n.x, n.y)
            and _area(n.prev, n, n.next) >= 0.0  # type: ignore[arg-type]
        ):
            return False
        n = n.next_z
    return True


def _cure_local_intersections(start: _Node, triangles: list[int]) -> _Node:
    p = start
    while True:
        a = p.prev
        assert a is not None and p.next is not None
        b = p.next.next
        assert b is not None

        if (
            not _equals(a, b)
            and _intersects(a, p, p.next, b)
            and _locally_inside(a, b)
            and _locally_inside(b, a)
        ):
            triangles.extend((a.i, p.i, b.i))
            _remove_node(p)
            _remove_node(p.next)
            p = start = b
        assert p.next is not None
        p = p.next
        if p is start:
            break
    result = _filter_points(p)
    assert result is not None
    return result


def _split_earcut(
    start: _Node, triangles: list[int], min_x: float, min_y: float, inv_size: float
) -> None:
    a: _Node | None = start
    while a is not None:
        assert a.next is not None
        b = a.next.next
        while b is not None and b is not a.prev:
            if a.i != b.i and _is_valid_diagonal(a, b):
                c = _split_polygon(a, b)
                a_filtered = _filter_points(a, a.next)
                c_filtered = _filter_points(c, c.next)
                _earcut_linked(a_filtered, triangles, min_x, min_y, inv_size, 0)
                _earcut_linked(c_filtered, triangles, min_x, min_y, inv_size, 0)
                return
            b = b.next
        a = a.next
        if a is start:
            break


def _eliminate_holes(data: list[float], hole_indices: list[int], outer_node: _Node) -> _Node | None:
    queue: list[_Node] = []
    for i, hole_start in enumerate(hole_indices):
        start = hole_start * 2
        end = hole_indices[i + 1] * 2 if i < len(hole_indices) - 1 else len(data)
        lst = _linked_list(data, start, end, False)
        if lst is None:
            continue
        if lst is lst.next:
            lst.steiner = True
        queue.append(_get_leftmost(lst))

    queue.sort(key=lambda node: (node.x, node.y))

    node: _Node | None = outer_node
    for hole in queue:
        node = _eliminate_hole(hole, node)
        if node is None:
            return None
    return node


def _eliminate_hole(hole: _Node, outer_node: _Node | None) -> _Node | None:
    if outer_node is None:
        return None
    bridge = _find_hole_bridge(hole, outer_node)
    if bridge is None:
        return outer_node

    bridge_reverse = _split_polygon(bridge, hole)
    _filter_points(bridge_reverse, bridge_reverse.next)
    return _filter_points(bridge, bridge.next)


def _find_hole_bridge(hole: _Node, outer_node: _Node) -> _Node | None:
    p = outer_node
    hx = hole.x
    hy = hole.y
    qx = -math.inf
    m: _Node | None = None

    while True:
        assert p.next is not None
        if p.y >= hy >= p.next.y and p.next.y != p.y:
            x = p.x + (hy - p.y) * (p.next.x - p.x) / (p.next.y - p.y)
            if hx >= x > qx:
                qx = x
                m = p if p.x < p.next.x else p.next
                if x == hx:
                    return m
        p = p.next
        if p is outer_node:
            break

    if m is None:
        return None

    stop = m
    mx = m.x
    my = m.y
    tan_min = math.inf

    p = m
    while True:
        if (
            hx >= p.x >= mx
            and hx != p.x
            and _point_in_triangle(
                hx if hy < my else qx,
                hy,
                mx,
                my,
                qx if hy < my else hx,
                hy,
                p.x,
                p.y,
            )
        ):
            tan = abs(hy - p.y) / (hx - p.x)
            if _locally_inside(p, hole) and (
                tan < tan_min
                or (
                    tan == tan_min and (p.x > m.x or (p.x == m.x and _sector_contains_sector(m, p)))
                )
            ):
                m = p
                tan_min = tan
        assert p.next is not None
        p = p.next
        if p is stop:
            break
    return m


def _sector_contains_sector(m: _Node, p: _Node) -> bool:
    assert m.prev is not None and m.next is not None
    return _area(m.prev, m, p.prev) < 0.0 and _area(p.next, m, m.next) < 0.0  # type: ignore[arg-type]


def _index_curve(start: _Node, min_x: float, min_y: float, inv_size: float) -> None:
    p = start
    while True:
        if p.z == 0:
            p.z = _z_order(p.x, p.y, min_x, min_y, inv_size)
        p.prev_z = p.prev
        p.next_z = p.next
        assert p.next is not None
        p = p.next
        if p is start:
            break

    assert p.prev_z is not None
    p.prev_z.next_z = None
    p.prev_z = None
    _sort_linked(p)


def _sort_linked(head: _Node | None) -> _Node | None:
    in_size = 1
    while True:
        p = head
        head = None
        tail: _Node | None = None
        num_merges = 0

        while p is not None:
            num_merges += 1
            q: _Node | None = p
            p_size = 0
            for _ in range(in_size):
                p_size += 1
                assert q is not None
                q = q.next_z
                if q is None:
                    break
            q_size = in_size

            while p_size > 0 or (q_size > 0 and q is not None):
                if p_size != 0 and (q_size == 0 or q is None or (p is not None and p.z <= q.z)):
                    assert p is not None
                    e = p
                    p = p.next_z
                    p_size -= 1
                else:
                    assert q is not None
                    e = q
                    q = q.next_z
                    q_size -= 1

                if tail is not None:
                    tail.next_z = e
                else:
                    head = e
                e.prev_z = tail
                tail = e
            p = q

        assert tail is not None
        tail.next_z = None
        in_size *= 2
        if num_merges <= 1:
            return head


def _z_order(x: float, y: float, min_x: float, min_y: float, inv_size: float) -> int:
    ix = int((x - min_x) * inv_size)
    iy = int((y - min_y) * inv_size)

    ix = (ix | (ix << 8)) & 0x00FF00FF
    ix = (ix | (ix << 4)) & 0x0F0F0F0F
    ix = (ix | (ix << 2)) & 0x33333333
    ix = (ix | (ix << 1)) & 0x55555555

    iy = (iy | (iy << 8)) & 0x00FF00FF
    iy = (iy | (iy << 4)) & 0x0F0F0F0F
    iy = (iy | (iy << 2)) & 0x33333333
    iy = (iy | (iy << 1)) & 0x55555555

    return ix | (iy << 1)


def _get_leftmost(start: _Node) -> _Node:
    p = start
    leftmost = start
    while True:
        if p.x < leftmost.x or (p.x == leftmost.x and p.y < leftmost.y):
            leftmost = p
        assert p.next is not None
        p = p.next
        if p is start:
            break
    return leftmost


def _point_in_triangle(
    ax: float,
    ay: float,
    bx: float,
    by: float,
    cx: float,
    cy: float,
    px: float,
    py: float,
) -> bool:
    return (
        (cx - px) * (ay - py) >= (ax - px) * (cy - py)
        and (ax - px) * (by - py) >= (bx - px) * (ay - py)
        and (bx - px) * (cy - py) >= (cx - px) * (by - py)
    )


def _is_valid_diagonal(a: _Node, b: _Node) -> bool:
    assert a.next is not None and a.prev is not None
    assert b.next is not None and b.prev is not None
    return (
        b.i not in (a.next.i, a.prev.i)
        and not _intersects_polygon(a, b)
        and (
            (
                _locally_inside(a, b)
                and _locally_inside(b, a)
                and _middle_inside(a, b)
                and (_area(a.prev, a, b.prev) != 0.0 or _area(a, b.prev, b) != 0.0)
            )
            or (_equals(a, b) and _area(a.prev, a, a.next) > 0.0 and _area(b.prev, b, b.next) > 0.0)
        )
    )


def _area(p: _Node, q: _Node, r: _Node) -> float:
    return (q.y - p.y) * (r.x - q.x) - (q.x - p.x) * (r.y - q.y)


def _equals(p1: _Node, p2: _Node) -> bool:
    return p1.x == p2.x and p1.y == p2.y


def _intersects(p1: _Node, q1: _Node, p2: _Node | None, q2: _Node) -> bool:
    assert p2 is not None
    o1 = _sign(_area(p1, q1, p2))
    o2 = _sign(_area(p1, q1, q2))
    o3 = _sign(_area(p2, q2, p1))
    o4 = _sign(_area(p2, q2, q1))

    if o1 != o2 and o3 != o4:
        return True
    if o1 == 0 and _on_segment(p1, p2, q1):
        return True
    if o2 == 0 and _on_segment(p1, q2, q1):
        return True
    if o3 == 0 and _on_segment(p2, p1, q2):
        return True
    return bool(o4 == 0 and _on_segment(p2, q1, q2))


def _on_segment(p: _Node, q: _Node, r: _Node) -> bool:
    return min(p.x, r.x) <= q.x <= max(p.x, r.x) and min(p.y, r.y) <= q.y <= max(p.y, r.y)


def _sign(num: float) -> int:
    if num > 0:
        return 1
    if num < 0:
        return -1
    return 0


def _intersects_polygon(a: _Node, b: _Node) -> bool:
    p = a
    while True:
        assert p.next is not None
        if (
            a.i not in (p.i, p.next.i)
            and b.i not in (p.i, p.next.i)
            and _intersects(p, p.next, a, b)
        ):
            return True
        p = p.next
        if p is a:
            break
    return False


def _locally_inside(a: _Node, b: _Node) -> bool:
    assert a.prev is not None and a.next is not None
    if _area(a.prev, a, a.next) < 0.0:
        return _area(a, b, a.next) >= 0.0 and _area(a, a.prev, b) >= 0.0
    return _area(a, b, a.prev) < 0.0 or _area(a, a.next, b) < 0.0


def _middle_inside(a: _Node, b: _Node) -> bool:
    p = a
    inside = False
    px = (a.x + b.x) / 2.0
    py = (a.y + b.y) / 2.0
    while True:
        assert p.next is not None
        if (
            ((p.y > py) != (p.next.y > py))
            and p.next.y != p.y
            and px < (p.next.x - p.x) * (py - p.y) / (p.next.y - p.y) + p.x
        ):
            inside = not inside
        p = p.next
        if p is a:
            break
    return inside


def _split_polygon(a: _Node, b: _Node) -> _Node:
    a2 = _Node(a.i, a.x, a.y)
    b2 = _Node(b.i, b.x, b.y)
    an = a.next
    bp = b.prev
    assert an is not None and bp is not None

    a.next = b
    b.prev = a

    a2.next = an
    an.prev = a2

    b2.next = a2
    a2.prev = b2

    bp.next = b2
    b2.prev = bp

    return b2


def _insert_node(i: int, x: float, y: float, last: _Node | None) -> _Node:
    p = _Node(i, x, y)
    if last is None:
        p.prev = p
        p.next = p
    else:
        assert last.next is not None
        p.next = last.next
        p.prev = last
        last.next.prev = p
        last.next = p
    return p


def _remove_node(p: _Node) -> None:
    assert p.next is not None and p.prev is not None
    p.next.prev = p.prev
    p.prev.next = p.next
    if p.prev_z is not None:
        p.prev_z.next_z = p.next_z
    if p.next_z is not None:
        p.next_z.prev_z = p.prev_z


def _signed_area(data: list[float], start: int, end: int) -> float:
    total = 0.0
    j = end - 2
    for i in range(start, end, 2):
        total += (data[j] - data[i]) * (data[i + 1] + data[j + 1])
        j = i
    return total
