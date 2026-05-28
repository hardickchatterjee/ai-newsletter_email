import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Cookie, Depends, HTTPException, status
import bcrypt
from jose import JWTError, jwt
from sqlalchemy.orm import Session

from app.database.models import User
from app.database.repository import Repository
from app.web.dependencies import get_db

SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    raise ValueError("SECRET_KEY environment variable must be set. Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\"")
ALGORITHM = "HS256"
TOKEN_TTL_DAYS = 7

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def create_access_token(user_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(days=TOKEN_TTL_DAYS)
    return jwt.encode({"sub": user_id, "exp": expire}, SECRET_KEY, algorithm=ALGORITHM)


from fastapi.responses import RedirectResponse


def get_current_user(
    access_token: Optional[str] = Cookie(default=None),
    db: Session = Depends(get_db),
) -> User:
    def _redirect():
        raise HTTPException(
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            headers={"Location": "/login"},
        )

    if not access_token:
        _redirect()
    try:
        payload = jwt.decode(access_token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            _redirect()
    except JWTError:
        _redirect()

    repo = Repository(db)
    user = repo.get_user_by_id(user_id)
    if user is None or not user.is_active:
        _redirect()
    return user
