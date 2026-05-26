from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database.repository import Repository
from app.web.auth import create_access_token, hash_password, verify_password
from app.web.dependencies import get_db

router = APIRouter()
templates = Jinja2Templates(directory="app/web/templates")


@router.get("/signup", response_class=HTMLResponse)
async def signup_form(request: Request):
    return templates.TemplateResponse(request, "signup.html", {"error": None})


@router.post("/signup", response_class=HTMLResponse)
async def signup(
    request: Request,
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    repo = Repository(db)
    if repo.get_user_by_email(email):
        return templates.TemplateResponse(
            request, "signup.html",
            {"error": "An account with that email already exists."},
            status_code=400,
        )
    user = repo.create_user(email=email, name=name, password_hash=hash_password(password))
    token = create_access_token(str(user.id))
    response = RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie("access_token", token, httponly=True, max_age=60 * 60 * 24 * 7)
    return response


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@router.post("/login", response_class=HTMLResponse)
async def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    repo = Repository(db)
    user = repo.get_user_by_email(email)
    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            request, "login.html",
            {"error": "Invalid email or password."},
            status_code=401,
        )
    token = create_access_token(str(user.id))
    response = RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie("access_token", token, httponly=True, max_age=60 * 60 * 24 * 7)
    return response


@router.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie("access_token")
    return response
