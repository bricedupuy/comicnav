from app.readium import editor_guided_document, guided_document, points_fragment, xywh_fragment


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


def test_editor_guided_document_preserves_normalized_rectangles_and_polygons():
    doc = editor_guided_document(
        "page_0003.jpg",
        "page_0003.guided.json",
        [
            {"points": [[10, 20], [110, 20], [110, 220], [10, 220]]},
            {"points": [[200, 100], [300, 110], [280, 240]]},
        ],
        width=400,
        height=800,
    )

    assert doc["guided"][0]["imgref"] == "page_0003.jpg#xywh=percent:2.5,2.5,25,25"
    assert doc["guided"][1]["imgref"] == "page_0003.jpg#points=percent:50,12.5 75,13.75 70,30"
