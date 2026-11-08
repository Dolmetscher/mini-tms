"""Сегментные локи: кто редактирует — тот блокирует.

Мягкий лок с TTL: чужой свежий лок дает 423 на захват и на сохранение,
свой лок продлевается, просроченный перехватывается, менеджмент снимает
чужой принудительно. Смена статуса задания (submit/complete/return/cancel)
снимает локи — редактирование закрыто.
"""
import io
import os
import tempfile
from datetime import timedelta

_TEST_DIR = tempfile.mkdtemp(prefix="mini-tms-lock-")
os.environ.setdefault("TMS_DATABASE_URL", f"sqlite:///{_TEST_DIR}/test.db".replace("\\", "/"))
os.environ.setdefault("TMS_DATA_DIR", os.path.join(_TEST_DIR, "data").replace("\\", "/"))
os.environ.setdefault("TMS_MT_PROVIDER", "mock")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import config, models  # noqa: E402
from app.database import SessionLocal  # noqa: E402
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


@pytest.fixture(scope="module")
def ivan(client):
    return login(client, "ivan", "ivan123")


def _user_id(client: TestClient, pm: dict, username: str) -> int:
    members = client.get(f"/api/orgs/{ORG}/members", headers=pm).json()
    return next(m["user"]["id"] for m in members if m["user"]["username"] == username)


def _accepted_job(client: TestClient, pm: dict, ann: dict, name: str, lines: list[str]) -> dict:
    """Проект + документ + задание, назначенное ann и принятое (редактируемое)."""
    res = client.post("/api/projects", headers=pm, json={
        "name": name, "source_lang": "en", "target_langs": ["ru"],
    })
    assert res.status_code == 201, res.text
    project = res.json()["id"]
    doc = io.BytesIO("\n".join(lines).encode())
    res = client.post(f"/api/projects/{project}/documents", headers=pm,
                      files={"file": (f"{name}.txt", doc, "text/plain")})
    assert res.status_code == 201, res.text
    job = client.get(f"/api/jobs?project_id={project}", headers=pm).json()[0]
    assert client.post(f"/api/jobs/{job['id']}/assign", headers=pm,
                       json={"assignee_id": _user_id(client, pm, "ann")}).status_code == 200
    assert client.post(f"/api/jobs/{job['id']}/accept", headers=ann).status_code == 200
    return client.get(f"/api/jobs/{job['id']}", headers=ann).json()


def test_lock_blocks_other_editors(client, pm, ann, ivan):
    job = _accepted_job(client, pm, ann, "Lock Basic", ["First line.", "Second line."])
    seg = job["segments"][0]

    # ann захватывает лок: ответ содержит владельца и TTL
    res = client.post(f"/api/segments/{seg['id']}/lock", headers=ann)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["locked_by"]["username"] == "ann"
    assert body["ttl_seconds"] == config.SEGMENT_LOCK_TTL_SECONDS

    # Лок виден в деталях задания
    detail = client.get(f"/api/jobs/{job['id']}", headers=pm).json()
    assert [(l["segment_id"], l["user"]["username"]) for l in detail["locks"]] == [
        (seg["id"], "ann"),
    ]

    # Чужой захват -> 423 с данными владельца
    res = client.post(f"/api/segments/{seg['id']}/lock", headers=pm)
    assert res.status_code == 423, res.text
    assert res.json()["detail"]["locked_by"]["username"] == "ann"

    # Чужое сохранение -> 423 (перевод не перезатирается)
    res = client.patch(f"/api/segments/{seg['id']}", headers=pm,
                       json={"target_text": "PM влез без очереди."})
    assert res.status_code == 423, res.text

    # Сторонний переводчик вообще не имеет доступа к сегменту
    res = client.post(f"/api/segments/{seg['id']}/lock", headers=ivan)
    assert res.status_code == 403

    # Свой лок продлевается (heartbeat), сохранение проходит
    res = client.post(f"/api/segments/{seg['id']}/lock", headers=ann)
    assert res.status_code == 200
    res = client.patch(f"/api/segments/{seg['id']}", headers=ann,
                       json={"target_text": "Первая строка перевода."})
    assert res.status_code == 200, res.text

    # ann снимает лок; повторное снятие (лока нет) -> 404
    assert client.delete(f"/api/segments/{seg['id']}/lock", headers=ann).status_code == 204
    res = client.delete(f"/api/segments/{seg['id']}/lock", headers=ann)
    assert res.status_code == 404

    # После снятия pm сохраняет: сохранение само захватывает лок редактора
    res = client.patch(f"/api/segments/{seg['id']}", headers=pm,
                       json={"target_text": "Правка менеджера."})
    assert res.status_code == 200, res.text
    detail = client.get(f"/api/jobs/{job['id']}", headers=pm).json()
    assert [(l["segment_id"], l["user"]["username"]) for l in detail["locks"]] == [
        (seg["id"], "pm"),
    ]


def test_stale_lock_is_taken_over(client, pm, ann):
    job = _accepted_job(client, pm, ann, "Lock Stale", ["Only line."])
    seg = job["segments"][0]

    assert client.post(f"/api/segments/{seg['id']}/lock", headers=ann).status_code == 200
    # Вкладка ann умерла без снятия: лок искусственно стареет вдвое против TTL
    with SessionLocal() as db:
        lock = db.query(models.SegmentLock).filter_by(segment_id=seg["id"]).one()
        lock.locked_at = lock.locked_at - timedelta(seconds=config.SEGMENT_LOCK_TTL_SECONDS * 2)
        db.commit()

    # pm перехватывает просроченный лок без 423
    res = client.post(f"/api/segments/{seg['id']}/lock", headers=pm)
    assert res.status_code == 200, res.text
    assert res.json()["locked_by"]["username"] == "pm"


def test_manager_force_releases_foreign_lock(client, pm, ann):
    job = _accepted_job(client, pm, ann, "Lock Force", ["Text to translate."])
    seg = job["segments"][0]

    assert client.post(f"/api/segments/{seg['id']}/lock", headers=ann).status_code == 200
    # Менеджмент снимает чужой лок (переводчик ушел с занятым сегментом)
    assert client.delete(f"/api/segments/{seg['id']}/lock", headers=pm).status_code == 204
    detail = client.get(f"/api/jobs/{job['id']}", headers=pm).json()
    assert detail["locks"] == []
    # ...и сразу занимает сегмент себе
    res = client.post(f"/api/segments/{seg['id']}/lock", headers=pm)
    assert res.status_code == 200 and res.json()["locked_by"]["username"] == "pm"


def test_submit_releases_locks_and_lock_needs_editable_status(client, pm, ann):
    job = _accepted_job(client, pm, ann, "Lock Submit", ["Line one.", "Line two."])
    for seg in job["segments"]:
        res = client.patch(f"/api/segments/{seg['id']}", headers=ann,
                           json={"target_text": "Готовый перевод.", "confirmed": True})
        assert res.status_code == 200, res.text

    held = job["segments"][0]
    assert client.post(f"/api/segments/{held['id']}/lock", headers=ann).status_code == 200
    assert client.post(f"/api/jobs/{job['id']}/submit", headers=ann).status_code == 200

    # Сдача сняла локи: задание не редактируется, локи не имеют смысла
    detail = client.get(f"/api/jobs/{job['id']}", headers=pm).json()
    assert detail["locks"] == []

    # На несдаваемом статусе лок не выдается (редактирование закрыто)
    res = client.post(f"/api/segments/{held['id']}/lock", headers=pm)
    assert res.status_code == 409
