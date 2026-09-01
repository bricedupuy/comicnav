from __future__ import annotations

import cv2
import numpy as np


def polygon_area(points: np.ndarray) -> float:
    if len(points) < 3:
        return 0.0
    return float(abs(cv2.contourArea(points.astype(np.float32))))


def bbox_from_polygon(points: np.ndarray) -> tuple[float, float, float, float]:
    xs = points[:, 0]
    ys = points[:, 1]
    x1, y1, x2, y2 = float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max())
    return x1, y1, x2 - x1, y2 - y1


def simplify_polygon(points: np.ndarray, epsilon_ratio: float) -> np.ndarray:
    if len(points) <= 4:
        return points.astype(np.float32)
    contour = points.astype(np.float32).reshape((-1, 1, 2))
    perimeter = cv2.arcLength(contour, True)
    epsilon = max(0.5, perimeter * epsilon_ratio)
    approx = cv2.approxPolyDP(contour, epsilon, True)
    return approx.reshape((-1, 2)).astype(np.float32)


def is_effectively_axis_aligned_rectangle(
    points: np.ndarray,
    bbox: tuple[float, float, float, float],
    fill_threshold: float,
) -> bool:
    x, y, w, h = bbox
    bbox_area = max(1.0, w * h)
    fill = polygon_area(points) / bbox_area
    if fill < fill_threshold:
        return False

    simplified = simplify_polygon(points, 0.02)
    if len(simplified) != 4:
        return False

    tolerance_x = max(2.0, w * 0.03)
    tolerance_y = max(2.0, h * 0.03)
    for px, py in simplified:
        near_x_edge = abs(px - x) <= tolerance_x or abs(px - (x + w)) <= tolerance_x
        near_y_edge = abs(py - y) <= tolerance_y or abs(py - (y + h)) <= tolerance_y
        if not (near_x_edge and near_y_edge):
            return False
    return True


def pct(value: float, total: int, decimals: int) -> float:
    return round((value / total) * 100.0, decimals)


def bbox_percent(bbox: tuple[float, float, float, float], width: int, height: int, decimals: int) -> list[float]:
    x, y, w, h = bbox
    return [pct(x, width, decimals), pct(y, height, decimals), pct(w, width, decimals), pct(h, height, decimals)]


def polygon_percent(points: np.ndarray, width: int, height: int, decimals: int) -> list[list[float]]:
    return [[pct(float(x), width, decimals), pct(float(y), height, decimals)] for x, y in points]
