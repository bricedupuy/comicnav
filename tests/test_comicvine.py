import asyncio
import logging

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import comicvine
from app.projects import _metadata_payload
from app.schemas import ProjectMetadata


KEY = "synthetic-api-key-not-a-real-secret"
ISSUE = {"id": 123, "issue_number": "25", "name": "A title", "cover_date": "2025-09-00",
         "volume": {"id": 12, "name": "Example"}, "site_detail_url": "https://comicvine.gamespot.com/a-title/4000-123/",
         "person_credits": [{"name": "Artist", "role": "penciler, inker, cover"}, {"name": "Writer", "role": "writer"}]}
VOLUME = {"id": 12, "name": "Example", "publisher": {"id": 1, "name": "Publisher"}}


def envelope(results, total=1, status=1):
    return {"status_code": status, "results": results, "number_of_total_results": total}


def app_client(monkeypatch, handler):
    monkeypatch.setattr(comicvine.settings, "comicvine_api_key", KEY)
    monkeypatch.setattr(comicvine, "client", comicvine.ComicVineClient(httpx.MockTransport(handler), interval=0))
    app = FastAPI()
    app.include_router(comicvine.router)
    return TestClient(app)


def test_search_volume_then_issues_and_details(monkeypatch, caplog):
    calls = []
    def handler(request):
        calls.append(request)
        assert request.url.host == "comicvine.gamespot.com"
        assert request.url.params["api_key"] == KEY
        assert request.url.params["format"] == "json"
        if request.url.path == "/api/search/":
            assert request.url.params["resources"] == "volume"
            assert request.url.params["page"] == "2"
            assert request.url.params["query"] == "Example"
            assert "offset" not in request.url.params
            return httpx.Response(200, json=envelope([VOLUME], total=41))
        if request.url.path == "/api/issues/":
            assert request.url.params["filter"] == "volume:12,issue_number:25,cover_date:2025-01-01|2025-12-31"
            assert request.url.params["offset"] == "20"
            assert "page" not in request.url.params
            return httpx.Response(200, json=envelope([ISSUE], total=21))
        return httpx.Response(200, json=envelope(ISSUE if "/issue/" in request.url.path else VOLUME))
    with caplog.at_level(logging.INFO, logger="httpx"), app_client(monkeypatch, handler) as api:
        params = {"series": "Example", "number": "25", "page": 2, "year": 2025, "language": "fr"}
        search = api.get("/v1/metadata/comicvine/search", params=params)
        assert search.status_code == 200
        result = search.json()
        assert result["stage"] == "volumes" and result["next_page"] == 3
        assert result["language_filter_supported"] is False
        assert result["language"] is None
        assert "not language-filtered" in result["notice"]
        assert result["candidates"][0]["kind"] == "volume"
        assert "Publisher" in result["candidates"][0]["label"]
        result = api.get("/v1/metadata/comicvine/search", params={**params, "volume_id": 12}).json()
        assert result["candidates"][0]["kind"] == "issue" and result["next_page"] is None
        response = api.get("/v1/metadata/comicvine/issues/123")
        assert response.status_code == 200
        detail = response.json()
        assert api.get("/v1/metadata/comicvine/issues/123").json() == detail
        assert len(calls) == 4
        assert KEY not in response.text
    assert KEY not in caplog.text
    assert "api_key=[REDACTED]" in caplog.text
    fields = detail["fields"]
    assert fields["publisher"] == "Publisher"
    assert fields["penciller"] == fields["inker"] == fields["cover_artist"] == "Artist"
    assert fields["writer"] == "Writer"
    assert fields["year"] == 2025
    assert "day" not in fields and "language_iso" not in fields and "release_group" not in fields
    assert detail["record"]["source_url"] == ISSUE["site_detail_url"]
    imported = ProjectMetadata(**fields, language_iso="fr", sources={**{k:"comicvine" for k in fields},"language_iso":"manual"},
        provider_records={detail["record_id"]:detail["record"]},
        field_provenance={k:{"record_id":detail["record_id"],"original_value":v} for k,v in fields.items()})
    stored = ProjectMetadata.model_validate(_metadata_payload(imported, 48))
    assert stored.language_iso == "fr"
    assert stored.provider_records[detail["record_id"]].provider == "comicvine"
    assert stored.field_provenance["title"].original_value == "A title"
    stored.sources["title"] = "manual"
    assert "title" not in _metadata_payload(stored, 48)["field_provenance"]


@pytest.mark.parametrize("http_status,api_status,expected", [(401,1,503),(403,1,503),(404,1,404),(500,1,502),
    (302,1,502),(200,100,503),(200,101,404),(200,104,502),(200,107,429),(429,1,429)])
def test_safe_errors(monkeypatch, http_status, api_status, expected):
    def handler(request):
        return httpx.Response(http_status, json={**envelope([],status=api_status),"error":KEY},
                              headers={"Location":"https://untrusted.test", "Retry-After":"120"})
    with app_client(monkeypatch, handler) as api:
        result = api.get("/v1/metadata/comicvine/issues/123")
        assert result.status_code == expected
        assert KEY not in result.text
        assert not comicvine.client.cache


def test_missing_key_and_filter_injection_make_no_requests(monkeypatch):
    def handler(request):
        pytest.fail("No upstream request expected")
    with app_client(monkeypatch, handler) as api:
        monkeypatch.setattr(comicvine.settings, "comicvine_api_key", "")
        assert api.get("/v1/metadata/comicvine/issues/123").status_code == 503
        assert api.get("/v1/metadata/comicvine/search",params={"series":"Example","number":"25,volume:1"}).status_code == 422
        assert api.get("/v1/metadata/comicvine/issues/0").status_code == 422


def test_cooldown_and_quota(monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After":"120"})
    with app_client(monkeypatch, handler) as api:
        for _ in range(2):
            assert api.get("/v1/metadata/comicvine/issues/123").status_code == 429
        assert len(calls) == 1
        comicvine.client.requests["issue"].extend([comicvine.monotonic()] * 180)
        result = api.get("/v1/metadata/comicvine/issues/124")
        assert result.status_code == 429 and "hourly" in result.text


def test_unreadable_or_malformed_responses(monkeypatch):
    for response in [httpx.Response(200,text="not json"),httpx.Response(200,json=[]),
                     httpx.Response(200,json=envelope({})),httpx.Response(200,json=envelope([],total="one"))]:
        with app_client(monkeypatch, lambda request: response) as api:
            assert api.get("/v1/metadata/comicvine/search?series=Example&number=25").status_code == 502
    def timeout(request):
        raise httpx.ReadTimeout(KEY,request=request)
    with app_client(monkeypatch, timeout) as api:
        result = api.get("/v1/metadata/comicvine/issues/123")
        assert result.status_code == 504 and KEY not in result.text


def test_no_remote_urls_followed_and_no_key_in_provenance(monkeypatch):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200,json=envelope({**ISSUE,"name":KEY,"site_detail_url":"https://untrusted.test/",
            "volume":{"api_detail_url":"https://untrusted.test/api/volume/1/"}}))
    with app_client(monkeypatch, handler) as api:
        result = api.get("/v1/metadata/comicvine/issues/123")
        assert result.status_code == 200 and KEY not in result.text
        assert result.json()["record"]["source_url"] == "https://comicvine.gamespot.com/issue/4000-123/"
        assert len(calls) == 1


def test_concurrent_cache_and_key_rotation(monkeypatch):
    calls = []
    async def handler(request):
        calls.append(request)
        await asyncio.sleep(0)
        return httpx.Response(200,json=envelope(ISSUE))
    monkeypatch.setattr(comicvine.settings,"comicvine_api_key",KEY)
    async def run():
        client = comicvine.ComicVineClient(httpx.MockTransport(handler),interval=0)
        results = await asyncio.gather(client.get("issue/4000-123/",{}),client.get("issue/4000-123/",{}))
        assert results[0] == results[1] and len(calls) == 1
        monkeypatch.setattr(comicvine.settings,"comicvine_api_key","another-synthetic-key")
        await client.get("issue/4000-123/",{})
        assert len(calls) == 2
    asyncio.run(run())
