"""RX-проверки QA: пользовательские правила на регулярных выражениях.

Правило организации: паттерн «должен / не должен встречаться» в исходнике
или переводе обязательного сегмента; severity=error блокирует сдачу.
Покрывает: права (pm/admin против переводчика), валидацию паттерна (422),
семантику must_match/must_not_match, чувствительность к регистру,
сторону source/target, включение/выключение и эндпоинт предпросмотра /test.
"""
import io
import os
import tempfile

_TEST_DIR = tempfile.mkdtemp(prefix="mini-tms-rx-")
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
    token = res.json()["access_token"]
    return {"Authorization": f"Bearer {token}", "X-Org-Id": str(ORG)}


@pytest.fixture(scope="module")
def pm(client):
    return login(client, "pm", "pm123")


@pytest.fixture(scope="module")
def ann(client):
    return login(client, "ann", "ann123")


SOURCE = "Save your changes now."


@pytest.fixture(scope="module")
def job(client, pm, ann):
    """Односегментное задание: ann принимает, далее правит перевод по сценариям."""
    res = client.post("/api/projects", headers=pm, json={
        "name": "RX QA", "source_lang": "en", "target_langs": ["ru"],
    })
    assert res.status_code == 201, res.text
    project = res.json()["id"]
    doc = io.BytesIO(SOURCE.encode())
    res = client.post(f"/api/projects/{project}/documents", headers=pm,
                      files={"file": ("rx.txt", doc, "text/plain")})
    assert res.status_code == 201, res.text
    job_id = client.get(f"/api/jobs?project_id={project}", headers=pm).json()[0]["id"]
    members = client.get(f"/api/orgs/{ORG}/members", headers=pm).json()
    ann_id = next(m["user"]["id"] for m in members if m["user"]["username"] == "ann")
    assert client.post(f"/api/jobs/{job_id}/assign", headers=pm,
                       json={"assignee_id": ann_id}).status_code == 200
    assert client.post(f"/api/jobs/{job_id}/accept", headers=ann).status_code == 200
    detail = client.get(f"/api/jobs/{job_id}", headers=ann).json()
    return detail


def _save_target(client: TestClient, ann: dict, job: dict, text: str) -> None:
    seg = job["segments"][0]
    res = client.patch(f"/api/segments/{seg['id']}", headers=ann,
                       json={"target_text": text, "confirmed": True})
    assert res.status_code == 200, res.text


def _qa(client: TestClient, ann: dict, job: dict) -> dict:
    res = client.get(f"/api/jobs/{job['id']}/qa", headers=ann)
    assert res.status_code == 200, res.text
    return res.json()


def _rx_items(report: dict) -> list[dict]:
    return [i for i in report["items"] if i["code"] == "regex_rule"]


def test_permissions_and_pattern_validation(client, pm, ann):
    # Переводчик не управляет правилами качества — только ревьюер
    res = client.post("/api/qa-rules", headers=ann, json={
        "name": " translator rule", "pattern": "x",
    })
    assert res.status_code == 403
    assert client.delete("/api/qa-rules/999999", headers=ann).status_code == 403

    # Невалидный паттерн отклоняется с 422 и текстом ошибки
    res = client.post("/api/qa-rules", headers=pm, json={
        "name": "Broken", "pattern": "(unclosed",
    })
    assert res.status_code == 422, res.text
    assert "регулярное выражение" in res.json()["detail"]

    # /test с невалидным паттерном возвращает ошибку в теле (не 500)
    res = client.post("/api/qa-rules/test", headers=pm,
                      json={"pattern": "(bad", "case_sensitive": False, "text": "any"})
    assert res.status_code == 200 and res.json()["error"]


def test_forbidden_pattern_error_blocks_submit(client, pm, ann, job):
    res = client.post("/api/qa-rules", headers=pm, json={
        "name": "No TODO left", "pattern": "TODO", "side": "target",
        "must_match": False, "severity": "error",
        "description": "Заглушки не сдаются заказчику",
    })
    assert res.status_code == 201, res.text
    rule = res.json()
    assert rule["enabled"] is True

    # Список правил виден и переводчику (требования должны быть известны)
    assert any(r["name"] == "No TODO left" for r in client.get("/api/qa-rules", headers=ann).json())

    _save_target(client, ann, job, "TODO: перевести позже")
    report = _qa(client, ann, job)
    hits = [i for i in _rx_items(report) if i["severity"] == "error" and "TODO" in i["message"]]
    assert hits, report["items"]
    assert not report["can_submit"]

    # /test предпросмотр: показывает найденный фрагмент
    res = client.post("/api/qa-rules/test", headers=pm,
                      json={"pattern": "TODO", "case_sensitive": True,
                            "text": "clean text, but TODO remains"})
    assert res.json() == {"matches": ["TODO"], "error": None}

    # Чистый перевод: нарушения правила больше нет
    _save_target(client, ann, job, "Сохраните изменения.")
    assert _rx_items(_qa(client, ann, job)) == []


def test_required_pattern_warning(client, pm, ann, job):
    res = client.post("/api/qa-rules", headers=pm, json={
        "name": "Typographic quotes", "pattern": "«.+»", "side": "target",
        "must_match": True, "severity": "warning",
    })
    assert res.status_code == 201, res.text

    # Перевод без «ёлочек» — предупреждение, но не блокировка
    _save_target(client, ann, job, "Сохраните свои изменения.")
    report = _qa(client, ann, job)
    warnings = [i for i in _rx_items(report)
                if i["severity"] == "warning" and "обязательный паттерн не найден" in i["message"]]
    assert warnings and report["can_submit"]

    # С «ёлочками» — чисто
    _save_target(client, ann, job, "«Сохраните свои изменения.»")
    assert _rx_items(_qa(client, ann, job)) == []


def test_case_sensitivity_and_source_side(client, pm, ann, job):
    # Чувствительное к регистру правило: «сохраните» со строчной не встречается
    res = client.post("/api/qa-rules", headers=pm, json={
        "name": "lowercase verb", "pattern": "сохраните", "side": "target",
        "must_match": False, "severity": "warning", "case_sensitive": True,
    })
    assert res.status_code == 201, res.text
    rule_id = res.json()["id"]

    _save_target(client, ann, job, "«Сохраните свои изменения.»")
    assert _rx_items(_qa(client, ann, job)) == []

    # Тот же паттерн без учета регистра уже ловит «Сохраните»
    res = client.patch(f"/api/qa-rules/{rule_id}", headers=pm, json={"case_sensitive": False})
    assert res.status_code == 200 and res.json()["case_sensitive"] is False
    assert _rx_items(_qa(client, ann, job))

    # Правило по исходнику: исходник без заглушки — нарушение «должен совпадать»
    res = client.post("/api/qa-rules", headers=pm, json={
        "name": "source placeholder", "pattern": "PLACEHOLDER_", "side": "source",
        "must_match": True, "severity": "warning",
    })
    assert res.status_code == 201, res.text
    report = _qa(client, ann, job)
    assert any("source placeholder" in i["message"] for i in _rx_items(report))

    # Выключенное правило не применяется
    res = client.patch(f"/api/qa-rules/{rule_id}", headers=pm, json={"enabled": False})
    assert res.status_code == 200
    remaining = [
        i for i in _rx_items(_qa(client, ann, job)) if "lowercase verb" in i["message"]
    ]
    assert remaining == []


def test_rule_delete(client, pm):
    res = client.post("/api/qa-rules", headers=pm, json={
        "name": "Disposable", "pattern": "disposable-\\d+",
    })
    rule_id = res.json()["id"]
    assert client.delete(f"/api/qa-rules/{rule_id}", headers=pm).status_code == 204
    assert client.delete(f"/api/qa-rules/{rule_id}", headers=pm).status_code == 404
    assert all(r["id"] != rule_id for r in client.get("/api/qa-rules", headers=pm).json())
