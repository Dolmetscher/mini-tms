"""Тесты обслуживания записей TM/TB (maintenance): правка и удаление.

Практика ведения ресурсов: устаревший перевод в памяти или неверный термин
должны правиться/удаляться на месте — иначе ресурс работает против
качества. Права — менеджмент (pm/admin); переводчик читает через проект.
"""
import os
import tempfile

_TEST_DIR = tempfile.mkdtemp(prefix="mini-tms-mnt-")
os.environ.setdefault("TMS_DATABASE_URL", f"sqlite:///{_TEST_DIR}/test.db".replace("\\", "/"))
os.environ.setdefault("TMS_DATA_DIR", os.path.join(_TEST_DIR, "data").replace("\\", "/"))
os.environ.setdefault("TMS_MT_PROVIDER", "mock")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

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


# --- TM: правка и удаление записей ---


def test_tm_entry_update_and_delete(client, pm, ann):
    res = client.post("/api/tm/tms", headers=pm, json={
        "name": "Maintenance TM", "source_lang": "en", "target_lang": "pl",
    })
    assert res.status_code in (201, 409), res.text
    tm_id = res.json()["id"] if res.status_code == 201 else next(
        t["id"] for t in client.get("/api/tm/tms", headers=pm).json()
        if t["name"] == "Maintenance TM"
    )

    entry = client.post(f"/api/tm/tms/{tm_id}/entries", headers=pm, json={
        "source_text": "Maintenance check line.", "target_text": "Stary tłumaczenie.",
    }).json()

    # Переводчик не правит память (права менеджмента)
    assert client.patch(f"/api/tm/tms/{tm_id}/entries/{entry['id']}", headers=ann, json={
        "target_text": "x",
    }).status_code == 403

    # Правка перевода: исходник не меняется (ключ памяти)
    res = client.patch(f"/api/tm/tms/{tm_id}/entries/{entry['id']}", headers=pm, json={
        "target_text": "Nowe tłumaczenie.",
    })
    assert res.status_code == 200, res.text
    assert res.json()["source_text"] == "Maintenance check line."
    assert res.json()["target_text"] == "Nowe tłumaczenie."

    # Подсказка отдает исправленный перевод
    suggestions = client.get("/api/tm/suggest", headers=pm, params={
        "text": "Maintenance check line.", "source_lang": "en", "target_lang": "pl",
    }).json()
    assert any(s["target_suggestion"] == "Nowe tłumaczenie." for s in suggestions)

    # Пустой перевод — 422 (мусорная запись не нужна)
    assert client.patch(f"/api/tm/tms/{tm_id}/entries/{entry['id']}", headers=pm, json={
        "target_text": "",
    }).status_code == 422

    # Удаление: запись перестает подсказываться
    assert client.delete(f"/api/tm/tms/{tm_id}/entries/{entry['id']}", headers=pm).status_code == 204
    assert client.delete(f"/api/tm/tms/{tm_id}/entries/{entry['id']}", headers=pm).status_code == 404
    suggestions = client.get("/api/tm/suggest", headers=pm, params={
        "text": "Maintenance check line.", "source_lang": "en", "target_lang": "pl",
    }).json()
    assert not any(s["source_match"] == "Maintenance check line." for s in suggestions)

    # Чужая запись (из другой TM организации) — 404
    other = client.post("/api/tm/tms", headers=pm, json={
        "name": "Maintenance TM 2", "source_lang": "en", "target_lang": "pl",
    })
    if other.status_code == 201:
        other_entry = client.post(f"/api/tm/tms/{other.json()['id']}/entries", headers=pm, json={
            "source_text": "Cross TM line.", "target_text": "Przekład.",
        }).json()
        assert client.patch(
            f"/api/tm/tms/{tm_id}/entries/{other_entry['id']}", headers=pm,
            json={"target_text": "x"},
        ).status_code == 404


# --- TB: правка и удаление терминов ---


def test_tb_entry_update_and_delete(client, pm, ann):
    res = client.post("/api/tb/tbs", headers=pm, json={
        "name": "Maintenance TB", "source_lang": "en", "target_lang": "pl",
    })
    assert res.status_code in (201, 409), res.text
    tb_id = res.json()["id"] if res.status_code == 201 else next(
        t["id"] for t in client.get("/api/tb/tbs", headers=pm).json()
        if t["name"] == "Maintenance TB"
    )

    first = client.post(f"/api/tb/tbs/{tb_id}/entries", headers=pm, json={
        "term": "engine", "translation": "silnik", "status": "preferred",
    }).json()
    second = client.post(f"/api/tb/tbs/{tb_id}/entries", headers=pm, json={
        "term": "gearbox", "translation": "skrzynia biegów", "status": "admitted",
    }).json()

    # Переводчик не правит терминологию
    assert client.patch(f"/api/tb/tbs/{tb_id}/entries/{first['id']}", headers=ann, json={
        "translation": "x",
    }).status_code == 403

    # Частичная правка: статус и примечание
    res = client.patch(f"/api/tb/tbs/{tb_id}/entries/{first['id']}", headers=pm, json={
        "note": "w motoryzacji", "status": "admitted",
    })
    assert res.status_code == 200, res.text
    assert res.json()["term"] == "engine"
    assert res.json()["translation"] == "silnik"
    assert res.json()["note"] == "w motoryzacji"
    assert res.json()["status"] == "admitted"

    # Смена термина на существующий — дубль, 409
    assert client.patch(f"/api/tb/tbs/{tb_id}/entries/{first['id']}", headers=pm, json={
        "term": "gearbox",
    }).status_code == 409

    # Смена термина на свободный — ок
    res = client.patch(f"/api/tb/tbs/{tb_id}/entries/{first['id']}", headers=pm, json={
        "term": "combustion engine",
    })
    assert res.status_code == 200, res.text
    assert res.json()["term"] == "combustion engine"

    # Неверный статус — 422 (Literal)
    assert client.patch(f"/api/tb/tbs/{tb_id}/entries/{first['id']}", headers=pm, json={
        "status": "bogus",
    }).status_code == 422

    # Удаление: термин исчезает из базы
    assert client.delete(f"/api/tb/tbs/{tb_id}/entries/{second['id']}", headers=pm).status_code == 204
    assert client.delete(f"/api/tb/tbs/{tb_id}/entries/{second['id']}", headers=pm).status_code == 404
    entries = client.get(f"/api/tb/tbs/{tb_id}/entries", headers=pm).json()
    assert all(e["id"] != second["id"] for e in entries)
