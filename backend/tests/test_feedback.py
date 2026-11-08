"""Кнопка обратной связи (бета): отправка из UI и лента для менеджмента.

Покрывает: отправку любым участником организации (с адресом страницы),
чтение ленты только pm/admin, валидацию (недопустимый тип, короткий текст),
анти-спам лимит (429 после FEEDBACK_MAX_PER_HOUR сообщений в час).
"""
import os
import tempfile

_TEST_DIR = tempfile.mkdtemp(prefix="mini-tms-fb-")
os.environ.setdefault("TMS_DATABASE_URL", f"sqlite:///{_TEST_DIR}/test.db".replace("\\", "/"))
os.environ.setdefault("TMS_DATA_DIR", os.path.join(_TEST_DIR, "data").replace("\\", "/"))
os.environ.setdefault("TMS_MT_PROVIDER", "mock")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import config  # noqa: E402
from app.main import app  # noqa: E402

ORG = 1


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def login(client: TestClient, username: str, password: str) -> dict:
    res = client.post("/api/login", json={"username": username, "password": password})
    assert res.status_code == 200, res.text
    token = res.json()["access_token"]
    return {"Authorization": f"Bearer {token}", "X-Org-Id": str(ORG)}


@pytest.fixture(scope="module")
def pm(client):
    return login(client, "pm", "pm123")


@pytest.fixture(scope="module")
def ann(client):
    return login(client, "ann", "ann123")


def test_send_and_read(client, pm, ann):
    # Без токена отправки нет
    assert client.post(
        "/api/feedback", json={"kind": "bug", "message": "anonymous message here"}
    ).status_code == 401

    # Переводчик отправляет баг со страницы редактора
    res = client.post("/api/feedback", headers=ann, json={
        "kind": "bug",
        "message": "Лок слетает после F5, второй редактор перехватывает сегмент",
        "page": "/editor/3",
    })
    assert res.status_code == 201, res.text
    fb = res.json()
    assert fb["kind"] == "bug"
    assert fb["author"]["username"] == "ann"
    assert fb["page"] == "/editor/3"
    assert fb["message"].startswith("Лок")

    # Переводчик ленту не читает — это инструмент менеджмента
    assert client.get("/api/feedback", headers=ann).status_code == 403

    # Менеджмент видит сообщение, новые впереди
    feed = client.get("/api/feedback", headers=pm).json()
    assert feed[0]["id"] == fb["id"]
    assert any("F5" in item["message"] for item in feed)


def test_validation(client, ann):
    # Недопустимый тип
    res = client.post("/api/feedback", headers=ann,
                      json={"kind": "spam", "message": "valid message text"})
    assert res.status_code == 422
    # Слишком короткий текст
    assert client.post("/api/feedback", headers=ann,
                       json={"kind": "idea", "message": "ок"}).status_code == 422
    # Пустая страница и неизвестный тип по умолчанию — ок (kind default)
    res = client.post("/api/feedback", headers=ann, json={"message": "just a thought"})
    assert res.status_code == 201 and res.json()["kind"] == "other"


def test_rate_limit(client, ann):
    """После FEEDBACK_MAX_PER_HOUR сообщений в час — 429, дальше тоже 429."""
    blocked = False
    for _ in range(config.FEEDBACK_MAX_PER_HOUR + 5):
        res = client.post("/api/feedback", headers=ann,
                          json={"kind": "other", "message": "rate limit probe message"})
        if res.status_code == 429:
            blocked = True
            break
    assert blocked, "429 not reached"
    assert client.post("/api/feedback", headers=ann,
                       json={"kind": "other", "message": "still blocked message"}).status_code == 429
