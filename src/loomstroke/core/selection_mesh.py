"""Piecewise projective sampling for a raster selection mesh."""

from __future__ import annotations

import math

import cv2
import numpy as np
from numpy.typing import NDArray

from .selection_clipboard import SelectionPayload
from .selection_projective import Quad, _image_array, _mask_array, valid_quad

Mesh = tuple[tuple[float, float], ...]


def regular_mesh(bounds: tuple[int, int, int, int], divisions: int = 2) -> Mesh:
    if not 1 <= divisions <= 10:
        raise ValueError("Mesh divisions must be between 1 and 10")
    x, y, width, height = bounds
    return tuple(
        (float(x + width * col / divisions), float(y + height * row / divisions))
        for row in range(divisions + 1)
        for col in range(divisions + 1)
    )


def mesh_divisions(mesh: Mesh) -> int:
    side = math.isqrt(len(mesh))
    return side - 1 if side * side == len(mesh) and 2 <= side <= 11 else 0


def resample_mesh(mesh: Mesh, divisions: int) -> Mesh | None:
    """Interpolate an existing mesh onto a different control grid."""
    old = mesh_divisions(mesh)
    if old == 0 or not 1 <= divisions <= 10 or not valid_mesh(mesh):
        return None
    if old == divisions:
        return mesh
    side = old + 1
    result = []
    for row in range(divisions + 1):
        v = row * old / divisions
        source_row = min(old - 1, math.floor(v))
        fy = v - source_row
        for col in range(divisions + 1):
            u = col * old / divisions
            source_col = min(old - 1, math.floor(u))
            fx = u - source_col
            a = mesh[source_row * side + source_col]
            b = mesh[source_row * side + source_col + 1]
            c = mesh[(source_row + 1) * side + source_col]
            d = mesh[(source_row + 1) * side + source_col + 1]
            result.append(
                tuple(
                    (1 - fy) * ((1 - fx) * a[i] + fx * b[i]) + fy * ((1 - fx) * c[i] + fx * d[i])
                    for i in range(2)
                )
            )
    sampled = tuple(result)
    return sampled if valid_mesh(sampled) else None


def _cell(mesh: Mesh, row: int, col: int, side: int) -> Quad:
    return (
        mesh[row * side + col],
        mesh[row * side + col + 1],
        mesh[(row + 1) * side + col + 1],
        mesh[(row + 1) * side + col],
    )


def valid_mesh(mesh: Mesh) -> bool:
    divisions = mesh_divisions(mesh)
    if divisions == 0 or not all(math.isfinite(value) for point in mesh for value in point):
        return False
    signs = []
    for row in range(divisions):
        for col in range(divisions):
            cell = _cell(mesh, row, col, divisions + 1)
            if not valid_quad(cell):
                return False
            first, second, _, fourth = cell
            cross = (second[0] - first[0]) * (fourth[1] - first[1]) - (second[1] - first[1]) * (
                fourth[0] - first[0]
            )
            signs.append(cross > 0)
    return all(sign == signs[0] for sign in signs)


def warp_mesh_payload(
    payload: SelectionPayload,
    mesh: Mesh,
    *,
    premultiplied: bool,
    mask_mode: str,
    canvas_size: tuple[int, int] | None = None,
) -> tuple[NDArray[np.uint8], NDArray[np.uint8], tuple[int, int, int, int]] | None:
    """Sample each cell into one image and mask patch in canvas coordinates."""
    if not valid_mesh(mesh):
        return None
    image, mask = _image_array(payload, premultiplied=premultiplied), _mask_array(payload)
    if image is None or mask is None or image.shape[:2] != mask.shape:
        return None
    height, width = mask.shape
    if width < 1 or height < 1:
        return None
    points = np.asarray(mesh, dtype=np.float32)
    if not np.isfinite(points).all():
        return None
    x0, y0 = math.floor(float(points[:, 0].min())), math.floor(float(points[:, 1].min()))
    x1, y1 = math.ceil(float(points[:, 0].max())), math.ceil(float(points[:, 1].max()))
    if canvas_size is not None:
        x0, y0 = max(x0, 0), max(y0, 0)
        x1, y1 = min(x1, canvas_size[0]), min(y1, canvas_size[1])
    out_width, out_height = x1 - x0, y1 - y0
    if out_width < 1 or out_height < 1 or out_width * out_height > 100_000_000:
        return None
    result_image = np.zeros((out_height, out_width, 4), dtype=np.uint8)
    result_mask = np.zeros((out_height, out_width), dtype=np.uint8)
    divisions = mesh_divisions(mesh)
    for row in range(divisions):
        for col in range(divisions):
            source = np.asarray(
                (
                    (width * col / divisions, height * row / divisions),
                    (width * (col + 1) / divisions, height * row / divisions),
                    (width * (col + 1) / divisions, height * (row + 1) / divisions),
                    (width * col / divisions, height * (row + 1) / divisions),
                ),
                dtype=np.float32,
            )
            dest = np.asarray(_cell(mesh, row, col, divisions + 1), dtype=np.float32)
            left = max(x0, math.floor(float(dest[:, 0].min())))
            top = max(y0, math.floor(float(dest[:, 1].min())))
            right = min(x1, math.ceil(float(dest[:, 0].max())))
            bottom = min(y1, math.ceil(float(dest[:, 1].max())))
            if right <= left or bottom <= top:
                continue
            matrix = cv2.getPerspectiveTransform(source - 0.5, dest - 0.5)
            matrix[0, :] -= left * matrix[2, :]
            matrix[1, :] -= top * matrix[2, :]
            size = (right - left, bottom - top)
            sampled_image = cv2.warpPerspective(image, matrix, size, flags=cv2.INTER_LINEAR)
            sampled_mask = cv2.warpPerspective(
                mask,
                matrix,
                size,
                flags=cv2.INTER_LINEAR if mask_mode == "soft" else cv2.INTER_NEAREST,
            )
            coverage = np.zeros((size[1], size[0]), dtype=np.uint8)
            cv2.fillConvexPoly(coverage, np.rint(dest - (left, top)).astype(np.int32), 255)
            sampled_mask = cv2.bitwise_and(sampled_mask, coverage)
            if mask_mode == "binary":
                sampled_mask = np.where(sampled_mask > 127, 255, 0).astype(np.uint8)
            oy, ox = top - y0, left - x0
            target_mask = result_mask[oy : oy + size[1], ox : ox + size[0]]
            target_image = result_image[oy : oy + size[1], ox : ox + size[0]]
            present = sampled_mask > 0
            target_mask[present] = sampled_mask[present]
            target_image[present] = sampled_image[present]
    return result_image, result_mask, (x0, y0, out_width, out_height)
