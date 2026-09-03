from app.projects import _metadata_payload
from app.schemas import ProjectMetadata


def test_metadata_keeps_valid_provenance_and_recomputes_page_count_warning():
    metadata = ProjectMetadata(
        series="Largo Winch",
        number="25",
        language_iso="fr",
        page_count=54,
        release_group="TONER",
        release_type="hybrid",
        release_tags=["upscale", "upscale", "re-encode"],
        edition_label="Edition Documentée",
        release_revision="v2",
        comicinfo_path="ComicInfo.xml",
        sources={
            "series": "comicinfo.xml",
            "number": "comicinfo.xml",
            "language_iso": "comicinfo.xml",
            "page_count": "comicinfo.xml",
            "release_group": "manual",
            "release_type": "manual",
            "release_tags": "manual",
            "edition_label": "manual",
            "release_revision": "manual",
            "not_a_metadata_field": "manual",
        },
        warnings=["Untrusted client warning"],
    )

    result = _metadata_payload(metadata, actual_page_count=56)

    assert result["series"] == "Largo Winch"
    assert result["sources"] == {
        "series": "comicinfo.xml",
        "number": "comicinfo.xml",
        "language_iso": "comicinfo.xml",
        "page_count": "comicinfo.xml",
        "release_group": "manual",
        "release_type": "manual",
        "release_tags": "manual",
        "edition_label": "manual",
        "release_revision": "manual",
    }
    assert result["release_tags"] == ["upscale", "re-encode"]
    assert result["warnings"] == ["ComicInfo.xml declares 54 pages, but this project contains 56."]
