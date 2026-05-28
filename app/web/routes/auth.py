import secrets
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database.repository import Repository
from app.web.auth import create_access_token, hash_password, verify_password
from app.web.dependencies import get_db
from app.services.email_utils import send_email

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

    verification_token = secrets.token_urlsafe(32)
    user = repo.create_user(
        email=email,
        name=name,
        password_hash=hash_password(password),
        email_verification_token=verification_token
    )
    if not user:
        return templates.TemplateResponse(
            request, "signup.html",
            {"error": "Failed to create account. Please try again."},
            status_code=500,
        )

    try:
        verification_link = f"http://localhost:8000/verify-email/{verification_token}"
        send_email(
            subject="Verify your AI News Digest account",
            body_text=f"Welcome {name}!\n\nPlease verify your email by visiting: {verification_link}\n\nThis link expires in 24 hours.",
            recipients=[email],
        )
    except Exception as e:
        return templates.TemplateResponse(
            request, "signup.html",
            {"error": "Account created but failed to send verification email. Please try again."},
            status_code=500,
        )

    return templates.TemplateResponse(
        request, "signup_confirmation.html",
        {"email": email}
    )


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
    if not user.email_verified:
        return templates.TemplateResponse(
            request, "login.html",
            {"error": "Please verify your email before logging in."},
            status_code=403,
        )
    token = create_access_token(str(user.id))
    response = RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie("access_token", token, httponly=True, secure=True, samesite="Strict", max_age=60 * 60 * 24 * 7)
    return response


@router.get("/verify-email/{token}", response_class=HTMLResponse)
async def verify_email(request: Request, token: str, db: Session = Depends(get_db)):
    repo = Repository(db)
    user = repo.get_user_by_verification_token(token)
    if not user:
        return templates.TemplateResponse(
            request, "verify_email_result.html",
            {"success": False, "message": "Invalid or expired verification link."},
            status_code=400,
        )

    repo.update_user(user.id, email_verified=True, email_verification_token=None)

    return templates.TemplateResponse(
        request, "verify_email_result.html",
        {"success": True, "message": "Email verified! You can now log in."}
    )


@router.get("/forgot-password", response_class=HTMLResponse)
async def forgot_password_form(request: Request):
    return templates.TemplateResponse(request, "forgot_password.html", {"error": None})


@router.post("/forgot-password", response_class=HTMLResponse)
async def forgot_password(
    request: Request,
    email: str = Form(...),
    db: Session = Depends(get_db),
):
    repo = Repository(db)
    user = repo.get_user_by_email(email)

    if not user:
        return templates.TemplateResponse(
            request, "forgot_password_sent.html",
            {"email": email, "found": False}
        )

    reset_token = secrets.token_urlsafe(32)
    reset_expires = datetime.now(timezone.utc) + timedelta(hours=24)
    repo.update_user(user.id, password_reset_token=reset_token, password_reset_expires=reset_expires)

    try:
        reset_link = f"http://localhost:8000/reset-password/{reset_token}"
        send_email(
            subject="Reset your AI News Digest password",
            body_text=f"You requested a password reset.\n\nClick here to reset: {reset_link}\n\nThis link expires in 24 hours.\n\nIf you didn't request this, ignore this email.",
            recipients=[email],
        )
    except Exception:
        pass

    return templates.TemplateResponse(
        request, "forgot_password_sent.html",
        {"email": email, "found": True}
    )


@router.get("/reset-password/{token}", response_class=HTMLResponse)
async def reset_password_form(request: Request, token: str, db: Session = Depends(get_db)):
    repo = Repository(db)
    user = repo.get_user_by_reset_token(token)

    if not user:
        return templates.TemplateResponse(
            request, "reset_password.html",
            {"token": token, "valid": False, "error": "Invalid or expired reset link"}
        )

    return templates.TemplateResponse(request, "reset_password.html", {"token": token, "valid": True, "error": None})


@router.post("/reset-password/{token}", response_class=HTMLResponse)
async def reset_password(
    request: Request,
    token: str,
    password: str = Form(...),
    password_confirm: str = Form(...),
    db: Session = Depends(get_db),
):
    repo = Repository(db)

    if password != password_confirm:
        return templates.TemplateResponse(
            request, "reset_password.html",
            {"token": token, "valid": True, "error": "Passwords do not match"}
        )

    user = repo.get_user_by_reset_token(token)
    if not user:
        return templates.TemplateResponse(
            request, "reset_password.html",
            {"token": token, "valid": False, "error": "Invalid or expired reset link"}
        )

    repo.update_user(user.id, password_hash=hash_password(password), password_reset_token=None, password_reset_expires=None)

    return templates.TemplateResponse(
        request, "reset_password_success.html",
        {}
    )


@router.get("/logout")
async def logout():
    response = RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie("access_token")
    return response
