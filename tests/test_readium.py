from app.readium import guided_document, points_fragment, xywh_fragment


def test_xywh_fragment():
    assert xywh_fragment([7.412, 9.732, 55.339, 40.474]) == "#xywh=percent:7.412,9.732,55.339,40.474"


def test_points_fragment():
    points = [[34.314, 71.061], [58.49, 71.267], [58.629, 82.441]]
    assert points_fragment(points) == "#points=percent:34.314,71.061 58.49,71.267 58.629,82.441"


def test_guided_document():
    doc = guided_document(
        "page_0003.jpg",
        "page_0003.guided.json",
        [{"geometry": {"type": "rectangle", "bbox_percent": [1, 2, 3, 4], "polygon_percent": None}}],
    )
    assert doc["guided"][0]["imgref"] == "page_0003.jpg#xywh=percent:1,2,3,4"
    assert doc["links"][0]["type"] == "application/guided-navigation+json"
