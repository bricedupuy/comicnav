from __future__ import annotations

import io
import re
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import FileResponse, JSONResponse
from PIL import Image, UnidentifiedImageError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import settings
from .database import get_session
from .project_models import GuideVersion, Project, ProjectPage
from .readium import editor_guided_document
from .schemas import ProjectCreateRequest, ProjectDraftUpdate, ProjectPageDraft
from .storage import media_storage


router = APIRouter(prefix="/v1/projects", tags=["projects"])
_ALLOWED_IMAGE_FORMATS = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}


def _project_url(project_id: UUID) -> str:
    return f"/v1/projects/{project_id}"


def _page_image_url(project_id: UUID, page_id: UUID) -> str:
    return f"{_project_url(project_id)}/pages/{page_id}/image"


def _page_payload(page: ProjectPage) -> dict:
    return {
        "id": str(page.id),
        "filename": page.filename,
        "image_url": _page_image_url(page.project_id, page.id),
        "width": page.width,
        "height": page.height,
        "background_color": page.background_color,
        "is_wide_spread": page.is_wide_spread,
        "panels": page.panels,
        "review_status": page.review_status,
        "model_ids": page.model_ids,
    }


def _validate_page_draft(page_index: int, draft: ProjectPageDraft) -> None:
    """Reject malformed or out-of-bounds panel geometry before it is stored."""
    for panel_index, panel in enumerate(draft.panels):
        for point in panel.points:
            if len(point) != 2:
                raise HTTPException(
                    status_code=422,
                    detail=f"Page {page_index + 1}, panel {panel_index + 1} contains an invalid point",
                )
            x, y = point
            if not 0 <= x <= draft.width or not 0 <= y <= draft.height:
                raise HTTPException(
                    status_code=422,
                    detail=f"Page {page_index + 1}, panel {panel_index + 1} is outside the page bounds",
                )


async def _project_payload(session: AsyncSession, project: Project) -> dict:
    pages = list(
        await session.scalars(
            select(ProjectPage).where(ProjectPage.project_id == project.id).order_by(ProjectPage.page_index)
        )
    )
    return {
        "id": str(project.id),
        "title": project.title,
        "reading_direction": project.reading_direction,
        "created_at": project.created_at.isoformat(),
        "updated_at": project.updated_at.isoformat(),
        "pages": [_page_payload(page) for page in pages],
    }


async def _require_project(session: AsyncSession, project_id: UUID) -> Project:
    project = await session.get(Project, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


async def _read_page_upload(file: UploadFile) -> tuple[bytes, int, int, str, str]:
    raw = await file.read(settings.max_upload_mb * 1024 * 1024 + 1)
    if len(raw) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Image is too large")

    try:
        image = Image.open(io.BytesIO(raw))
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise HTTPException(status_code=400, detail="Invalid image") from exc

    image_format = (image.format or "").upper()
    if image_format not in _ALLOWED_IMAGE_FORMATS:
        raise HTTPException(status_code=415, detail="Only JPEG, PNG and WebP are accepted")
    if image.width * image.height > settings.max_image_pixels:
        raise HTTPException(status_code=413, detail="Image pixel dimensions exceed limit")

    original_name = Path(file.filename or "page.jpg").name
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", original_name).strip("._") or "page.jpg"
    return raw, image.width, image.height, _ALLOWED_IMAGE_FORMATS[image_format], safe_name


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_project(
    body: ProjectCreateRequest,
    session: AsyncSession = Depends(get_session),
) -> dict:
    project = Project(title=body.title, reading_direction=body.reading_direction)
    session.add(project)
    await session.commit()
    await session.refresh(project)
    return await _project_payload(session, project)


@router.get("")
async def list_projects(session: AsyncSession = Depends(get_session)) -> dict:
    projects = list(await session.scalars(select(Project).order_by(Project.updated_at.desc(), Project.created_at.desc())))
    return {
        "projects": [
            {
                "id": str(project.id),
                "title": project.title,
                "reading_direction": project.reading_direction,
                "updated_at": project.updated_at.isoformat(),
            }
            for project in projects
        ]
    }


@router.get("/{project_id}")
async def get_project(project_id: UUID, session: AsyncSession = Depends(get_session)) -> dict:
    return await _project_payload(session, await _require_project(session, project_id))


@router.post("/{project_id}/pages", status_code=status.HTTP_201_CREATED)
async def upload_project_page(
    project_id: UUID,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
) -> dict:
    project = await _require_project(session, project_id)
    raw, width, height, media_type, filename = await _read_page_upload(file)
    next_index = await session.scalar(
        select(func.coalesce(func.max(ProjectPage.page_index) + 1, 0)).where(ProjectPage.project_id == project.id)
    )
    page_id = uuid4()
    page = ProjectPage(
        id=page_id,
        project_id=project.id,
        page_index=int(next_index),
        filename=filename,
        asset_key=f"projects/{project.id}/pages/{page_id}/{filename}",
        media_type=media_type,
        width=width,
        height=height,
    )
    project.updated_at = datetime.now(timezone.utc)
    session.add(page)
    try:
        await media_storage.put(page.asset_key, raw)
        await session.commit()
    except Exception:
        await session.rollback()
        await media_storage.delete(page.asset_key)
        raise
    await session.refresh(page)
    return _page_payload(page)


@router.put("/{project_id}")
async def save_project_draft(
    project_id: UUID,
    body: ProjectDraftUpdate,
    session: AsyncSession = Depends(get_session),
) -> dict:
    project = await _require_project(session, project_id)
    existing_pages = list(
        await session.scalars(select(ProjectPage).where(ProjectPage.project_id == project.id).order_by(ProjectPage.page_index))
    )
    existing_by_id = {page.id: page for page in existing_pages}
    requested_ids = [page.id for page in body.pages]
    if len(requested_ids) != len(set(requested_ids)):
        raise HTTPException(status_code=422, detail="A page may only appear once in a project draft")
    unknown_ids = set(requested_ids) - set(existing_by_id)
    if unknown_ids:
        raise HTTPException(status_code=422, detail="Upload project pages before saving their panel data")

    deleted_asset_keys: list[str] = []
    for page in existing_pages:
        if page.id not in requested_ids:
            deleted_asset_keys.append(page.asset_key)
            await session.delete(page)

    project.title = body.title
    project.reading_direction = body.reading_direction
    for page_index, draft in enumerate(body.pages):
        page = existing_by_id[draft.id]
        if draft.width != page.width or draft.height != page.height:
            raise HTTPException(status_code=422, detail="Page dimensions are fixed by the uploaded image")
        _validate_page_draft(page_index, draft)
        page.page_index = page_index
        page.filename = draft.filename
        page.width = draft.width
        page.height = draft.height
        page.background_color = draft.background_color
        page.is_wide_spread = draft.is_wide_spread
        page.panels = [panel.model_dump(exclude_none=True) for panel in draft.panels]
        page.review_status = draft.review_status
        page.model_ids = list(dict.fromkeys(draft.model_ids))

    project.updated_at = datetime.now(timezone.utc)
    await session.commit()
    for asset_key in deleted_asset_keys:
        await media_storage.delete(asset_key)
    await session.refresh(project)
    return await _project_payload(session, project)


@router.get("/{project_id}/pages/{page_id}/image", include_in_schema=False)
async def get_project_page_image(
    project_id: UUID,
    page_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> FileResponse:
    page = await session.get(ProjectPage, page_id)
    if page is None or page.project_id != project_id:
        raise HTTPException(status_code=404, detail="Project page not found")
    path = media_storage.path_for(page.asset_key)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Project page media is unavailable")
    return FileResponse(path, media_type=page.media_type, filename=page.filename)


@router.post("/{project_id}/guide-versions", status_code=status.HTTP_201_CREATED)
async def publish_guide_version(project_id: UUID, session: AsyncSession = Depends(get_session)) -> dict:
    project = await _require_project(session, project_id)
    pages = list(
        await session.scalars(
            select(ProjectPage).where(ProjectPage.project_id == project.id).order_by(ProjectPage.page_index)
        )
    )
    if not pages:
        raise HTTPException(status_code=422, detail="A project needs at least one page before publishing a guide")

    current_version = await session.scalar(
        select(func.coalesce(func.max(GuideVersion.version), 0)).where(GuideVersion.project_id == project.id)
    )
    version = int(current_version) + 1
    document = {
        "readingProgression": project.reading_direction,
        "pages": [
            editor_guided_document(
                page.filename,
                f"projects/{project.id}/guide-versions/{version}/pages/{page.page_index + 1}.guided.json",
                page.panels,
                page.width,
                page.height,
            )
            for page in pages
        ],
    }
    guide = GuideVersion(project_id=project.id, version=version, document=document)
    session.add(guide)
    project.updated_at = datetime.now(timezone.utc)
    await session.commit()
    await session.refresh(guide)
    return {
        "id": str(guide.id),
        "project_id": str(project.id),
        "version": guide.version,
        "created_at": guide.created_at.isoformat(),
        "document": guide.document,
    }


@router.get("/{project_id}/guide-versions")
async def list_guide_versions(project_id: UUID, session: AsyncSession = Depends(get_session)) -> dict:
    await _require_project(session, project_id)
    guides = list(
        await session.scalars(
            select(GuideVersion).where(GuideVersion.project_id == project_id).order_by(GuideVersion.version.desc())
        )
    )
    return {
        "guide_versions": [
            {"id": str(guide.id), "version": guide.version, "created_at": guide.created_at.isoformat()} for guide in guides
        ]
    }


@router.get("/{project_id}/guide-versions/{version}")
async def get_guide_version(
    project_id: UUID,
    version: int,
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    guide = await session.scalar(
        select(GuideVersion).where(GuideVersion.project_id == project_id, GuideVersion.version == version)
    )
    if guide is None:
        raise HTTPException(status_code=404, detail="Guide version not found")
    return JSONResponse(content=guide.document, media_type="application/json")
