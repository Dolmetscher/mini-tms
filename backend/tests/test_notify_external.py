"""Внешние уведомления (бета): email/SMTP и Telegram, контакты пользователя.

Каналы и доставка мокаются (monkeypatch): SMTP-функция и Telegram-функция
заменяются на захват вызовов, фоновый поток — на синхронный запуск. Ценное
здесь не сеть, а маршрутизация: кому, когда и с каким текстом.
"""
import os
import tempfile
from types import SimpleNamespace

_TEST_DIR = tempfile.mkdtemp(prefix="mini-tms-notify-")
os.environ.setdefault("TMS_DATABASE_URL", f"sqlite:///{_TEST_DIR}/test.db".replace("\\", "/"))
os.environ.setdefault("TMS_DATA_DIR", os.path.join(_TEST_DIR, "data").replace("\\", "/"))
os.environ.setdefault("TMS_MT_PROVIDER", "mock")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import config  # noqa: E402
from app.main import app  # noqa: E402
from app.services import notify as notify_service  # noqa: E402
from app.services.notify import _render  # noqa: E402

ORG = 1


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def login(client: TestClient, username: str, password: str) -> dict:
    res = client.post("/api/login", json={"username": username, "password": password})
    assert res.status_code == 200, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}", "X-Org-Id": str(ORG)}


@pytest.fixture(scope="module")
def pm(client):
    return login(client, "pm", "pm123")


@pytest.fixture(scope="module")
def ann(client):
    return login(client, "ann", "ann123")


@pytest.fixture
def smtp_calls(monkeypatch):
    """SMTP и Telegram мокаются; поток выполняется синхронно."""
    calls = {"smtp": [], "tg": []}

    def fake_send_smtp(to_email, subject, body):
        calls["smtp"].append((to_email, subject, body))

    def fake_send_telegram(chat_id, text):
        calls["tg"].append((chat_id, text))

    class InlineThread:
        def __init__(self, target=None, args=(), daemon=None):
            self._target, self._args = target, args

        def start(self):
            self._target(*self._args)

    monkeypatch.setattr(notify_service, "_send_smtp", fake_send_smtp)
    monkeypatch.setattr(notify_service, "_send_telegram", fake_send_telegram)
    monkeypatch.setattr(
        notify_service, "threading", SimpleNamespace(Thread=InlineThread)
    )
    return calls


@pytest.fixture
def smtp_enabled(monkeypatch):
    monkeypatch.setattr(config, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(config, "SMTP_FROM", "tms@example.com")


@pytest.fixture
def tg_enabled(monkeypatch):
    monkeypatch.setattr(config, "TG_BOT_TOKEN", "bot-token-xyz")


def _assign_job(client, pm) -> int:
    res = client.post("/api/projects", headers=pm, json={
        "name": "Notify project", "source_lang": "en", "target_langs": ["ru"],
    })
    assert res.status_code == 201, res.text
    project = res.json()["id"]
    res = client.post(f"/api/projects/{project}/documents", headers=pm,
                      files={"file": ("n.txt", __import__("io").BytesIO(b"Hello world."), "text/plain")})
    assert res.status_code == 201, res.text
    job_id = client.get(f"/api/jobs?project_id={project}", headers=pm).json()[0]["id"]
    members = client.get(f"/api/orgs/{ORG}/members", headers=pm).json()
    ann_id = next(m["user"]["id"] for m in members if m["user"]["username"] == "ann")
    assert client.post(f"/api/jobs/{job_id}/assign", headers=pm,
                       json={"assignee_id": ann_id}).status_code == 200
    return job_id


def test_contacts_update_and_validation(client, ann):
    res = client.patch("/api/me", headers={"Authorization": ann["Authorization"]},
                       json={"email": "ann@example.com", "telegram_chat_id": "123456789"})
    assert res.status_code == 200, res.text
    assert res.json()["email"] == "ann@example.com"
    assert res.json()["telegram_chat_id"] == "123456789"

    # /me возвращает контакты
    res = client.get("/api/me", headers={"Authorization": ann["Authorization"]})
    assert res.json()["user"]["email"] == "ann@example.com"

    # Валидация
    assert client.patch("/api/me", headers={"Authorization": ann["Authorization"]},
                        json={"email": "not-an-email"}).status_code == 422
    assert client.patch("/api/me", headers={"Authorization": ann["Authorization"]},
                        json={"telegram_chat_id": "не чат"}).status_code == 422

    # @username допустим; пустая строка чистит канал
    res = client.patch("/api/me", headers={"Authorization": ann["Authorization"]},
                       json={"telegram_chat_id": "@translator"})
    assert res.status_code == 200 and res.json()["telegram_chat_id"] == "@translator"
    res = client.patch("/api/me", headers={"Authorization": ann["Authorization"]},
                       json={"telegram_chat_id": ""})
    assert res.status_code == 200 and res.json()["telegram_chat_id"] is None


def test_email_delivered_on_assignment(client, pm, ann, smtp_calls, smtp_enabled):
    client.patch("/api/me", headers={"Authorization": ann["Authorization"]},
                 json={"email": "ann@example.com"})
    _assign_job(client, pm)
    assert len(smtp_calls["smtp"]) == 1
    to_email, subject, body = smtp_calls["smtp"][0]
    assert to_email == "ann@example.com"
    assert "New job assigned" in subject
    assert "n.txt" in body  # документ из payload — в тексте письма


def test_telegram_delivered_on_assignment(client, pm, ann, smtp_calls, tg_enabled):
    client.patch("/api/me", headers={"Authorization": ann["Authorization"]},
                 json={"telegram_chat_id": "123456789"})
    _assign_job(client, pm)
    assert len(smtp_calls["tg"]) == 1
    chat_id, text = smtp_calls["tg"][0]
    assert chat_id == "123456789"
    assert "New job assigned" in text


def test_no_delivery_when_channel_disabled(client, pm, ann, smtp_calls):
    """Канал не настроен (SMTP_HOST пуст, токена нет) — сеть не трогаем вовсе."""
    client.patch("/api/me", headers={"Authorization": ann["Authorization"]},
                 json={"email": "ann@example.com", "telegram_chat_id": "123456789"})
    _assign_job(client, pm)
    assert smtp_calls["smtp"] == []
    assert smtp_calls["tg"] == []


def test_no_delivery_without_contacts(client, pm, ann, smtp_calls, smtp_enabled, tg_enabled):
    """Каналы настроены, но у получателя нет контактов — никто не зовется."""
    client.patch("/api/me", headers={"Authorization": ann["Authorization"]},
                 json={"email": "", "telegram_chat_id": ""})
    _assign_job(client, pm)
    assert smtp_calls["smtp"] == [] and smtp_calls["tg"] == []


def test_render_contains_payload_fields():
    subject, body = _render("deadline_reminder", {
        "job_id": 7, "document": "manual.docx", "deadline": "2026-01-01T10:00:00",
        "offset": "24h",
    })
    assert subject == "Mini-TMS: Deadline reminder"
    assert "manual.docx" in body and "24h" in body and "#7" in body
