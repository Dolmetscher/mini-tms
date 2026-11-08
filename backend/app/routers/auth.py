"""Аутентификация: логин, сессия, смена пароля.

Гигиена аккаунта (лучшие практики):

- Логин защищен от подбора пароля: скользящее окно неудачных попыток на
  пару (логин, IP); при превышении — 429 с Retry-After. Состояние в памяти
  процесса (MVP: один инстанс; для кластера нужен внешний сторедж).
- Смена пароля требует текущий пароль (подтверждение владельца сессии);
  новая пара PBKDF2 (200k итераций). Существующие JWT остаются валидными
  до истечения — stateless-токены, «выйти со всех устройств» нет (MVP).
"""
import re
import threading
import time
from collections import deque

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from ..auth import create_access_token, get_current_user, hash_password, verify_password
from ..database import get_db
from ..models import Membership, Organization, User
from ..schemas import (
    ContactUpdate, LoginRequest, MembershipOut, NewToken, PasswordChange, TokenResponse, UserOut,
)

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
            detail="Слишком много неудачных попыток входа; попробуйте позже",
            headers={"Retry-After": str(wait)},
        )
    user = db.query(User).filter(User.username == body.username).first()
    if user is None or not verify_password(body.password, user.password_hash):
        _register_failure(key)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Неверное имя пользователя или пароль")
    _clear_failures(key)
    memberships = (
        db.query(Membership, Organization)
        .join(Organization, Membership.org_id == Organization.id)
        .filter(Membership.user_id == user.id)
        .all()
    )
    return TokenResponse(
        access_token=create_access_token(user.username, user.pwd_ver),
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


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_TG_CHAT_RE = re.compile(r"^@?[A-Za-z0-9_]{3,64}$")


@router.patch("/me", response_model=UserOut)
def update_contacts(
    body: ContactUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Свои контакты для внешних уведомлений (бета): email и Telegram chat_id.

    chat_id — числовой идентификатор чата (узнать: написать боту, затем
    @userinfobot) или @username. Пустая строка очищает канал.
    """
    if body.email is not None:
        email = body.email.strip()
        if email and not _EMAIL_RE.match(email):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Некорректный email")
        user.email = email or None
    if body.telegram_chat_id is not None:
        tg = body.telegram_chat_id.strip()
        if tg and not _TG_CHAT_RE.match(tg):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Некорректный Telegram chat id")
        user.telegram_chat_id = tg or None
    db.commit()
    db.refresh(user)
    return UserOut.model_validate(user)


@router.post("/change-password", response_model=NewToken)
def change_password(
    body: PasswordChange,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Смена собственного пароля: текущий обязателен, новый — не равен ему.

    Версионирование пароля отзывает все ранее выданные токены (stateless-
    ревокация), поэтому клиенту возвращается свежий — сессия продолжается
    без повторного входа; остальные сессии этого пользователя сбрасываются.
    """
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Текущий пароль неверен")
    if body.current_password == body.new_password:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Новый пароль должен отличаться от текущего",
        )
    user.password_hash = hash_password(body.new_password)
    user.pwd_ver += 1
    db.commit()
    return NewToken(access_token=create_access_token(user.username, user.pwd_ver))
