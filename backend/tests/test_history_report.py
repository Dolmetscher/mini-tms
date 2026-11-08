"""Тесты истории правок сегмента (track changes), отчета продуктивности
переводчиков и резервного экспорта данных организации.

Используется общая тестовая БД всего набора; сценарии изолированы
собственными проектами и отдельным переводчиком (zoe) — статистика
не смешивается с данными других тестов.
"""
import io
import os
import tempfile

_TEST_DIR = tempfile.mkdtemp(prefix="mini-tms-ops-")
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
def admin(client):
    return login(client, "admin", "admin123")


@pytest.fixture(scope="module")
def ann(client):
    return login(client, "ann", "ann123")


@pytest.fixture(scope="module")
def ivan(client):
    return login(client, "ivan", "ivan123")


@pytest.fixture(scope="module")
def zoe(client, admin):
    """Отдельный переводчик — чистая статистика для отчета продуктивности."""
    res = client.post(f"/api/orgs/{ORG}/members", headers=admin, json={
        "username": "zoe", "password": "zoe1234", "role": "translator",
    })
    assert res.status_code in (201, 409), res.text  # 409 — уже создан в этом процессе
    return login(client, "zoe", "zoe1234")


def _user_id(client: TestClient, pm: dict, username: str) -> int:
    members = client.get(f"/api/orgs/{ORG}/members", headers=pm).json()
    return next(m["user"]["id"] for m in members if m["user"]["username"] == username)


def _assign_accept(client: TestClient, pm: dict, who: dict, project: int, username: str) -> dict:
    job = client.get(f"/api/jobs?project_id={project}", headers=pm).json()[0]
    assert client.post(f"/api/jobs/{job['id']}/assign", headers=pm,
                       json={"assignee_id": _user_id(client, pm, username)}).status_code == 200
    assert client.post(f"/api/jobs/{job['id']}/accept", headers=who).status_code == 200
    return job


def _make_project(client: TestClient, pm: dict, name: str, lines: list[str]) -> int:
    res = client.post("/api/projects", headers=pm, json={
        "name": name, "source_lang": "en", "target_langs": ["ru"],
    })
    assert res.status_code == 201, res.text
    project = res.json()["id"]
    doc = io.BytesIO("\n".join(lines).encode())
    res = client.post(f"/api/projects/{project}/documents", headers=pm,
                      files={"file": (f"{name}.txt", doc, "text/plain")})
    assert res.status_code == 201, res.text
    return project


# --- История правок сегмента ---


def test_segment_history_track_changes(client, pm, ann, ivan):
    project = _make_project(client, pm, "History Tracks", [
        "History first segment.",
        "Repeated source line.",
        "Repeated source line.",
    ])
    job = _assign_accept(client, pm, ann, project, "ann")
    segs = client.get(f"/api/jobs/{job['id']}", headers=ann).json()["segments"]
    first = segs[0]

    # Чужой переводчик историю не видит
    assert client.get(f"/api/segments/{first['id']}/history", headers=ivan).status_code == 403
    assert client.get("/api/segments/999999/history", headers=ann).status_code == 404

    # Пустая история до правок
    assert client.get(f"/api/segments/{first['id']}/history", headers=ann).json() == []

    # Две правки текста + подтверждение без изменения текста
    assert client.patch(f"/api/segments/{first['id']}", headers=ann,
                        json={"target_text": "Version one"}).status_code == 200
    assert client.patch(f"/api/segments/{first['id']}", headers=ann,
                        json={"target_text": "Version two"}).status_code == 200
    assert client.patch(f"/api/segments/{first['id']}", headers=ann,
                        json={"confirmed": True}).status_code == 200

    history = client.get(f"/api/segments/{first['id']}/history", headers=ann).json()
    assert len(history) == 2  # подтверждение без правки не создает запись
    assert [h["target_text"] for h in history] == ["Version one", "Version two"]
    assert {h["author"]["username"] for h in history} == {"ann"}
    assert all(h["position"] == first["position"] for h in history)

    # PM корректирует — третья запись от другого автора
    assert client.patch(f"/api/segments/{first['id']}", headers=pm,
                        json={"target_text": "PM correction"}).status_code == 200
    history = client.get(f"/api/segments/{first['id']}/history", headers=pm).json()
    assert len(history) == 3
    assert history[-1]["author"]["username"] == "pm"
    assert history[-1]["target_text"] == "PM correction"


def test_history_records_pretranslate_and_propagation(client, pm, ann):
    # TM с точным совпадением для последней строки — пред-перевод оставит след
    project = _make_project(client, pm, "History Leverage", [
        "Twin source line.",
        "Twin source line.",
        "Pretranslated line here.",
    ])
    tm = client.post("/api/tm/tms", headers=pm, json={
        "name": "History TM", "source_lang": "en", "target_lang": "ru",
    }).json()
    assert client.post(f"/api/tm/tms/{tm['id']}/entries", headers=pm, json={
        "source_text": "Pretranslated line here.", "target_text": "Предпереведенная строка.",
    }).status_code == 201
    assert client.post(f"/api/projects/{project}/resources", headers=pm, json={
        "tm_id": tm["id"], "use_for_search": True, "use_for_write": False,
    }).status_code == 201

    job = _assign_accept(client, pm, ann, project, "ann")
    assert client.post(f"/api/jobs/{job['id']}/pre-translate", headers=ann,
                       json={"min_score": 100}).status_code == 200
    segs = client.get(f"/api/jobs/{job['id']}", headers=ann).json()["segments"]

    # Пред-перевод: автор записи — инициатор batch-действия (ann)
    history = client.get(f"/api/segments/{segs[2]['id']}/history", headers=ann).json()
    assert [h["target_text"] for h in history] == ["Предпереведенная строка."]
    assert history[0]["author"]["username"] == "ann"

    # Автопропагация: подтверждение первого «близнеца» заполняет пустой второй —
    # и это тоже фиксируется в истории (автор — подтвердивший)
    assert client.patch(f"/api/segments/{segs[0]['id']}", headers=ann, json={
        "target_text": "Строка-близнец, подтверждена.", "confirmed": True,
    }).status_code == 200
    assert segs[1]["target_text"] == ""  # было пусто до пропагации
    detail = client.get(f"/api/jobs/{job['id']}", headers=ann).json()
    assert detail["segments"][1]["target_text"] == "Строка-близнец, подтверждена."
    history = client.get(f"/api/segments/{segs[1]['id']}/history", headers=ann).json()
    assert [h["target_text"] for h in history] == ["Строка-близнец, подтверждена."]
    assert history[0]["author"]["username"] == "ann"


# --- Отчет продуктивности переводчиков ---


def test_productivity_report(client, pm, zoe):
    project = _make_project(client, pm, "Productivity Zoe", ["word " * 100])
    job = _assign_accept(client, pm, zoe, project, "zoe")
    seg = client.get(f"/api/jobs/{job['id']}", headers=zoe).json()["segments"][0]
    assert client.patch(f"/api/segments/{seg['id']}", headers=zoe, json={
        "target_text": "Перевод ста слов для отчета.", "confirmed": True,
    }).status_code == 200
    assert client.post(f"/api/jobs/{job['id']}/submit", headers=zoe).status_code == 200
    assert client.post(f"/api/jobs/{job['id']}/complete", headers=pm).status_code == 200

    # Отдельная LQA-разметка: 1 minor на 100 слов -> score 90.0
    assert client.post(f"/api/jobs/{job['id']}/lqa", headers=pm, json={
        "segment_id": seg["id"], "category": "style", "severity": "minor",
        "comment": "Стиль можно лучше",
    }).status_code == 201

    rows = client.get("/api/reports/productivity", headers=pm).json()
    zoe_row = next(r for r in rows if r["user"]["username"] == "zoe")
    assert zoe_row["jobs_assigned"] == 1
    assert zoe_row["jobs_completed"] == 1
    assert zoe_row["jobs_overdue"] == 0
    assert zoe_row["words_confirmed"] == 100
    assert zoe_row["lqa_jobs"] == 1
    assert zoe_row["avg_lqa_score"] == 90.0

    # Без LQA-ошибок среднее не считается (тривиальные 100 не завышают отчет)
    ivan_row = next(r for r in rows if r["user"]["username"] == "ivan")
    assert ivan_row["avg_lqa_score"] is None and ivan_row["lqa_jobs"] == 0

    # Периодный срез: заданий после 2100 года нет
    rows = client.get("/api/reports/productivity", headers=pm,
                      params={"updated_after": "2100-01-01T00:00:00"}).json()
    zoe_row = next(r for r in rows if r["user"]["username"] == "zoe")
    assert zoe_row["jobs_assigned"] == 0 and zoe_row["words_confirmed"] == 0


def test_productivity_report_permissions(client, ann):
    assert client.get("/api/reports/productivity", headers=ann).status_code == 403


# --- Резервный экспорт данных организации ---


def test_org_backup_export(client, pm, ann):
    res = client.get(f"/api/orgs/{ORG}/export", headers=ann)
    assert res.status_code == 403  # переводчику нельзя
    res = client.get("/api/orgs/999/export", headers=pm)
    assert res.status_code == 403  # чужая организация

    res = client.get(f"/api/orgs/{ORG}/export", headers=pm)
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith("application/json")
    assert "mini-tms-backup" in res.headers["content-disposition"]
    data = res.json()

    assert data["format"] == "mini-tms-org-backup"
    assert data["organization"]["id"] == ORG
    assert data["organization"]["name"] == "Demo Org"

    usernames = {m["username"] for m in data["members"]}
    assert {"admin", "pm", "ann", "ivan", "zoe"} <= usernames
    assert {"translator", "pm", "admin"} <= {m["role"] for m in data["members"]}

    # TM/TB и прайс-листы попадают в копию
    assert data["tms"] and data["tms"][0]["entries"]
    assert data["tbs"] and data["tbs"][0]["entries"]
    assert any(c["name"] == "Standard EN→RU" for c in data["rate_cards"])

    # Проекты с заданиями и сегментами; хеши паролей не выгружаются
    assert data["projects"]
    with_jobs = [p for p in data["projects"] if p["jobs"]]
    assert with_jobs
    job = with_jobs[0]["jobs"][0]
    assert job["segments"] and "source" in job["segments"][0]
    assert "password" not in data["members"][0]
    assert "password_hash" not in str(data["members"])
