"""GCD metadata suggestions. No project is changed by a lookup."""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from datetime import datetime, timezone
from time import monotonic
from urllib.parse import quote, urlparse
import re
import json

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import ValidationError

from .config import settings
from .schemas import ProjectMetadata

router = APIRouter(prefix="/v1/metadata/gcd", tags=["metadata"])
BASE = "https://www.comics.org/api/"


def resource_id(value: object, resource: str) -> int | None:
    """Only extract IDs from known GCD resource URLs; never follow remote URLs."""
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    if not isinstance(value, str):
        return None
    try:
        url = urlparse(value)
        if url.netloc and (url.hostname != "www.comics.org" or url.scheme != "https" or url.port):
            return None
    except ValueError:
        return None
    match = re.fullmatch(rf"/api/{resource}/([1-9][0-9]*)/?", url.path)
    return int(match[1]) if match else None


class GCDClient:
    def __init__(self, transport=None, interval=1.0):
        self.transport = transport
        self.interval = interval
        self.lock = asyncio.Lock()
        self.next_request = 0.0
        self.cache: OrderedDict[str, tuple[float, object, str]] = OrderedDict()

    async def get(self, path: str) -> tuple[object, str]:
        # Serialize outbound requests and coalesce duplicate lookups through cache.
        async with self.lock:
            now = monotonic()
            cached = self.cache.get(path)
            if cached and cached[0] > now:
                self.cache.move_to_end(path)
                return cached[1], cached[2]
            if self.next_request > now:
                # Reject during a provider cooldown instead of holding requests open.
                if self.next_request - now > 2:
                    raise HTTPException(429, "GCD is rate limited. Please try again later.",
                                        headers={"Retry-After": str(int(self.next_request - now) + 1)})
                await asyncio.sleep(self.next_request - now)
            username, password = settings.gcd_username, settings.gcd_password
            if bool(username) != bool(password):
                raise HTTPException(503, "Configure both GCD_USERNAME and GCD_PASSWORD on the server.")
            auth = httpx.BasicAuth(username, password) if username else None
            self.next_request = monotonic() + self.interval
            try:
                async with httpx.AsyncClient(transport=self.transport, timeout=15, follow_redirects=False) as client:
                    async with client.stream("GET", BASE + path, auth=auth,
                                             headers={"Accept": "application/json", "User-Agent": "ComicNav/0.1 (metadata lookup)"}) as response:
                        if response.status_code == 429:
                            retry = response.headers.get("Retry-After", "60")
                            seconds = min(max(int(retry), 1), 3600) if retry.isdigit() else 60
                            self.next_request = monotonic() + seconds
                            raise HTTPException(429, "GCD request limit reached. Please try again later.",
                                                headers={"Retry-After": str(seconds)})
                        if response.status_code in (401, 403):
                            raise HTTPException(503, "GCD denied access. Check the server's GCD account settings.")
                        if response.status_code == 404:
                            raise HTTPException(404, "GCD record not found.")
                        if not 200 <= response.status_code < 300:
                            raise HTTPException(502, "GCD is temporarily unavailable.")
                        raw = bytearray()
                        async for chunk in response.aiter_bytes():
                            raw.extend(chunk)
                            if len(raw) > 2_000_000:
                                raise HTTPException(502, "GCD response exceeded the size limit. Narrow your search.")
                        data = json.loads(raw)
            except httpx.TimeoutException as exc:
                raise HTTPException(504, "GCD lookup timed out. Please retry.") from exc
            except (httpx.HTTPError, ValueError) as exc:
                raise HTTPException(502, "GCD returned an unreadable response.") from exc
            fetched = datetime.now(timezone.utc).isoformat()
            self.cache[path] = (monotonic() + 3600, data, fetched)
            self.cache.move_to_end(path)
            while len(self.cache) > 32:
                self.cache.popitem(last=False)
            return data, fetched


client = GCDClient()


def clean(value: object) -> str:
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return ""
    text = str(value).strip()
    return "" if text.lower() in ("none", "unknown", "?") else text


def normalize_issue(issue: dict, series: dict, publisher: dict) -> dict:
    values = {
        "series": clean(series.get("name")) or clean(issue.get("series_name")),
        "number": clean(issue.get("number")), "volume": clean(issue.get("volume")),
        "title": clean(issue.get("title")), "edition_label": clean(issue.get("variant_name")),
        "publisher": clean(publisher.get("name")) or clean(issue.get("indicia_publisher")),
        "language_iso": clean(series.get("language")),
        "format": clean(series.get("publishing_format")), "age_rating": clean(issue.get("rating")),
        "editor": clean(issue.get("editing")),
    }
    date = re.fullmatch(r"(\d{4})[-.](\d{2})[-.](\d{2})", clean(issue.get("key_date")))
    if date:
        values.update({key: int(value) for key, value in zip(("year", "month", "day"), date.groups()) if int(value)})
    count = clean(issue.get("page_count"))
    if re.fullmatch(r"\d+(?:\.0+)?", count):
        values["page_count"] = int(float(count))
    stories = issue.get("story_set", [])
    stories = [s for s in stories if isinstance(s, dict)] if isinstance(stories, list) else []
    body = [s for s in stories if clean(s.get("type")).lower() == "comic story"]
    covers = [s for s in stories if clean(s.get("type")).lower() == "cover"]
    for target, source in {"writer": "script", "penciller": "pencils", "inker": "inks",
                           "colorist": "colors", "letterer": "letters", "genre": "genre"}.items():
        values[target] = "; ".join(dict.fromkeys(clean(s.get(source)) for s in body if clean(s.get(source))))
    values["cover_artist"] = "; ".join(dict.fromkeys(clean(s.get("pencils")) for s in covers if clean(s.get("pencils"))))
    # Schema drift or unusual field lengths should not discard otherwise useful fields.
    result = {}
    for key, value in values.items():
        if value == "":
            continue
        try:
            checked = ProjectMetadata.model_validate({key: value})
            result[key] = getattr(checked, key)
        except ValidationError:
            continue
    return result


@router.get("/search")
async def search(
    series: str = Query(min_length=1, max_length=200),
    number: str = Query(min_length=1, max_length=50),
    year: int | None = Query(default=None, ge=1, le=9999),
    page: int = Query(default=1, ge=1, le=100),
    language: str | None = Query(default=None, pattern=r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$", max_length=35),
) -> dict:
    if not series.strip() or not number.strip() or any(x in (".", "..") for x in (series.strip(), number.strip())):
        raise HTTPException(422, "Enter a series name and issue number.")
    path = f"series/name/{quote(series.strip(), safe='')}/issue/{quote(number.strip(), safe='')}/"
    if year:
        path += f"year/{year}/"
    data, fetched = await client.get(path + f"?page={page}")
    rows = data.get("results") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise HTTPException(502, "GCD search response format has changed.")
    # GCD issue search has no language filter. Resolve series once per result
    # page (also cached by the client), then filter locally without guessing.
    language = language.lower().split("-")[0] if language else None
    candidates = []
    series_languages = {}
    filtered_count = unknown_language_count = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        issue_id = resource_id(row.get("api_url"), "issue") or resource_id(row.get("id"), "issue")
        if issue_id:
            series_id = resource_id(row.get("series"), "series")
            if series_id and series_id not in series_languages:
                series_data, _ = await client.get(f"series/{series_id}/")
                series_languages[series_id] = clean(series_data.get("language")).lower() if isinstance(series_data, dict) else ""
            candidate_language = series_languages.get(series_id, "")
            if not candidate_language:
                unknown_language_count += 1
            if language and candidate_language != language:
                filtered_count += 1
                continue
            candidates.append({"id": issue_id, "label": clean(row.get("descriptor")) or clean(row.get("series_name")),
                               "language_iso": candidate_language or None,
                               "publication_date": clean(row.get("publication_date")),
                               "page_count": clean(row.get("page_count")),
                               "source_url": f"https://www.comics.org/issue/{issue_id}/"})
    return {"provider": "gcd", "candidates": candidates, "page": page,
            "language": language, "filtered_count": filtered_count, "unknown_language_count": unknown_language_count,
            "next_page": page + 1 if isinstance(data, dict) and data.get("next") and page < 100 else None,
            "retrieved_at": fetched}


@router.get("/issues/{issue_id}")
async def details(issue_id: int) -> dict:
    if issue_id < 1:
        raise HTTPException(422, "Invalid GCD issue ID.")
    issue, fetched = await client.get(f"issue/{issue_id}/")
    if not isinstance(issue, dict) or "number" not in issue:
        raise HTTPException(502, "GCD issue response format has changed.")
    series_id = resource_id(issue.get("series"), "series")
    series = (await client.get(f"series/{series_id}/"))[0] if series_id else {}
    series = series if isinstance(series, dict) else {}
    publisher_id = resource_id(series.get("publisher"), "publisher")
    publisher = (await client.get(f"publisher/{publisher_id}/"))[0] if publisher_id else {}
    publisher = publisher if isinstance(publisher, dict) else {}
    return {
        "id": issue_id, "fields": normalize_issue(issue, series, publisher),
        "record_id": f"gcd:issue:{issue_id}",
        "record": {"provider": "gcd", "external_id": issue_id,
                   "source_url": f"https://www.comics.org/issue/{issue_id}/",
                   "retrieved_at": fetched, "adapter_version": "gcd-v1",
                   "license": "CC BY-SA 4.0", "attribution": "Grand Comics Database",
                   "raw": {"issue": issue, "series": series, "publisher": publisher}},
    }
