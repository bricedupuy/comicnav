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
