"""Аутентификация: логин, сессия, смена пароля.

Гигиена аккаунта (лучшие практики):

- Логин защищен от подбора пароля: скользящее окно неудачных попыток на
  пару (логин, IP); при превышении — 429 с Retry-After. Состояние в памяти
  процесса (MVP: один инстанс; для кластера нужен внешний сторедж).
- Смена пароля требует текущий пароль (подтверждение владельца сессии);
  новая пара PBKDF2 (200k итераций). Существующие JWT остаются валидными
  до истечения — stateless-токены, «выйти со всех устройств» нет (MVP).
"""
import threading
import time
from collections import deque

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from ..auth import create_access_token, get_current_user, hash_password, verify_password
from ..database import get_db
from ..models import Membership, Organization, User
from ..schemas import LoginRequest, MembershipOut, PasswordChange, TokenResponse, UserOut

router = APIRouter(prefix="/api", tags=["auth"])

# Скользящее окно неудачных логинов (brute force). Константы читаются в
# рантайме (а не при импорте) — тесты подменяют их monkeypatch-ом.
LOGIN_FAILURE_LIMIT = 10
LOGIN_FAILURE_WINDOW_SECONDS = 15 * 60

_login_failures: dict[str, deque] = {}
_login_lock = threading.Lock()


def _failure_key(body: LoginRequest, request: Request) -> str:
    client = request.client.host if request.client else "unknown"
    return f"{body.username.lower()}@{client}"


def _prune(attempts: deque, now: float) -> None:
    while attempts and now - attempts[0] > LOGIN_FAILURE_WINDOW_SECONDS:
        attempts.popleft()


def _retry_after_seconds(key: str) -> int:
    """Сколько ждать до разблокировки (0 — блокировки нет)."""
    with _login_lock:
        attempts = _login_failures.get(key)
        if not attempts:
            return 0
        now = time.monotonic()
        _prune(attempts, now)
        if len(attempts) >= LOGIN_FAILURE_LIMIT:
            return max(int(LOGIN_FAILURE_WINDOW_SECONDS - (now - attempts[0])), 1)
        return 0


def _register_failure(key: str) -> None:
    with _login_lock:
        attempts = _login_failures.setdefault(key, deque())
        now = time.monotonic()
        _prune(attempts, now)
        attempts.append(now)


def _clear_failures(key: str) -> None:
    with _login_lock:
        _login_failures.pop(key, None)


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)):
    key = _failure_key(body, request)
    wait = _retry_after_seconds(key)
    if wait:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many failed login attempts; try again later",
            headers={"Retry-After": str(wait)},
        )
    user = db.query(User).filter(User.username == body.username).first()
    if user is None or not verify_password(body.password, user.password_hash):
        _register_failure(key)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")
    _clear_failures(key)
    memberships = (
        db.query(Membership, Organization)
        .join(Organization, Membership.org_id == Organization.id)
        .filter(Membership.user_id == user.id)
        .all()
    )
    return TokenResponse(
        access_token=create_access_token(user.username),
        user=UserOut.model_validate(user),
        memberships=[
            MembershipOut(org_id=m.org_id, org_name=org.name, role=m.role)
            for m, org in memberships
        ],
    )


@router.get("/me", response_model=TokenResponse)
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    memberships = (
        db.query(Membership, Organization)
        .join(Organization, Membership.org_id == Organization.id)
        .filter(Membership.user_id == user.id)
        .all()
    )
    return TokenResponse(
        access_token="",  # /me не выдает новый токен
        user=UserOut.model_validate(user),
        memberships=[
            MembershipOut(org_id=m.org_id, org_name=org.name, role=m.role)
            for m, org in memberships
        ],
    )


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    body: PasswordChange,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Смена собственного пароля: текущий обязателен, новый — не равен ему.

    Токены живут до истечения (stateless JWT): смена пароля не «выкидывает»
    другие сессии — осознанное ограничение MVP.
    """
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect")
    if body.current_password == body.new_password:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="New password must be different from the current one",
        )
    user.password_hash = hash_password(body.new_password)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
