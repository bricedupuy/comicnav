from __future__ import annotations

from urllib.parse import quote


def _fmt(v: float) -> str:
    text = f"{v:.3f}".rstrip("0").rstrip(".")
    return text if text else "0"


def xywh_fragment(bbox_percent: list[float]) -> str:
    return "#xywh=percent:" + ",".join(_fmt(v) for v in bbox_percent)


def points_fragment(points_percent: list[list[float]]) -> str:
    return "#points=percent:" + " ".join(f"{_fmt(x)},{_fmt(y)}" for x, y in points_percent)


def guided_document(image_href: str, self_href: str, ordered_panels: list[dict]) -> dict:
    guided = []
    safe_image_href = quote(image_href, safe="/._-~")
    for panel in ordered_panels:
        geom = panel["geometry"]
        if geom["type"] == "polygon" and geom.get("polygon_percent"):
            fragment = points_fragment(geom["polygon_percent"])
        else:
            fragment = xywh_fragment(geom["bbox_percent"])
        guided.append({"role": ["panel"], "imgref": safe_image_href + fragment})

    return {
        "links": [
            {
                "rel": "self",
                "href": self_href,
                "type": "application/guided-navigation+json",
            }
        ],
        "guided": guided,
    }


def _editor_bbox(points: list[list[float]]) -> tuple[float, float, float, float]:
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    x, y = min(xs), min(ys)
    return x, y, max(xs) - x, max(ys) - y


def _is_editor_rectangle(points: list[list[float]]) -> bool:
    if len(points) != 4:
        return False
    x, y, width, height = _editor_bbox(points)
    tolerance = max(1.0, min(width, height) * 0.005)
    return all(
        (abs(point_x - x) < tolerance or abs(point_x - (x + width)) < tolerance)
        and (abs(point_y - y) < tolerance or abs(point_y - (y + height)) < tolerance)
        for point_x, point_y in points
    )


def editor_guided_document(
    image_href: str,
    self_href: str,
    panels: list[dict],
    width: int,
    height: int,
) -> dict:
    """Convert persisted editor panel points into a Readium guided document."""
    ordered_panels: list[dict] = []
    for panel in panels:
        points = panel.get("points", [])
        if len(points) < 3:
            continue

        x, y, panel_width, panel_height = _editor_bbox(points)
        geometry = {
            "type": "rectangle" if _is_editor_rectangle(points) else "polygon",
            "bbox_percent": [
                x / width * 100,
                y / height * 100,
                panel_width / width * 100,
                panel_height / height * 100,
            ],
            "polygon_percent": None,
        }
        if geometry["type"] == "polygon":
            geometry["polygon_percent"] = [[point_x / width * 100, point_y / height * 100] for point_x, point_y in points]
        ordered_panels.append({"geometry": geometry})

    return guided_document(image_href, self_href, ordered_panels)
