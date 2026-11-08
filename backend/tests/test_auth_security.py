"""Тесты гигиены аккаунта: смена пароля и защита логина от подбора.

Отдельный пользователь (pwtester) — пароли сидированных аккаунтов не
трогаем: модули набора делят один процесс и одну БД.
"""
import os
import tempfile
import time

_TEST_DIR = tempfile.mkdtemp(prefix="mini-tms-sec-")
os.environ.setdefault("TMS_DATABASE_URL", f"sqlite:///{_TEST_DIR}/test.db".replace("\\", "/"))
os.environ.setdefault("TMS_DATA_DIR", os.path.join(_TEST_DIR, "data").replace("\\", "/"))
os.environ.setdefault("TMS_MT_PROVIDER", "mock")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.routers import auth as auth_router  # noqa: E402

ORG = 1


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def login(client: TestClient, username: str, password: str) -> dict | None:
    res = client.post("/api/login", json={"username": username, "password": password})
    if res.status_code != 200:
        return None
    return {"Authorization": f"Bearer {res.json()['access_token']}", "X-Org-Id": str(ORG)}


@pytest.fixture(scope="module")
def admin(client):
    headers = login(client, "admin", "admin123")
    assert headers is not None
    return headers


@pytest.fixture(scope="module")
def pwtester(client, admin):
    """Пользователь для тестов смены пароля (изолирован от сидированных)."""
    res = client.post(f"/api/orgs/{ORG}/members", headers=admin, json={
        "username": "pwtester", "password": "pw-old-123", "role": "translator",
    })
    assert res.status_code in (201, 409), res.text  # 409 — уже создан в этом процессе
    headers = login(client, "pwtester", "pw-old-123")
    assert headers is not None
    return headers


# --- Смена пароля ---


def test_change_password_flow(client, pwtester):
    # Неверный текущий пароль — отказ (владелец не подтвержден)
    res = client.post("/api/change-password", headers=pwtester, json={
        "current_password": "wrong", "new_password": "pw-new-456",
    })
    assert res.status_code == 400, res.text

    # Новый пароль равен текущему — бессмысленная смена
    res = client.post("/api/change-password", headers=pwtester, json={
        "current_password": "pw-old-123", "new_password": "pw-old-123",
    })
    assert res.status_code == 422, res.text

    # Слишком короткий — валидация (как при регистрации)
    res = client.post("/api/change-password", headers=pwtester, json={
        "current_password": "pw-old-123", "new_password": "abc",
    })
    assert res.status_code == 422, res.text

    # Без токена — 401
    assert client.post("/api/change-password", json={
        "current_password": "pw-old-123", "new_password": "pw-new-456",
    }).status_code == 401

    # Успешная смена
    res = client.post("/api/change-password", headers=pwtester, json={
        "current_password": "pw-old-123", "new_password": "pw-new-456",
    })
    assert res.status_code == 204, res.text

    # Старый пароль больше не работает, новый — работает
    assert login(client, "pwtester", "pw-old-123") is None
    assert login(client, "pwtester", "pw-new-456") is not None

    # Токен, выданный ДО смены, остается валидным до истечения (stateless JWT,
    # задокументированное ограничение MVP)
    res = client.get("/api/me", headers=pwtester)
    assert res.status_code == 200, res.text


# --- Защита логина от подбора (brute force) ---


def test_login_rate_limit(client, monkeypatch, pwtester):
    # Сжатое окно для теста: 3 попытки за 0.5 секунды
    monkeypatch.setattr(auth_router, "LOGIN_FAILURE_LIMIT", 3)
    monkeypatch.setattr(auth_router, "LOGIN_FAILURE_WINDOW_SECONDS", 0.5)

    # Три неудачных попытки для несуществующего пользователя
    for _ in range(3):
        assert client.post("/api/login", json={
            "username": "bruteforce", "password": "nope",
        }).status_code == 401

    # Четвертая — блокировка с Retry-After
    res = client.post("/api/login", json={"username": "bruteforce", "password": "nope"})
    assert res.status_code == 429, res.text
    assert int(res.headers["Retry-After"]) >= 1

    # Даже ВЕРНЫЙ пароль не принят, пока действует блокировка (окно по логину)
    for _ in range(3):
        assert client.post("/api/login", json={
            "username": "pwtester", "password": "pw-wrong",
        }).status_code == 401
    res = client.post("/api/login", json={"username": "pwtester", "password": "pw-new-456"})
    assert res.status_code == 429, res.text

    # Другой логин не задет блокировкой pwtester
    assert login(client, "pm", "pm123") is not None

    # Окно истекло — верный пароль снова принимается
    time.sleep(0.6)
    res = client.post("/api/login", json={"username": "pwtester", "password": "pw-new-456"})
    assert res.status_code == 200, res.text

    monkeypatch.undo()


def test_successful_login_resets_counter(client, monkeypatch):
    monkeypatch.setattr(auth_router, "LOGIN_FAILURE_LIMIT", 3)
    monkeypatch.setattr(auth_router, "LOGIN_FAILURE_WINDOW_SECONDS", 0.5)

    # Две неудачных, затем успешная — счетчик сброшен, лимит не копится
    for _ in range(2):
        assert client.post("/api/login", json={"username": "pm", "password": "bad"}).status_code == 401
    assert login(client, "pm", "pm123") is not None
    for _ in range(2):
        assert client.post("/api/login", json={"username": "pm", "password": "bad"}).status_code == 401
    # Всего 4 неудачных, но из-за сброса после успеха лимит (3 подряд) не достигнут
    assert client.post("/api/login", json={"username": "pm", "password": "bad"}).status_code == 401

    monkeypatch.undo()
