"""Псевдо-перевод: проверка экспорт-пайплайна синтетическим текстом.

Покрывает: заполнение только пустых сегментов (overwrite=False) и полный
прогон (overwrite=True); сохранность тегов, чисел и плейсхолдеров; статус
«переведен, не подтвержден»; QA чисел/плейсхолдеров проходит на псевдо-тексте;
доступ — исполнитель/менеджмент, чужой переводчик — 403.
"""
import io
import os
import tempfile

_TEST_DIR = tempfile.mkdtemp(prefix="mini-tms-pseudo-")
os.environ.setdefault("TMS_DATABASE_URL", f"sqlite:///{_TEST_DIR}/test.db".replace("\\", "/"))
os.environ.setdefault("TMS_DATA_DIR", os.path.join(_TEST_DIR, "data").replace("\\", "/"))
os.environ.setdefault("TMS_MT_PROVIDER", "mock")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services.pseudo import pseudo_text  # noqa: E402

ORG = 1


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def login(client: TestClient, username: str, password: str) -> dict:
    res = client.post("/api/login", json={"username": username, "password": password})
    assert res.status_code == 200, res.text
    return {
        "Authorization": f"Bearer {res.json()['access_token']}",
        "X-Org-Id": str(ORG),
    }


@pytest.fixture(scope="module")
def pm(client):
    return login(client, "pm", "pm123")


@pytest.fixture(scope="module")
def ann(client):
    return login(client, "ann", "ann123")


@pytest.fixture(scope="module")
def ivan(client):
    return login(client, "ivan", "ivan123")


SOURCE = 'Save 50% via <x id="1"/> portal in 2024.'


@pytest.fixture(scope="module")
def job(client, pm, ann):
    res = client.post("/api/projects", headers=pm, json={
        "name": "Pseudo pipeline", "source_lang": "en", "target_langs": ["ru"],
    })
    assert res.status_code == 201, res.text
    project = res.json()["id"]
    doc = io.BytesIO(SOURCE.encode())
    res = client.post(f"/api/projects/{project}/documents", headers=pm,
                      files={"file": ("pseudo.txt", doc, "text/plain")})
    assert res.status_code == 201, res.text
    job_id = client.get(f"/api/jobs?project_id={project}", headers=pm).json()[0]["id"]
    members = client.get(f"/api/orgs/{ORG}/members", headers=pm).json()
    ann_id = next(m["user"]["id"] for m in members if m["user"]["username"] == "ann")
    assert client.post(f"/api/jobs/{job_id}/assign", headers=pm,
                       json={"assignee_id": ann_id}).status_code == 200
    assert client.post(f"/api/jobs/{job_id}/accept", headers=ann).status_code == 200
    return client.get(f"/api/jobs/{job_id}", headers=ann).json()


def test_pseudo_text_transform():
    """Единица трансформации: буквы — с диакритикой и удлинением, теги/числа — как были."""
    out = pseudo_text('Save 50% via <x id="1"/> portal in 2024.')
    assert out.startswith("[") and out.endswith("]")
    assert '<x id="1"/>' in out  # тег сохранен дословно
    assert "50%" in out and "2024" in out  # числа и проценты не тронуты
    assert "Save" not in out  # буквы искажены
    assert len(out) > len("Save 50% via  portal in 2024.")  # удлиннение ~40%


def test_fill_empty_only_by_default(client, pm, ann, ivan, job):
    # Переводчик заранее переводит один сегмент вручную
    seg = job["segments"][0]
    res = client.patch(f"/api/segments/{seg['id']}", headers=ann,
                       json={"target_text": "Сохраните 50%.", "confirmed": True})
    assert res.status_code == 200, res.text

    # Чужой переводчик не может запустить псевдо-перевод
    res = client.post(f"/api/jobs/{job['id']}/pseudo-translate", headers=ivan,
                      json={"overwrite": False})
    assert res.status_code == 403

    res = client.post(f"/api/jobs/{job['id']}/pseudo-translate", headers=ann,
                      json={"overwrite": False})
    assert res.status_code == 200, res.text
    assert res.json() == {"filled": 0, "skipped_nonempty": 1}

    # Полный прогон: и заполненный сегмент переписывается псевдо-текстом
    res = client.post(f"/api/jobs/{job['id']}/pseudo-translate", headers=pm,
                      json={"overwrite": True})
    assert res.status_code == 200
    assert res.json()["filled"] >= 1

    detail = client.get(f"/api/jobs/{job['id']}", headers=ann).json()
    seg = detail["segments"][0]
    assert seg["target_text"].startswith("[")
    assert seg["translation_status"] == "translated"
    assert seg["confirmed"] is False  # псевдо-текст нельзя подтвердить по ошибке
    assert seg["target_text"] == pseudo_text(seg["source_text"])


def test_qa_passes_on_pseudo(client, ann, job):
    """QA чисел и плейсхолдеров проходит на псевдо-тексте — пайплайн проверяем."""
    res = client.get(f"/api/jobs/{job['id']}/qa", headers=ann)
    assert res.status_code == 200, res.text
    report = res.json()
    codes = {item["code"] for item in report["items"]}
    assert "number_mismatch" not in codes
    assert "placeholder" not in codes
    assert "tag_error" not in codes


def test_uneditable_status_conflict(client, pm, ann, job):
    """В сданном задании псевдо-перевод запрещен (409)."""
    detail = client.get(f"/api/jobs/{job['id']}", headers=ann).json()
    seg = detail["segments"][0]
    client.patch(f"/api/segments/{seg['id']}", headers=ann,
                 json={"target_text": 'Сохраните 50% на портале <x id="1"/> в 2024 году.', "confirmed": True})
    assert client.post(f"/api/jobs/{job['id']}/submit", headers=ann).status_code == 200
    res = client.post(f"/api/jobs/{job['id']}/pseudo-translate", headers=pm,
                      json={"overwrite": True})
    assert res.status_code == 409
