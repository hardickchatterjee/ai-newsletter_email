import re

import requests
from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database.models import User
from app.database.repository import Repository
from app.web.auth import get_current_user
from app.web.dependencies import get_db

router = APIRouter()
templates = Jinja2Templates(directory="app/web/templates")

CHANNEL_ID_RE = re.compile(r"UC[a-zA-Z0-9_-]{22}")


def _extract_channel_id(raw: str) -> str | None:
    raw = raw.strip()
    m = CHANNEL_ID_RE.search(raw)
    if m:
        return m.group(0)
    if re.match(r"^UC[a-zA-Z0-9_-]{22}$", raw):
        return raw
    return None


def _resolve_channel_name(channel_id: str) -> str | None:
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    try:
        resp = requests.get(url, timeout=10)
        if resp.ok:
            m = re.search(r"<title>([^<]+)</title>", resp.text)
            if m:
                return m.group(1).strip()
    except Exception:
        pass
    return None


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    repo = Repository(db)
    channels = repo.get_user_channels(current_user.id)
    return templates.TemplateResponse(
        request, "dashboard.html",
        {"user": current_user, "channels": channels, "error": None, "success": None},
    )


@router.post("/settings", response_class=HTMLResponse)
async def update_settings(
    request: Request,
    name: str = Form(...),
    background: str = Form(""),
    expertise_level: str = Form("Intermediate"),
    interests: str = Form(""),
    content_depth: str = Form(""),
    content_type: str = Form(""),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    repo = Repository(db)
    interests_list = [i.strip() for i in interests.split(",") if i.strip()] if interests else []
    repo.update_user_profile(
        current_user.id,
        name=name,
        background=background or None,
        expertise_level=expertise_level,
        interests=interests_list or None,
        content_depth=content_depth or None,
        content_type=content_type or None,
    )
    channels = repo.get_user_channels(current_user.id)
    updated_user = repo.get_user_by_id(current_user.id)
    return templates.TemplateResponse(
        request, "dashboard.html",
        {"user": updated_user, "channels": channels, "error": None, "success": "Profile saved."},
    )


@router.post("/channels/add", response_class=HTMLResponse)
async def add_channel(
    request: Request,
    channel_input: str = Form(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    repo = Repository(db)
    channel_id = _extract_channel_id(channel_input)
    channels = repo.get_user_channels(current_user.id)

    if not channel_id:
        return templates.TemplateResponse(
            request, "dashboard.html",
            {"user": current_user, "channels": channels, "error": "Could not find a valid channel ID in that input.", "success": None},
            status_code=400,
        )

    channel_name = _resolve_channel_name(channel_id)
    result = repo.add_user_channel(current_user.id, channel_id, channel_name)
    if result is None:
        return templates.TemplateResponse(
            request, "dashboard.html",
            {"user": current_user, "channels": channels, "error": "That channel is already in your list.", "success": None},
            status_code=400,
        )

    channels = repo.get_user_channels(current_user.id)
    return templates.TemplateResponse(
        request, "dashboard.html",
        {"user": current_user, "channels": channels, "error": None, "success": f"Added: {channel_name or channel_id}"},
    )


@router.post("/channels/remove", response_class=HTMLResponse)
async def remove_channel(
    request: Request,
    channel_id: str = Form(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    repo = Repository(db)
    repo.remove_user_channel(current_user.id, channel_id)
    return RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
