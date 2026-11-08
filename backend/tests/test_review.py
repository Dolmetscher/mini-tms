"""Тесты второго прохода развития: LQA-оценка качества, комментарии к
сегментам, TBX-обмен терминологией.

Используется та же тестовая БД, что и в test_cat.py/test_leverage.py
(модули импортируются в одном процессе); сценарии изолированы
собственными проектами/TB.
"""
import io
import os
import tempfile

_TEST_DIR = tempfile.mkdtemp(prefix="mini-tms-rev-")
os.environ.setdefault("TMS_DATABASE_URL", f"sqlite:///{_TEST_DIR}/test.db".replace("\\", "/"))
os.environ.setdefault("TMS_DATA_DIR", os.path.join(_TEST_DIR, "data").replace("\\", "/"))
os.environ.setdefault("TMS_MT_PROVIDER", "mock")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from lxml import etree  # noqa: E402

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


def _ann_id(client: TestClient, pm: dict) -> int:
    members = client.get(f"/api/orgs/{ORG}/members", headers=pm).json()
    return next(m["user"]["id"] for m in members if m["user"]["username"] == "ann")


def _submitted_job(client: TestClient, pm: dict, ann: dict, name: str, lines: list[str]) -> dict:
    """Проект + документ + задание: ann перевел и подтвердил всё, задание сдано."""
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
                       json={"assignee_id": _ann_id(client, pm)}).status_code == 200
    assert client.post(f"/api/jobs/{job['id']}/accept", headers=ann).status_code == 200
    detail = client.get(f"/api/jobs/{job['id']}", headers=ann).json()
    for seg in detail["segments"]:
        res = client.patch(f"/api/segments/{seg['id']}", headers=ann,
                           json={"target_text": "Перевод строки для проверки.", "confirmed": True})
        assert res.status_code == 200, res.text
    assert client.post(f"/api/jobs/{job['id']}/submit", headers=ann).status_code == 200
    return job


# --- LQA: оценка качества перевода ---


def test_lqa_scoring_and_permissions(client, pm, ann, ivan):
    lines = ["term " * 50, "another " * 50]  # 2 сегмента × 50 слов = 100 слов
    job = _submitted_job(client, pm, ann, "LQA Scoring", lines)
    detail = client.get(f"/api/jobs/{job['id']}", headers=ann).json()
    first, second = detail["segments"][0], detail["segments"][1]

    # Пустой отчет: ошибок нет, оценка 100, зачет
    res = client.get(f"/api/jobs/{job['id']}/lqa", headers=ann)
    assert res.status_code == 200, res.text
    report = res.json()
    assert report["score"] == 100.0 and report["passed"] and report["error_count"] == 0

    # Переводчик не размечает ошибки — только ревьюер (pm/admin)
    res = client.post(f"/api/jobs/{job['id']}/lqa", headers=ann, json={
        "segment_id": first["id"], "category": "fluency", "severity": "minor",
        "comment": "Опечатка",
    })
    assert res.status_code == 403

    # PM размечает: minor (1 балл) + major (5) = 6 баллов на 100 слов
    # -> score = 100 - 6*1000/100 = 40.0, незачет
    res = client.post(f"/api/jobs/{job['id']}/lqa", headers=pm, json={
        "segment_id": first["id"], "category": "terminology", "severity": "minor",
        "comment": "Термин 'term' переведен не по TB",
    })
    assert res.status_code == 201, res.text
    minor = res.json()
    assert minor["penalty"] == 1.0 and minor["position"] == first["position"]

    res = client.post(f"/api/jobs/{job['id']}/lqa", headers=pm, json={
        "segment_id": second["id"], "category": "accuracy", "severity": "major",
        "comment": "Пропущена часть предложения",
    })
    assert res.status_code == 201
    major = res.json()
    assert major["penalty"] == 5.0

    report = client.get(f"/api/jobs/{job['id']}/lqa", headers=pm).json()
    assert report["error_count"] == 2
    assert report["penalty_points"] == 6.0
    assert report["score"] == 40.0
    assert not report["passed"]
    assert {c["category"]: c["count"] for c in report["by_category"]} == {
        "terminology": 1, "accuracy": 1,
    }

    # Некорректные category/severity/segment_id — 422/404
    assert client.post(f"/api/jobs/{job['id']}/lqa", headers=pm, json={
        "segment_id": first["id"], "category": "unknown", "severity": "minor",
        "comment": "x",
    }).status_code == 422
    assert client.post(f"/api/jobs/{job['id']}/lqa", headers=pm, json={
        "segment_id": first["id"], "category": "accuracy", "severity": "huge",
        "comment": "x",
    }).status_code == 422
    assert client.post(f"/api/jobs/{job['id']}/lqa", headers=pm, json={
        "segment_id": 999999, "category": "accuracy", "severity": "minor",
        "comment": "x",
    }).status_code == 404

    # Снятие ошибки пересчитывает оценку: остается major -> 50.0
    res = client.delete(f"/api/jobs/{job['id']}/lqa/{minor['id']}", headers=pm)
    assert res.status_code == 204
    report = client.get(f"/api/jobs/{job['id']}/lqa", headers=ann).json()
    assert report["error_count"] == 1 and report["score"] == 50.0

    # Переводчик не может снять ошибку
    assert client.delete(f"/api/jobs/{job['id']}/lqa/{major['id']}", headers=ann).status_code == 403


def test_lqa_return_notification_carries_score(client, pm, ann):
    # 1 minor на 100 слов -> score 90.0; возврат уведомляет с оценкой
    job = _submitted_job(client, pm, ann, "LQA Return", ["word " * 100])
    seg = client.get(f"/api/jobs/{job['id']}", headers=ann).json()["segments"][0]
    assert client.post(f"/api/jobs/{job['id']}/lqa", headers=pm, json={
        "segment_id": seg["id"], "category": "style", "severity": "minor",
        "comment": "Слишком разговорный регистр",
    }).status_code == 201

    res = client.post(f"/api/jobs/{job['id']}/return", headers=pm,
                      json={"comment": "Доработать стиль"})
    assert res.status_code == 200

    notes = client.get("/api/notifications", headers=ann).json()
    returned = [n for n in notes if n["type"] == "job_returned" and n["payload"]["job_id"] == job["id"]]
    assert returned, "job_returned notification missing"
    payload = returned[-1]["payload"]
    assert payload["lqa_score"] == 90.0
    assert payload["lqa_errors"] == 1


def test_lqa_score_never_negative_and_zero_words(client, pm, ann):
    # 2 критические ошибки на 100 слов: 20 баллов -> score = max(0, -100) = 0
    job = _submitted_job(client, pm, ann, "LQA Critical", ["item " * 100])
    segs = client.get(f"/api/jobs/{job['id']}", headers=ann).json()["segments"]
    for severity in ("critical", "critical"):
        assert client.post(f"/api/jobs/{job['id']}/lqa", headers=pm, json={
            "segment_id": segs[0]["id"], "category": "accuracy", "severity": severity,
            "comment": "Искажение смысла",
        }).status_code == 201
    report = client.get(f"/api/jobs/{job['id']}/lqa", headers=pm).json()
    assert report["score"] == 0.0 and not report["passed"]


# --- Комментарии к сегментам ---


def test_segment_notes_dialog(client, pm, ann, ivan):
    lines = ["Comment on this segment please.", "Second line of the text."]
    res = client.post("/api/projects", headers=pm, json={
        "name": "Notes Dialog", "source_lang": "en", "target_langs": ["ru"],
    })
    assert res.status_code == 201
    project = res.json()["id"]
    doc = io.BytesIO("\n".join(lines).encode())
    res = client.post(f"/api/projects/{project}/documents", headers=pm,
                      files={"file": ("notes.txt", doc, "text/plain")})
    assert res.status_code == 201
    job = client.get(f"/api/jobs?project_id={project}", headers=pm).json()[0]
    assert client.post(f"/api/jobs/{job['id']}/assign", headers=pm,
                       json={"assignee_id": _ann_id(client, pm)}).status_code == 200
    assert client.post(f"/api/jobs/{job['id']}/accept", headers=ann).status_code == 200
    seg = client.get(f"/api/jobs/{job['id']}", headers=ann).json()["segments"][0]

    # Чужой переводчик не участвует в обсуждении
    assert client.post(f"/api/segments/{seg['id']}/notes", headers=ivan,
                       json={"body": "Hi"}).status_code == 403
    assert client.get(f"/api/segments/{seg['id']}/notes", headers=ivan).status_code == 403

    # Переводчик спрашивает, PM отвечает — обсуждение в контексте сегмента
    res = client.post(f"/api/segments/{seg['id']}/notes", headers=ann, json={
        "body": "Здесь сохранить торговую марку без перевода?",
    })
    assert res.status_code == 201, res.text
    assert res.json()["author"]["username"] == "ann"
    assert res.json()["position"] == seg["position"]
    res = client.post(f"/api/segments/{seg['id']}/notes", headers=pm, json={
        "body": "Да, оставьте как есть.",
    })
    assert res.status_code == 201

    res = client.get(f"/api/segments/{seg['id']}/notes", headers=ann)
    assert res.status_code == 200
    notes = res.json()
    assert [n["body"] for n in notes] == [
        "Здесь сохранить торговую марку без перевода?", "Да, оставьте как есть.",
    ]
    assert [n["author"]["username"] for n in notes] == ["ann", "pm"]

    # Пустой комментарий и несуществующий сегмент
    assert client.post(f"/api/segments/{seg['id']}/notes", headers=ann,
                       json={"body": ""}).status_code == 422
    assert client.post("/api/segments/999999/notes", headers=ann,
                       json={"body": "x"}).status_code == 404


# --- TBX: экспорт/импорт терминологии ---


def _make_tb(client: TestClient, pm: dict, name: str) -> int:
    res = client.post("/api/tb/tbs", headers=pm, json={
        "name": name, "source_lang": "en", "target_lang": "ru",
    })
    assert res.status_code == 201, res.text
    return res.json()["id"]


def test_tbx_export_import_roundtrip(client, pm):
    tb = _make_tb(client, pm, "Roundtrip TB")
    entries = [
        {"term": "dashboard", "translation": "панель управления", "note": "не «приборная доска»", "status": "preferred"},
        {"term": "legacy system", "translation": "устаревшая система", "note": "", "status": "admitted"},
        {"term": "leverage", "translation": "использовать повторно", "note": "", "status": "preferred"},
        {"term": "mounting bracket", "translation": "кронштейн", "note": "", "status": "forbidden"},
    ]
    for entry in entries:
        assert client.post(f"/api/tb/tbs/{tb}/entries", headers=pm, json=entry).status_code == 201

    # Экспорт: структура TBX-Basic
    res = client.get(f"/api/tb/tbs/{tb}/export-tbx", headers=pm)
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith("application/x-tbx+xml")
    root = etree.fromstring(res.content)
    assert root.tag == "martif" and root.get("type") == "TBX"
    term_entries = root.findall(".//termEntry")
    assert len(term_entries) == 4
    langs = {ls.get("{http://www.w3.org/XML/1998/namespace}lang")
             for ls in root.findall(".//langSet")}
    assert langs == {"en", "ru"}

    # Импорт в новую TB: полный round-trip без потерь
    tb2 = _make_tb(client, pm, "Roundtrip TB 2")
    res = client.post(f"/api/tb/tbs/{tb2}/import-tbx", headers=pm,
                      files={"file": ("rt.tbx", io.BytesIO(res.content), "application/x-tbx+xml")})
    assert res.status_code == 200, res.text
    assert res.json()["added"] == 4
    imported = client.get(f"/api/tb/tbs/{tb2}/entries", headers=pm, params={"limit": 500}).json()
    assert {(e["term"], e["translation"], e["note"], e["status"]) for e in imported} == {
        (e["term"], e["translation"], e["note"], e["status"]) for e in entries
    }


def test_tbx_import_lang_prefix_and_forgiving_status(client, pm):
    tb = _make_tb(client, pm, "Prefix TB")
    tbx = f"""<?xml version="1.0" encoding="UTF-8"?>
<martif type="TBX" xml:lang="en">
  <text><body>
    <termEntry id="e1">
      <langSet xml:lang="en-US"><tig><term>cloud storage</term></tig></langSet>
      <langSet xml:lang="ru"><tig><term>облачное хранилище</term></tig></langSet>
      <descrip type="note">Только в контексте ИТ.</descrip>
    </termEntry>
    <termEntry id="e2">
      <langSet xml:lang="en"><tig><term>kill switch</term>
        <termNote type="normativeAuthorization">forbiddenTerm</termNote></tig></langSet>
      <langSet xml:lang="ru"><tig><term>аварийный выключатель</term></tig></langSet>
    </termEntry>
  </body></text>
</martif>"""
    res = client.post(f"/api/tb/tbs/{tb}/import-tbx", headers=pm,
                      files={"file": ("p.tbx", io.BytesIO(tbx.encode()), "application/x-tbx+xml")})
    assert res.status_code == 200, res.text
    assert res.json()["added"] == 2
    entries = {e["term"]: e for e in client.get(
        f"/api/tb/tbs/{tb}/entries", headers=pm).json()}
    # en-US сматчился с TB-языком en; descrip note; статус forbiddenTerm -> forbidden
    assert entries["cloud storage"]["translation"] == "облачное хранилище"
    assert entries["cloud storage"]["note"] == "Только в контексте ИТ."
    assert entries["kill switch"]["status"] == "forbidden"


def test_tbx_import_rejects_garbage(client, pm):
    tb = _make_tb(client, pm, "Bad TB")
    res = client.post(f"/api/tb/tbs/{tb}/import-tbx", headers=pm,
                      files={"file": ("bad.tbx", io.BytesIO(b"not xml at < all"), "application/x-tbx+xml")})
    assert res.status_code == 422

    # Валидный XML без termEntry с парой языков — тоже 422
    res = client.post(f"/api/tb/tbs/{tb}/import-tbx", headers=pm,
                      files={"file": ("empty.tbx", io.BytesIO(b"<martif type='TBX'/>"), "application/xml")})
    assert res.status_code == 422
