from __future__ import annotations

from dataclasses import dataclass


@dataclass
class OrderablePanel:
    index: int
    x: float
    y: float
    w: float
    h: float


def _row_groups(panels: list[OrderablePanel]) -> list[list[OrderablePanel]]:
    """Simple European-comics heuristic: group by meaningful vertical overlap, then rows."""
    remaining = sorted(panels, key=lambda p: (p.y, p.x))
    rows: list[list[OrderablePanel]] = []
    while remaining:
        seed = remaining.pop(0)
        row = [seed]
        row_top = seed.y
        row_bottom = seed.y + seed.h
        keep: list[OrderablePanel] = []
        for p in remaining:
            overlap = max(0.0, min(row_bottom, p.y + p.h) - max(row_top, p.y))
            min_h = max(1.0, min(seed.h, p.h))
            if overlap / min_h >= 0.35:
                row.append(p)
                row_top = min(row_top, p.y)
                row_bottom = max(row_bottom, p.y + p.h)
            else:
                keep.append(p)
        rows.append(row)
        remaining = keep
    return rows


def reading_order(panels: list[OrderablePanel], direction: str = "ltr") -> list[int]:
    rows = _row_groups(panels)
    ordered: list[int] = []
    for row in rows:
        row.sort(key=lambda p: p.x, reverse=(direction == "rtl"))
        ordered.extend(p.index for p in row)
    return ordered
