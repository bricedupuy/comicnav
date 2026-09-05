import asyncio

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import metadata
from app.projects import _metadata_payload
from app.schemas import ProjectMetadata


# Synthetic fixtures follow GCD's IssueOnly/Issue/Series serializers.
ISSUE = {
    "api_url": "https://www.comics.org/api/issue/123/", "series_name": "Example (1990 series)",
    "series": "https://www.comics.org/api/series/12/", "number": "25", "title": "An example",
    "key_date": "2025-06-00", "page_count": "48.000", "variant_name": "Documented edition",
    "story_set": [
        {"type": "cover", "pencils": "Cover Artist", "script": "None"},
        {"type": "comic story", "script": "Writer", "pencils": "Artist", "colors": "?"},
        {"type": "advertisement", "script": "Advertiser"},
    ],
}
SERIES = {"name": "Example", "language": "fr", "publisher": "https://www.comics.org/api/publisher/1/"}


def app_client(monkeypatch, handler):
    monkeypatch.setattr(metadata.settings, "gcd_username", "")
    monkeypatch.setattr(metadata.settings, "gcd_password", "")
    monkeypatch.setattr(metadata, "client", metadata.GCDClient(httpx.MockTransport(handler), interval=0))
    app = FastAPI()
    app.include_router(metadata.router)
    return TestClient(app)


def test_search_pagination_and_details_provenance_round_trip(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        assert request.url.host == "www.comics.org"
        assert not request.headers.get("Authorization")
        if "/series/name/" in request.url.path:
            assert "year/2025/" in request.url.path
            return httpx.Response(200, json={"results": [{"api_url": ISSUE["api_url"], "descriptor": "Example #25"}],
                                             "next": "https://untrusted.test/do-not-follow"})
        objects = {"/api/issue/123/": ISSUE, "/api/series/12/": SERIES, "/api/publisher/1/": {"name": "Publisher"}}
        return httpx.Response(200, json=objects[request.url.path])

    with app_client(monkeypatch, handler) as api:
        search = api.get("/v1/metadata/gcd/search", params={"series": "Example", "number": "25", "year": 2025})
        assert search.status_code == 200
        assert search.json()["next_page"] == 2
        assert search.json()["candidates"][0]["id"] == 123
        result = api.get("/v1/metadata/gcd/issues/123")
        assert result.status_code == 200
        detail = result.json()
        fields = detail["fields"]
        assert fields["series"] == "Example"
        assert fields["language_iso"] == "fr"
        assert fields["page_count"] == 48
        assert fields["writer"] == "Writer"  # No cover/ad credits merged into story credits.
        assert fields["cover_artist"] == "Cover Artist"
        assert "day" not in fields and "colorist" not in fields
        assert "release_group" not in fields
        # Same detail served from cache, including the original retrieval timestamp.
        assert api.get("/v1/metadata/gcd/issues/123").json() == detail
        assert len(requests) == 4

    imported = ProjectMetadata(
        **fields, sources={key: "gcd" for key in fields},
        provider_records={detail["record_id"]: detail["record"]},
        field_provenance={key: {"record_id": detail["record_id"], "original_value": value} for key, value in fields.items()},
    )
    stored = _metadata_payload(imported, 50)
    restored = ProjectMetadata.model_validate_json(ProjectMetadata.model_validate(stored).model_dump_json())
    assert restored.field_provenance["title"].original_value == "An example"
    assert restored.provider_records[detail["record_id"]].raw["issue"] == ISSUE
    assert stored["warnings"] == ["Metadata declares 48 pages, but this project contains 50."]
    # Manually changing an imported field invalidates its active provider evidence.
    restored.sources["title"] = "manual"
    assert "title" not in _metadata_payload(restored, 50)["field_provenance"]


@pytest.mark.parametrize("status,expected", [(401,503),(403,503),(404,404),(500,502),(302,502)])
def test_upstream_errors_are_safe_and_not_cached(monkeypatch, status, expected):
    def handler(request):
        return httpx.Response(status, headers={"Location": "https://untrusted.test/"}, text="private upstream message")
    with app_client(monkeypatch, handler) as api:
        response = api.get("/v1/metadata/gcd/issues/123")
        assert response.status_code == expected
        assert "private upstream message" not in response.text
        assert not metadata.client.cache


def test_rate_limit_cooldown_prevents_another_upstream_call(monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "120"})
    with app_client(monkeypatch, handler) as api:
        for _ in range(2):
            response = api.get("/v1/metadata/gcd/issues/123")
            assert response.status_code == 429
            assert int(response.headers["retry-after"]) > 0
        assert len(calls) == 1


def test_timeout_invalid_json_and_empty_search(monkeypatch):
    for handler, status in [
        (lambda request: httpx.Response(200, text="not json"), 502),
        (lambda request: httpx.Response(200, json={"unexpected": []}), 502),
        (lambda request: httpx.Response(200, json={"results": [], "next": None}), 200),
    ]:
        with app_client(monkeypatch, handler) as api:
            response = api.get("/v1/metadata/gcd/search?series=Example&number=25")
            assert response.status_code == status
            if status == 200:
                assert response.json()["candidates"] == []
    def timeout(request):
        raise httpx.ReadTimeout("secret debug info", request=request)
    with app_client(monkeypatch, timeout) as api:
        assert api.get("/v1/metadata/gcd/issues/123").status_code == 504


def test_basic_auth_is_server_only(monkeypatch):
    def handler(request):
        assert request.headers["authorization"].startswith("Basic ")
        return httpx.Response(200, json=[])
    with app_client(monkeypatch, handler) as api:
        monkeypatch.setattr(metadata.settings, "gcd_username", "test-user")
        monkeypatch.setattr(metadata.settings, "gcd_password", "test-password")
        response = api.get("/v1/metadata/gcd/search?series=Example&number=25")
        assert response.status_code == 200
        assert "test-password" not in response.text


def test_foreign_resource_urls_not_followed(monkeypatch):
    malicious = {**ISSUE, "series": "https://example.test/api/series/12/"}
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=malicious)
    with app_client(monkeypatch, handler) as api:
        assert api.get("/v1/metadata/gcd/issues/123").status_code == 200
        assert len(calls) == 1


def test_duplicate_concurrent_requests_are_coalesced(monkeypatch):
    monkeypatch.setattr(metadata.settings, "gcd_username", "")
    monkeypatch.setattr(metadata.settings, "gcd_password", "")
    calls = []
    async def handler(request):
        calls.append(request)
        await asyncio.sleep(0)
        return httpx.Response(200, json=ISSUE)
    async def run():
        client = metadata.GCDClient(httpx.MockTransport(handler), interval=0)
        return await asyncio.gather(client.get("issue/123/"), client.get("issue/123/"))
    results = asyncio.run(run())
    assert results[0] == results[1]
    assert len(calls) == 1


@pytest.mark.parametrize("language,expected", [("fr", [101, 102]), ("FR-be", [101, 102]), ("nl", [103]), (None, [101, 102, 103, 104])])
def test_search_language_filter_and_cached_series(monkeypatch, language, expected):
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if "/series/name/" in request.url.path:
            assert "language" not in request.url.params  # Not supported upstream.
            if request.url.params.get("page") == "2":
                return httpx.Response(200, json={"results": [{"id": 105, "series": 12}], "next": None})
            return httpx.Response(200, json={"results": [
                {"id": 101, "series": 12}, {"id": 102, "series": 12},
                {"id": 103, "series": "https://www.comics.org/api/series/13/"},
                {"id": 104, "series": "https://untrusted.test/api/series/14/"},
            ], "next": "upstream page two"})
        return httpx.Response(200, json={"language": "fr" if request.url.path == "/api/series/12/" else "nl"})

    with app_client(monkeypatch, handler) as api:
        params = {"series": "Example", "number": "25"}
        if language:
            params["language"] = language
        response = api.get("/v1/metadata/gcd/search", params=params)
        assert response.status_code == 200
        data = response.json()
        assert [c["id"] for c in data["candidates"]] == expected
        assert data["filtered_count"] == 4 - len(expected)
        assert data["unknown_language_count"] == 1
        assert data["next_page"] == 2
        assert calls.count("/api/series/12/") == 1
        assert len(calls) == 3
        if language is None:
            assert data["candidates"][-1]["language_iso"] is None
        params["page"] = 2
        second = api.get("/v1/metadata/gcd/search", params=params).json()
        assert second["next_page"] is None
        assert calls.count("/api/series/12/") == 1


def test_language_filtered_empty_page_keeps_pagination(monkeypatch):
    def handler(request):
        if "/series/name/" in request.url.path:
            return httpx.Response(200, json={"results": [{"id": 123, "series": 12}], "next": "page2"})
        return httpx.Response(200, json={"language": "nl"})
    with app_client(monkeypatch, handler) as api:
        result = api.get("/v1/metadata/gcd/search?series=Example&number=25&language=fr").json()
        assert result["candidates"] == []
        assert result["next_page"] == 2
        assert result["filtered_count"] == 1
        assert api.get("/v1/metadata/gcd/search?series=Example&number=25&language=French").status_code == 422
