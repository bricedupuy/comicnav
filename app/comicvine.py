"""Server-only Comic Vine lookups; never change projects or infer language."""
from __future__ import annotations

import asyncio
from collections import OrderedDict, defaultdict, deque
from datetime import datetime, timezone
from hashlib import sha256
import json
import logging
import re
from time import monotonic

import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import ValidationError

from .config import settings
from .metadata import clean
from .schemas import ProjectMetadata

router = APIRouter(prefix="/v1/metadata/comicvine", tags=["metadata"])
BASE = "https://comicvine.gamespot.com/api/"
LANGUAGE_NOTICE = "Comic Vine does not supply a verified language field. These results are not language-filtered; verify the edition manually. Your existing language will be preserved."


class RedactAPIKey(logging.Filter):
    def filter(self, record):
        record.msg = re.sub(r"(?i)(api_key=)[^&\s\"']+", r"\1[REDACTED]", record.getMessage())
        record.args = ()
        return True


# httpx's INFO request log otherwise includes the provider's query-string key.
logging.getLogger("httpx").addFilter(RedactAPIKey())


class ComicVineClient:
    def __init__(self, transport=None, interval=1.0):
        self.transport, self.interval = transport, interval
        self.lock = asyncio.Lock()
        self.next_request = 0.0
        self.cache = OrderedDict()
        self.requests = defaultdict(deque)
        self.key_digest = None

    async def get(self, path, params):
        async with self.lock:
            key = settings.comicvine_api_key.strip()
            if not key:
                raise HTTPException(503, "Set COMICVINE_API_KEY in the server's environment, then redeploy.")
            digest = sha256(key.encode()).digest()
            if digest != self.key_digest:
                self.cache.clear()
                self.key_digest = digest
            cache_key = (path, tuple(sorted(params.items())))
            now = monotonic()
            cached = self.cache.get(cache_key)
            if cached and cached[0] > now:
                self.cache.move_to_end(cache_key)
                return cached[1], cached[2]
            history = self.requests[path.split('/')[0]]
            while history and history[0] <= now - 3600:
                history.popleft()
            if len(history) >= 180:
                raise HTTPException(429, "Comic Vine hourly request budget reached. Try later.",
                                    headers={"Retry-After": str(int(history[0] + 3600 - now) + 1)})
            if self.next_request - now > 2:
                raise HTTPException(429, "Comic Vine is rate limited. Try later.",
                                    headers={"Retry-After": str(int(self.next_request - now) + 1)})
            if self.next_request > now:
                await asyncio.sleep(self.next_request - now)
            history.append(monotonic())
            self.next_request = monotonic() + self.interval
            try:
                async with httpx.AsyncClient(transport=self.transport, timeout=15, follow_redirects=False) as http:
                    async with http.stream("GET", BASE + path, params={**params, "api_key": key, "format": "json"},
                                           headers={"User-Agent": "ComicNav/0.1 (metadata lookup)", "Accept": "application/json"}) as response:
                        if response.status_code == 429:
                            retry = response.headers.get("Retry-After", "60")
                            self.cooldown(min(max(int(retry), 1), 3600) if retry.isdigit() else 60)
                        if response.status_code in (401, 403):
                            raise HTTPException(503, "Comic Vine denied access. Check the server API key or retry later.")
                        if response.status_code == 404:
                            raise HTTPException(404, "Comic Vine record not found.")
                        if not 200 <= response.status_code < 300:
                            raise HTTPException(502, "Comic Vine is temporarily unavailable.")
                        raw = bytearray()
                        async for chunk in response.aiter_bytes():
                            raw.extend(chunk)
                            if len(raw) > 2_000_000:
                                raise HTTPException(502, "Comic Vine response exceeded the size limit. Narrow your search.")
                        # Don't retain an echoed key in cached content or imported values.
                        data = json.loads(raw.decode('utf-8').replace(key, '[REDACTED]'))
            except httpx.TimeoutException:
                raise HTTPException(504, "Comic Vine lookup timed out. Please retry.") from None
            except (httpx.HTTPError, ValueError):
                raise HTTPException(502, "Comic Vine returned an unreadable response.") from None
            if not isinstance(data, dict):
                raise HTTPException(502, "Comic Vine response format has changed.")
            status = data.get("status_code")
            if status == 100:
                raise HTTPException(503, "Comic Vine rejected the server API key. Check COMICVINE_API_KEY.")
            if status == 101:
                raise HTTPException(404, "Comic Vine record not found.")
            if status == 107:
                self.cooldown(60)
            if status != 1:
                raise HTTPException(502, "Comic Vine could not complete this lookup.")
            fetched = datetime.now(timezone.utc).isoformat()
            self.cache[cache_key] = (monotonic() + 3600, data, fetched)
            self.cache.move_to_end(cache_key)
            while len(self.cache) > 64:
                self.cache.popitem(last=False)
            return data, fetched

    def cooldown(self, seconds):
        self.next_request = monotonic() + seconds
        raise HTTPException(429, "Comic Vine request limit reached. Try later.", headers={"Retry-After": str(seconds)})


client = ComicVineClient()


def positive_id(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def mapping(value):
    return value if isinstance(value, dict) else {}


def source_url(issue_id, value=None):
    if isinstance(value, str) and re.fullmatch(rf"https://comicvine\.gamespot\.com/[^/?#]*/4000-{issue_id}/", value):
        return value
    return f"https://comicvine.gamespot.com/issue/4000-{issue_id}/"


@router.get("/search")
async def search(
    series: str = Query(min_length=1, max_length=200),
    number: str = Query(min_length=1, max_length=50),
    year: int | None = Query(default=None, ge=1, le=9999),
    page: int = Query(default=1, ge=1, le=100),
    volume_id: int | None = Query(default=None, gt=0),
    language: str | None = Query(default=None, max_length=35),
):
    if not series.strip() or not number.strip():
        raise HTTPException(422, "Enter a series name and issue number.")
    # Commas, colons and pipes are operators in Comic Vine's filter grammar.
    if any(char in number for char in ",:|"):
        raise HTTPException(422, "Issue numbers cannot contain Comic Vine filter operators (, : |).")
    limit = 20
    if volume_id is None:
        path = "search/"
        params = {"query": series.strip(), "resources": "volume", "limit": limit, "page": page,
                  "field_list": "id,name,start_year,publisher,count_of_issues"}
    else:
        path = "issues/"
        filters = f"volume:{volume_id},issue_number:{number.strip()}"
        if year:
            filters += f",cover_date:{year:04d}-01-01|{year:04d}-12-31"
        params = {"filter": filters, "limit": limit, "offset": (page - 1) * limit,
                  "field_list": "id,name,issue_number,cover_date,volume"}
    data, fetched = await client.get(path, params)
    rows = data.get("results")
    total = data.get("number_of_total_results")
    if not isinstance(rows, list) or not isinstance(total, int) or total < 0:
        raise HTTPException(502, "Comic Vine search response format has changed.")
    candidates = []
    for row in rows:
        if not isinstance(row, dict) or not positive_id(row.get("id")):
            continue
        if volume_id is None:
            label = " · ".join(filter(None, [clean(row.get("name")), clean(row.get("start_year")),
                                            clean(mapping(row.get("publisher")).get("name"))]))
        else:
            # Defensive exact check in case upstream filtering semantics drift.
            if clean(row.get("issue_number")) != number.strip() or mapping(row.get("volume")).get("id") != volume_id:
                continue
            label = f"{clean(mapping(row.get('volume')).get('name'))} #{clean(row.get('issue_number'))} — {clean(row.get('name'))}"
        candidates.append({"id": row["id"], "kind": "volume" if volume_id is None else "issue", "label": label,
                           "publication_date": clean(row.get("cover_date")), "language_iso": None})
    return {"provider": "comicvine", "candidates": candidates, "page": page,
            "next_page": page + 1 if page * limit < total and page < 100 else None,
            "language": None, "language_filter_supported": False, "notice": LANGUAGE_NOTICE,
            "stage": "volumes" if volume_id is None else "issues", "retrieved_at": fetched}


def normalize_issue(issue, volume):
    values = {"series": clean(volume.get("name")) or clean(mapping(issue.get("volume")).get("name")),
              "number": clean(issue.get("issue_number")), "title": clean(issue.get("name")),
              "publisher": clean(mapping(volume.get("publisher")).get("name"))}
    # Cover date is a publication-date hint, not the volume's start year.
    date = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", clean(issue.get("cover_date")))
    if date:
        values.update({k: int(v) for k, v in zip(("year", "month", "day"), date.groups()) if int(v)})
    credits = issue.get("person_credits")
    credits = credits if isinstance(credits, list) else []
    for field, role in {"writer": "writer", "penciller": "penciler", "inker": "inker", "colorist": "colorist",
                        "letterer": "letterer", "editor": "editor", "cover_artist": "cover"}.items():
        values[field] = "; ".join(dict.fromkeys(clean(c.get("name")) for c in credits if isinstance(c, dict)
            and role in [r.strip().lower() for r in clean(c.get("role")).split(",")] and clean(c.get("name"))))
    result = {}
    for key, value in values.items():
        if value == "":
            continue
        try:
            result[key] = getattr(ProjectMetadata.model_validate({key: value}), key)
        except ValidationError:
            continue
    return result


@router.get("/issues/{issue_id}")
async def details(issue_id: int):
    if issue_id < 1:
        raise HTTPException(422, "Invalid Comic Vine issue ID.")
    data, fetched = await client.get(f"issue/4000-{issue_id}/",
        {"field_list": "id,name,issue_number,cover_date,volume,person_credits,site_detail_url"})
    issue = mapping(data.get("results"))
    if issue.get("id") != issue_id or "issue_number" not in issue:
        raise HTTPException(502, "Comic Vine issue response format has changed.")
    volume_id = positive_id(mapping(issue.get("volume")).get("id"))
    volume = {}
    if volume_id:
        data, _ = await client.get(f"volume/4050-{volume_id}/", {"field_list": "id,name,publisher"})
        volume = mapping(data.get("results"))
        if volume.get("id") != volume_id:
            raise HTTPException(502, "Comic Vine volume response format has changed.")
    fields = normalize_issue(issue, volume)
    return {"id": issue_id, "fields": fields, "notice": LANGUAGE_NOTICE,
            "record_id": f"comicvine:issue:{issue_id}",
            "record": {"provider": "comicvine", "external_id": issue_id, "source_url": source_url(issue_id, issue.get("site_detail_url")),
                       "retrieved_at": fetched, "adapter_version": "comicvine-v1", "license": "Comic Vine API terms",
                       "attribution": "Comic Vine", "raw": {"fields": fields}}}
