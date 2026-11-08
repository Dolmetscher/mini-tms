"""Тесты второго прохода развития: леверидж TM и доп. QA.

- пред-перевод из TM (exact / fuzzy / пропагация повторов, права, статусы);
- анализ объема (банды совпадений, повторы, взвешенные слова);
- автопропагация при подтверждении сегмента;
- QA: плейсхолдеры (%s, {0}) и аномальное соотношение длины;
- двуязычная выгрузка DOCX для ревью.

Используется та же тестовая БД, что и в test_cat.py (модули импортируются
в одном процессе); сценарии изолированы собственными проектами/TM.
"""
import io
import os
import tempfile

_TEST_DIR = tempfile.mkdtemp(prefix="mini-tms-lev-")
os.environ.setdefault("TMS_DATABASE_URL", f"sqlite:///{_TEST_DIR}/test.db".replace("\\", "/"))
os.environ.setdefault("TMS_DATA_DIR", os.path.join(_TEST_DIR, "data").replace("\\", "/"))
os.environ.setdefault("TMS_MT_PROVIDER", "mock")

import pytest  # noqa: E402
from docx import Document as parse_docx  # noqa: E402
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


@pytest.fixture(scope="module")
def ivan(client):
    return login(client, "ivan", "ivan123")


def _ann_id(client: TestClient, pm: dict) -> int:
    members = client.get(f"/api/orgs/{ORG}/members", headers=pm).json()
    return next(m["user"]["id"] for m in members if m["user"]["username"] == "ann")


def _setup_job(client: TestClient, pm: dict, ann: dict, name: str, tm_entries, lines, assign=True) -> dict:
    """Проект + TM (use_for_search) + документ + задание (назначено ann)."""
    res = client.post("/api/projects", headers=pm, json={
        "name": name, "source_lang": "en", "target_langs": ["ru"],
    })
    assert res.status_code == 201, res.text
    project = res.json()["id"]
    res = client.post("/api/tm/tms", headers=pm, json={
        "name": f"{name} TM", "source_lang": "en", "target_lang": "ru",
    })
    tm = res.json()["id"]
    for source, target in tm_entries:
        assert client.post(f"/api/tm/tms/{tm}/entries", headers=pm,
                           json={"source_text": source, "target_text": target}).status_code == 201
    assert client.post(f"/api/projects/{project}/resources", headers=pm,
                       json={"tm_id": tm, "use_for_search": True}).status_code == 201
    doc = io.BytesIO("\n".join(lines).encode())
    res = client.post(f"/api/projects/{project}/documents", headers=pm,
                      files={"file": (f"{name}.txt", doc, "text/plain")})
    assert res.status_code == 201, res.text
    document_id = res.json()["id"]
    assert client.get(f"/api/documents/{document_id}", headers=pm).json()["processing_status"] == "ready"
    job = client.get(f"/api/jobs?project_id={project}", headers=pm).json()[0]
    if assign:
        assert client.post(f"/api/jobs/{job['id']}/assign", headers=pm,
                           json={"assignee_id": _ann_id(client, pm)}).status_code == 200
        assert client.post(f"/api/jobs/{job['id']}/accept", headers=ann).status_code == 200
    return job


# --- пред-перевод ---


def test_pretranslate_exact_repetition_fuzzy(client, pm, ann, ivan):
    job = _setup_job(
        client, pm, ann, "Lev Pretranslate",
        tm_entries=[
            ("Open the settings window.", "Откройте окно настроек."),
            ("Save the document before closing.", "Сохраните документ перед закрытием."),
        ],
        lines=[
            "Open the settings window.",       # exact из TM (оба вхождения)
            "Unique line number one.",          # без совпадений
            "Open the settings window.",       # exact из TM
            "Save the file before closing.",   # fuzzy (~76%)
            "Manual translated line.",         # без TM: переводим вручную
            "Manual translated line.",         # повтор предыдущего — пропагация
        ],
    )

    # Чужой переводчик не может пред-переводить чужое задание
    assert client.post(f"/api/jobs/{job['id']}/pre-translate", headers=ivan,
                       json={"min_score": 100}).status_code == 403

    # Переводчик вручную перевел сегмент 4 — его повтор заполнится пропагацией
    detail = client.get(f"/api/jobs/{job['id']}", headers=ann).json()
    seg = {s["position"]: s for s in detail["segments"]}
    assert client.patch(f"/api/segments/{seg[4]['id']}", headers=ann,
                        json={"target_text": "Строка, переведенная вручную."}).status_code == 200

    # 1) Консервативный режим: только 100% + повторы уже переведенного
    res = client.post(f"/api/jobs/{job['id']}/pre-translate", headers=ann, json={})
    assert res.status_code == 200, res.text
    assert res.json() == {"filled_exact": 2, "filled_fuzzy": 0, "propagated": 1, "skipped": 2}

    detail = client.get(f"/api/jobs/{job['id']}", headers=ann).json()
    segments = {s["position"]: s for s in detail["segments"]}
    assert segments[0]["target_text"] == "Откройте окно настроек."
    assert segments[2]["target_text"] == "Откройте окно настроек."  # тоже exact из TM
    assert segments[5]["target_text"] == "Строка, переведенная вручную."  # пропагация повтора
    assert segments[1]["target_text"] == "" and segments[3]["target_text"] == ""
    # черновики: не подтверждены (переводчик обязан вычитать), статус translated
    assert segments[0]["confirmed"] is False and segments[0]["translation_status"] == "translated"
    assert segments[5]["confirmed"] is False

    # 2) Fuzzy-режим: добирает 76% совпадение
    res = client.post(f"/api/jobs/{job['id']}/pre-translate", headers=ann, json={"min_score": 60})
    assert res.status_code == 200, res.text
    assert res.json() == {"filled_exact": 0, "filled_fuzzy": 1, "propagated": 0, "skipped": 1}
    detail = client.get(f"/api/jobs/{job['id']}", headers=ann).json()
    segments = {s["position"]: s for s in detail["segments"]}
    assert segments[3]["target_text"] == "Сохраните документ перед закрытием."

    # Повторный запуск ничего не меняет (заполненные сегменты не трогаем)
    res = client.post(f"/api/jobs/{job['id']}/pre-translate", headers=ann, json={})
    assert res.json() == {"filled_exact": 0, "filled_fuzzy": 0, "propagated": 0, "skipped": 1}


def test_pretranslate_requires_editable_status(client, pm):
    # Назначение/принятие не выполнялись: статус unassigned -> 409
    job = _setup_job(client, pm, pm, "Lev Unassigned", [], ["Only one line."], assign=False)
    res = client.post(f"/api/jobs/{job['id']}/pre-translate", headers=pm, json={})
    assert res.status_code == 409


# --- анализ объема ---


def test_analysis_buckets_and_weights(client, pm, ann, ivan):
    job = _setup_job(
        client, pm, ann, "Lev Analysis",
        tm_entries=[
            ("Open the settings window.", "Откройте окно настроек."),
            ("Save the document before closing.", "Сохраните документ перед закрытием."),
        ],
        lines=[
            "Open the settings window.",       # exact (оба вхождения: TM приоритетнее повтора)
            "Unique line number one.",          # no match
            "Open the settings window.",       # exact
            "Save the file before closing.",   # 76% -> банд 75–84
            "Internal repeated phrase here.",   # no match (первое вхождение)
            "Internal repeated phrase here.",   # повтор без TM -> repetition
        ],
    )
    res = client.get(f"/api/jobs/{job['id']}/analysis", headers=ann)
    assert res.status_code == 200, res.text
    report = res.json()
    cats = {c["code"]: c for c in report["categories"]}
    assert report["segments"] == 6 and report["words"] == 25
    assert cats["exact"]["segments"] == 2 and cats["exact"]["words"] == 8
    assert cats["exact"]["weighted"] == 2  # round(8 * 0.30)
    assert cats["fuzzy_75_84"]["segments"] == 1 and cats["fuzzy_75_84"]["words"] == 5
    assert cats["fuzzy_75_84"]["weighted"] == 4  # round(5 * 0.85)
    assert cats["repetition"]["segments"] == 1 and cats["repetition"]["weighted"] == 1
    assert cats["no_match"]["segments"] == 2 and cats["no_match"]["words"] == 8
    assert report["weighted_words"] == 2 + 4 + 1 + 8
    assert report["tm_names"] == ["Lev Analysis TM"]

    # Посторонний переводчик (нет заданий в проекте) отчет не видит
    assert client.get(f"/api/jobs/{job['id']}/analysis", headers=ivan).status_code == 403


# --- автопропагация при подтверждении ---


def test_autopropagation_on_confirm(client, pm, ann):
    job = _setup_job(
        client, pm, ann, "Lev Propagate", [],
        lines=["Repeat me please.", "Other sentence.", "Repeat me please."],
    )
    detail = client.get(f"/api/jobs/{job['id']}", headers=ann).json()
    segments = {s["position"]: s for s in detail["segments"]}
    res = client.patch(f"/api/segments/{segments[0]['id']}", headers=ann, json={
        "target_text": "Повтори меня, пожалуйста.", "confirmed": True,
    })
    assert res.status_code == 200, res.text

    detail = client.get(f"/api/jobs/{job['id']}", headers=ann).json()
    segments = {s["position"]: s for s in detail["segments"]}
    assert segments[0]["confirmed"] is True
    # Идентичный пустой сегмент получил тот же перевод — черновиком
    assert segments[2]["target_text"] == "Повтори меня, пожалуйста."
    assert segments[2]["confirmed"] is False and segments[2]["translation_status"] == "translated"
    # Остальной текст не затронут
    assert segments[1]["target_text"] == ""


# --- QA: плейсхолдеры и длина ---


def test_qa_placeholders_and_length(client, pm, ann):
    long_source = "This is a fairly long source sentence used to verify the length ratio check."
    job = _setup_job(
        client, pm, ann, "Lev QA", [],
        lines=["Error %s in file {0}.", long_source, "Ready: %s"],
    )
    detail = client.get(f"/api/jobs/{job['id']}", headers=ann).json()
    seg = {s["position"]: s for s in detail["segments"]}

    # Плейсхолдеры искажены: %s -> %d, {0} -> {1}
    assert client.patch(f"/api/segments/{seg[0]['id']}", headers=ann,
                        json={"target_text": "Ошибка %d в файле {1}."}).status_code == 200
    # Аномально короткий перевод длинного исходника
    assert client.patch(f"/api/segments/{seg[1]['id']}", headers=ann,
                        json={"target_text": "Ошибка."}).status_code == 200
    # Контроль: плейсхолдеры сохранены
    assert client.patch(f"/api/segments/{seg[2]['id']}", headers=ann,
                        json={"target_text": "Готово: %s"}).status_code == 200

    report = client.get(f"/api/jobs/{job['id']}/qa", headers=ann).json()
    items = {(i["segment_id"], i["code"]) for i in report["items"]}
    assert (seg[0]["id"], "placeholder") in items
    assert (seg[1]["id"], "length_ratio") in items
    assert (seg[2]["id"], "placeholder") not in items
    # Обе проверки — предупреждения, не блокирующие
    codes = {i["code"]: i["severity"] for i in report["items"]}
    assert codes.get("placeholder") == "warning"
    assert codes.get("length_ratio") == "warning"


# --- двуязычная выгрузка ---


def test_bilingual_export(client, pm, ann, ivan):
    job = _setup_job(
        client, pm, ann, "Lev Bilingual", [],
        lines=["First segment.", "Second segment."],
    )
    detail = client.get(f"/api/jobs/{job['id']}", headers=ann).json()
    seg = {s["position"]: s for s in detail["segments"]}
    client.patch(f"/api/segments/{seg[0]['id']}", headers=ann,
                 json={"target_text": "Первый сегмент."})

    res = client.get(f"/api/jobs/{job['id']}/export-bilingual", headers=ann)
    assert res.status_code == 200, res.text
    assert "bilingual" in res.headers["Content-Disposition"]
    doc = parse_docx(io.BytesIO(res.content))
    table = doc.tables[0]
    assert len(table.rows) == 3  # заголовок + 2 сегмента
    assert [c.text for c in table.rows[0].cells] == ["#", "Исходник (en)", "Перевод (ru)"]
    assert table.rows[1].cells[1].text == "First segment."
    assert table.rows[1].cells[2].text == "Первый сегмент."
    assert table.rows[2].cells[2].text == "—"  # пустой перевод виден ревьюеру

    # Менеджер тоже может скачать
    assert client.get(f"/api/jobs/{job['id']}/export-bilingual", headers=pm).status_code == 200
    # Посторонний переводчик — нет
    assert client.get(f"/api/jobs/{job['id']}/export-bilingual", headers=ivan).status_code == 403
