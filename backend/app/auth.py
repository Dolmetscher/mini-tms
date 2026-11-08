import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from . import config
from .database import get_db
from .models import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="api/login")

_PBKDF2_ITERATIONS = 200_000


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _PBKDF2_ITERATIONS).hex()
    return f"pbkdf2${salt}${digest}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, salt, digest = stored.split("$", 2)
    except ValueError:
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), _PBKDF2_ITERATIONS).hex()
    return hmac.compare_digest(candidate, digest)


def create_access_token(username: str, pwd_ver: int = 1) -> str:
    payload = {
        "sub": username,
        # Версия пароля: после смены пароля инкрементируется — токены со
        # старой версией отклоняются (stateless-ревокация всех сессий).
        "pwd_ver": pwd_ver,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=config.ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, config.SECRET_KEY, algorithm="HS256")


def decode_token(token: str) -> tuple[str, int]:
    """Возвращает (username, pwd_ver) из токена или бросает jwt.PyJWTError.

    Токены, выданные до внедрения версионирования, несут старую версию 1.
    """
    payload = jwt.decode(token, config.SECRET_KEY, algorithms=["HS256"])
    username = payload.get("sub")
    if not username:
        raise jwt.InvalidTokenError("missing sub claim")
    try:
        pwd_ver = int(payload.get("pwd_ver", 1))
    except (TypeError, ValueError):
        pwd_ver = 1
    return username, pwd_ver


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    credentials_exc = HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        detail="Недействительный или истекший токен",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        username, pwd_ver = decode_token(token)
    except jwt.PyJWTError:
        raise credentials_exc
    user = db.query(User).filter(User.username == username).first()
    if user is None:
        raise credentials_exc
    if user.pwd_ver != pwd_ver:
        # Пароль сменили после выдачи этого токена — сессия отозвана.
        raise credentials_exc
    return user
