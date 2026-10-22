"""Smooth distance samples without changing the saved contour or its endpoints."""

import math

import numpy as np


def smooth_samples(points, distances, closed, amount):
    if amount <= 0:
        return points, distances
    count = len(points) - 1
    uniform = np.linspace(0, distances[-1], count + 1)
    samples = np.column_stack([np.interp(uniform, distances, points[:, axis]) for axis in (0, 1)])
    sigma = max(0.15, count * 0.02 * amount / 100)
    radius = max(1, math.ceil(sigma * 3))
    positions = np.arange(-radius, radius + 1)
    kernel = np.exp(-0.5 * (positions / sigma) ** 2)
    kernel /= kernel.sum()
    values = samples[:-1] if closed else samples
    padded = np.pad(values, ((radius, radius), (0, 0)), mode="wrap" if closed else "edge")
    smooth = np.column_stack(
        [np.convolve(padded[:, axis], kernel, mode="valid") for axis in (0, 1)]
    )
    if closed:
        smooth = np.vstack((smooth, smooth[0]))
    else:
        t = np.linspace(0, 1, len(smooth))[:, None]
        smooth += (1 - t) * (points[0] - smooth[0]) + t * (points[-1] - smooth[-1])
        smooth[0], smooth[-1] = points[0], points[-1]
    cumulative = np.concatenate(([0.0], np.cumsum(np.linalg.norm(np.diff(smooth, axis=0), axis=1))))
    return (smooth, cumulative) if cumulative[-1] > 1e-6 else (points, distances)
