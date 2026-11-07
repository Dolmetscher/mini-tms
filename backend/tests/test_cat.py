"""Приемочные и модульные тесты CAT-системы (ТЗ §12).

До импорта app: временная БД, временный каталог данных, MT-провайдер mock.
"""
import io
import os
import tempfile

_TEST_DIR = tempfile.mkdtemp(prefix="mini-tms-cat-")
os.environ["TMS_DATABASE_URL"] = f"sqlite:///{_TEST_DIR}/test.db".replace("\\", "/")
os.environ["TMS_DATA_DIR"] = os.path.join(_TEST_DIR, "data").replace("\\", "/")
os.environ["TMS_MT_PROVIDER"] = "mock"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from lxml import etree  # noqa: E402

from app.main import app  # noqa: E402

ORG = 1


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def login(client: TestClient, username: str, password: str, org: int = ORG) -> dict:
    res = client.post("/api/login", json={"username": username, "password": password})
    assert res.status_code == 200, res.text
    token = res.json()["access_token"]
    return {"Authorization": f"Bearer {token}", "X-Org-Id": str(org)}


@pytest.fixture(scope="module")
def pm(client):
    return login(client, "pm", "pm123")


@pytest.fixture(scope="module")
def ann(client):
    return login(client, "ann", "ann123")


@pytest.fixture(scope="module")
def ivan(client):
    return login(client, "ivan", "ivan123")


# --- FR-01: вход со списком организаций/ролей ---


def test_login_returns_memberships(client):
    res = client.post("/api/login", json={"username": "pm", "password": "pm123"})
    body = res.json()
    assert res.status_code == 200
    assert body["memberships"][0]["org_name"] == "Demo Org"
    assert body["memberships"][0]["role"] == "pm"


def test_me_requires_token(client):
    assert client.get("/api/me").status_code == 401


# --- §12-11: изоляция организаций ---


def test_org_isolation(client, ann):
    # ann не состоит в организации 2 (ее создаст admin): запрос с чужим X-Org-Id отвергается
    admin = login(client, "admin", "admin123")
    res = client.post("/api/orgs", json={"name": "Other Corp"}, headers=admin)
    assert res.status_code == 201
    other_org = res.json()["id"]

    ann_other = {k: v for k, v in ann.items() if k != "X-Org-Id"}

    # не член организации -> 403
    res = client.get("/api/jobs", headers={**ann_other, "X-Org-Id": str(other_org)})
    assert res.status_code == 403

    # admin (создатель org2) добавляет ann в org2; теперь прямой запрос к заданию
    # организации 1 из контекста организации 2 -> 404, данные изолированы
    admin_org2 = {**admin, "X-Org-Id": str(other_org)}
    res = client.post(f"/api/orgs/{other_org}/members", headers=admin_org2,
                      json={"username": "ann", "password": "ignored-existing", "role": "translator"})
    assert res.status_code == 201, res.text
    res = client.get("/api/jobs/1", headers={**ann_other, "X-Org-Id": str(other_org)})
    assert res.status_code == 404
    res = client.get("/api/jobs", headers={**ann_other, "X-Org-Id": str(other_org)})
    assert res.json() == []

    # без заголовка организации -> 400
    res = client.get("/api/jobs", headers={"Authorization": ann["Authorization"]})
    assert res.status_code == 400


# --- §12-1..9,13: сквозной сценарий ---


def test_acceptance_scenario(client, pm, ann):
    # 1. Проект с двумя целевыми языками
    res = client.post("/api/projects", headers=pm, json={
        "name": "Scenario", "source_lang": "en", "target_langs": ["ru", "de"],
        "instructions": "Keep it short.", "mt_enabled": True,
        "deadline": "2026-12-31T12:00:00",
    })
    assert res.status_code == 201, res.text
    project = res.json()["id"]

    # TM + TB и подключение к проекту
    res = client.post("/api/tm/tms", headers=pm, json={"name": "Scen TM", "source_lang": "en", "target_lang": "ru"})
    tm = res.json()["id"]
    client.post(f"/api/tm/tms/{tm}/entries", headers=pm,
                json={"source_text": "Hello world.", "target_text": "Привет, мир."})
    res = client.post("/api/tb/tbs", headers=pm, json={"name": "Scen TB", "source_lang": "en", "target_lang": "ru"})
    tb = res.json()["id"]
    client.post(f"/api/tb/tbs/{tb}/entries", headers=pm,
                json={"term": "world", "translation": "мир", "note": "", "status": "preferred"})

    assert client.post(f"/api/projects/{project}/resources", headers=pm,
                       json={"tm_id": tm, "use_for_search": True, "use_for_write": True}).status_code == 201
    assert client.post(f"/api/projects/{project}/resources", headers=pm,
                       json={"tb_id": tb, "use_for_search": True}).status_code == 201
    # Несовместимая языковая пара отвергается (FR-12)
    res = client.post("/api/tm/tms", headers=pm, json={"name": "FR TM", "source_lang": "en", "target_lang": "fr"})
    fr_tm = res.json()["id"]
    res = client.post(f"/api/projects/{project}/resources", headers=pm, json={"tm_id": fr_tm})
    assert res.status_code == 422

    # Загрузка документа: на каждый целевой язык — задание
    doc = io.BytesIO("Hello world.\nSecond segment.".encode())
    res = client.post(f"/api/projects/{project}/documents", headers=pm,
                      files={"file": ("scenario.txt", doc, "text/plain")})
    assert res.status_code == 201
    document_id = res.json()["id"]
    res = client.get(f"/api/documents/{document_id}", headers=pm)
    assert res.json()["processing_status"] == "ready"

    res = client.get(f"/api/jobs?project_id={project}", headers=pm)
    jobs = res.json()
    assert len(jobs) == 2
    ru_job = next(j for j in jobs if j["target_lang"] == "ru")
    assert ru_job["total_segments"] == 2 and ru_job["total_words"] == 4

    # Назначение (FR-11): только переводчик организации
    members = client.get(f"/api/orgs/{ORG}/members", headers=pm).json()
    ann_id = next(m["user"]["id"] for m in members if m["user"]["username"] == "ann")
    res = client.post(f"/api/jobs/{ru_job['id']}/assign", headers=pm, json={"assignee_id": ann_id})
    assert res.status_code == 200
    # pm не переводчик -> назначить нельзя
    pm_id = next(m["user"]["id"] for m in members if m["user"]["username"] == "pm")
    assert client.post(f"/api/jobs/{ru_job['id']}/assign", headers=pm, json={"assignee_id": pm_id}).status_code == 422

    # 2. Переводчик получил уведомление и принимает задание
    res = client.get("/api/notifications", headers=ann)
    assert any(n["type"] == "job_assigned" and n["payload"]["job_id"] == ru_job["id"] for n in res.json())
    assert client.post(f"/api/jobs/{ru_job['id']}/accept", headers=ann).status_code == 200

    # 3. Редактор: ввод, подтверждение
    detail = client.get(f"/api/jobs/{ru_job['id']}", headers=ann).json()
    seg1, seg2 = detail["segments"]
    res = client.patch(f"/api/segments/{seg1['id']}", headers=ann, json={"target_text": "Привет, мир."})
    assert res.status_code == 200
    res = client.patch(f"/api/segments/{seg1['id']}", headers=ann, json={"confirmed": True})
    assert res.json()["confirmed"] is True
    # правка подтвержденного снимает подтверждение (FR-03)
    res = client.patch(f"/api/segments/{seg1['id']}", headers=ann, json={"target_text": "Привет, мир!"})
    assert res.json()["confirmed"] is False
    client.patch(f"/api/segments/{seg1['id']}", headers=ann, json={"confirmed": True})

    # accepted -> in_progress при первой правке
    assert client.get(f"/api/jobs/{ru_job['id']}", headers=ann).json()["status"] == "in_progress"

    # 4. TM: точное совпадение из подключенной TM с именем базы (конкорданс проекта)
    res = client.get(f"/api/projects/{project}/tm/search", headers=ann,
                     params={"text": "Hello world.", "target_lang": "ru"})
    matches = res.json()
    assert matches and matches[0]["match_percentage"] == 100 and matches[0]["tm_name"] == "Scen TM"
    # нечеткое
    res = client.get(f"/api/projects/{project}/tm/search", headers=ann,
                     params={"text": "Hello big world.", "target_lang": "ru"})
    assert res.json() and 60 <= res.json()[0]["match_percentage"] < 100

    # подтвержденный сегмент записан в TM с use_for_write
    res = client.get(f"/api/projects/{project}/tm/search", headers=ann,
                     params={"text": "Hello world.", "target_lang": "ru"})
    assert any(m["target_suggestion"] == "Привет, мир!" for m in res.json())

    # 5. TB: термин из исходника определяется (FR-07)
    res = client.get(f"/api/projects/{project}/tb/lookup", headers=ann,
                     params={"text": "Hello world.", "target_lang": "ru"})
    hits = res.json()
    assert any(h["term"] == "world" and h["translation"] == "мир" for h in hits)

    # 6. MT по действию пользователя; маркировка провайдером (mock)
    res = client.get(f"/api/jobs/{ru_job['id']}/segments/{seg2['id']}/mt", headers=ann)
    assert res.status_code == 200
    assert res.json()["provider"] == "mock"

    # 8. QA: неподтвержденный сегмент блокирует сдачу
    res = client.post(f"/api/jobs/{ru_job['id']}/submit", headers=ann)
    assert res.status_code == 422
    assert res.json()["blocking_count"] >= 1

    client.patch(f"/api/segments/{seg2['id']}", headers=ann, json={"target_text": "Второй сегмент."})
    client.patch(f"/api/segments/{seg2['id']}", headers=ann, json={"confirmed": True})

    # 9. Сдача; менеджер видит статус и уведомление
    assert client.post(f"/api/jobs/{ru_job['id']}/submit", headers=ann).status_code == 200
    assert client.get(f"/api/jobs/{ru_job['id']}", headers=pm).json()["status"] == "submitted"
    res = client.get("/api/notifications", headers=pm)
    assert any(n["type"] == "job_submitted" and n["payload"]["job_id"] == ru_job["id"] for n in res.json())

    # 10. Экспорт (FR-14)
    res = client.get(f"/api/jobs/{ru_job['id']}/export", headers=pm)
    assert res.status_code == 200
    exported_text = res.content.decode("utf-8")
    assert "Привет, мир!" in exported_text and "Второй сегмент." in exported_text

    # 13. Возврат на доработку с комментарием; исправление и повторная сдача
    res = client.post(f"/api/jobs/{ru_job['id']}/return", headers=pm, json={"comment": "Fix greeting"})
    assert res.status_code == 200
    res = client.get("/api/notifications", headers=ann)
    assert any(n["type"] == "job_returned" and n["payload"]["job_id"] == ru_job["id"] for n in res.json())
    detail = client.get(f"/api/jobs/{ru_job['id']}", headers=ann).json()
    assert detail["status"] == "rework" and detail["rework_comment"] == "Fix greeting"

    seg1 = detail["segments"][0]
    client.patch(f"/api/segments/{seg1['id']}", headers=ann,
                 json={"target_text": "Привет, мир!!", "confirmed": True})
    assert client.post(f"/api/jobs/{ru_job['id']}/submit", headers=ann).status_code == 200
    assert client.post(f"/api/jobs/{ru_job['id']}/complete", headers=pm).status_code == 200

    # завершено: редактирование запрещено
    seg1 = client.get(f"/api/jobs/{ru_job['id']}", headers=ann).json()["segments"][0]
    assert client.patch(f"/api/segments/{seg1['id']}", headers=ann,
                        json={"target_text": "x"}).status_code == 409


# --- FR-02: отклонение, переназначение, отмена ---


def test_decline_reassign_cancel(client, pm, ivan):
    res = client.post("/api/projects", headers=pm, json={
        "name": "DnR", "source_lang": "en", "target_langs": ["ru"],
    })
    project = res.json()["id"]
    res = client.post(f"/api/projects/{project}/documents", headers=pm,
                      files={"file": ("dnr.txt", io.BytesIO(b"One line."), "text/plain")})
    job = next(j for j in client.get(f"/api/jobs?project_id={project}", headers=pm).json())
    members = client.get(f"/api/orgs/{ORG}/members", headers=pm).json()
    ivan_id = next(m["user"]["id"] for m in members if m["user"]["username"] == "ivan")
    ann_id = next(m["user"]["id"] for m in members if m["user"]["username"] == "ann")

    client.post(f"/api/jobs/{job['id']}/assign", headers=pm, json={"assignee_id": ivan_id})
    # отклонение с причиной
    res = client.post(f"/api/jobs/{job['id']}/decline", headers=ivan, json={"reason": "No capacity"})
    assert res.status_code == 200
    res = client.get("/api/notifications", headers=pm)
    assert any(n["type"] == "job_declined" for n in res.json())
    # повторное назначение другому исполнителю
    res = client.post(f"/api/jobs/{job['id']}/assign", headers=pm, json={"assignee_id": ann_id})
    assert res.status_code == 200
    # отмена менеджером
    res = client.post(f"/api/jobs/{job['id']}/cancel", headers=pm)
    assert res.status_code == 200 and res.json()["status"] == "canceled"


# --- FR-04: конфликт версий ---


def test_optimistic_lock_conflict(client, pm, ann):
    res = client.post("/api/projects", headers=pm, json={
        "name": "Conflict", "source_lang": "en", "target_langs": ["ru"],
    })
    project = res.json()["id"]
    client.post(f"/api/projects/{project}/documents", headers=pm,
                files={"file": ("conflict.txt", io.BytesIO(b"Line A."), "text/plain")})
    job = next(j for j in client.get(f"/api/jobs?project_id={project}", headers=pm).json())
    members = client.get(f"/api/orgs/{ORG}/members", headers=pm).json()
    ann_id = next(m["user"]["id"] for m in members if m["user"]["username"] == "ann")
    client.post(f"/api/jobs/{job['id']}/assign", headers=pm, json={"assignee_id": ann_id})
    client.post(f"/api/jobs/{job['id']}/accept", headers=ann)
    seg = client.get(f"/api/jobs/{job['id']}", headers=ann).json()["segments"][0]

    client.patch(f"/api/segments/{seg['id']}", headers=ann, json={"target_text": "first edit"})
    # устаревшая база -> 409 с серверной версией в теле
    res = client.patch(f"/api/segments/{seg['id']}", headers=ann, json={
        "target_text": "second edit", "base_updated_at": seg["updated_at"],
    })
    assert res.status_code == 409
    assert res.json()["target_text"] == "first edit"


# --- FR-09: QA (теги, числа, запрещенные термины) ---


def test_qa_tags_numbers_terms(client, pm, ann):
    res = client.post("/api/projects", headers=pm, json={
        "name": "QA", "source_lang": "en", "target_langs": ["ru"],
    })
    project = res.json()["id"]
    content = 'Click <x id="1"/> now.\nVersion 5 is out.\nWork fast today.'
    client.post(f"/api/projects/{project}/documents", headers=pm,
                files={"file": ("qa.txt", io.BytesIO(content.encode()), "text/plain")})
    job = next(j for j in client.get(f"/api/jobs?project_id={project}", headers=pm).json())

    # TB с запрещенным вариантом
    res = client.post("/api/tb/tbs", headers=pm, json={"name": "QA TB", "source_lang": "en", "target_lang": "ru"})
    tb = res.json()["id"]
    client.post(f"/api/tb/tbs/{tb}/entries", headers=pm,
                json={"term": "fast", "translation": "быстренько", "status": "forbidden"})
    client.post(f"/api/projects/{project}/resources", headers=pm, json={"tb_id": tb})

    members = client.get(f"/api/orgs/{ORG}/members", headers=pm).json()
    ann_id = next(m["user"]["id"] for m in members if m["user"]["username"] == "ann")
    client.post(f"/api/jobs/{job['id']}/assign", headers=pm, json={"assignee_id": ann_id})
    client.post(f"/api/jobs/{job['id']}/accept", headers=ann)
    detail = client.get(f"/api/jobs/{job['id']}", headers=ann).json()
    s_tag, s_num, s_term = detail["segments"]

    # сегмент без тега, подтвержден -> блокирующая ошибка тега (§12-8)
    client.patch(f"/api/segments/{s_tag['id']}", headers=ann, json={"target_text": "Нажмите сейчас."})
    client.patch(f"/api/segments/{s_tag['id']}", headers=ann, json={"confirmed": True})
    # расхождение чисел и запрещенный термин — предупреждения
    client.patch(f"/api/segments/{s_num['id']}", headers=ann, json={"target_text": "Версия 6 вышла."})
    client.patch(f"/api/segments/{s_num['id']}", headers=ann, json={"confirmed": True})
    client.patch(f"/api/segments/{s_term['id']}", headers=ann, json={"target_text": "Работаем быстренько сегодня."})
    client.patch(f"/api/segments/{s_term['id']}", headers=ann, json={"confirmed": True})

    report = client.get(f"/api/jobs/{job['id']}/qa", headers=ann).json()
    codes = [i["code"] for i in report["items"]]
    assert "tag_error" in codes
    assert "number_mismatch" in codes
    assert "terminology" in codes
    assert report["can_submit"] is False
    assert client.post(f"/api/jobs/{job['id']}/submit", headers=ann).status_code == 422

    # исправление тега снимает блокировку
    client.patch(f"/api/segments/{s_tag['id']}", headers=ann, json={"target_text": 'Нажмите <x id="1"/> сейчас.'})
    client.patch(f"/api/segments/{s_tag['id']}", headers=ann, json={"confirmed": True})
    report = client.get(f"/api/jobs/{job['id']}/qa", headers=ann).json()
    assert report["blocking_count"] == 0
    assert client.post(f"/api/jobs/{job['id']}/submit", headers=ann).status_code == 200


# --- §9: XLIFF с inline-тегами, round-trip ---


XLIFF_DOC = b"""<?xml version="1.0" encoding="UTF-8"?>
<xliff version="1.2">
 <file source-language="en" target-language="ru" datatype="plaintext">
  <body>
   <trans-unit id="1"><source>Hello <x id="1"/> world</source></trans-unit>
   <trans-unit id="2"><source><bpt id="b">[b]</bpt>Bold<ept id="b">[/b]</ept> text</source></trans-unit>
  </body>
 </file>
</xliff>"""


def test_xliff_tags_roundtrip(client, pm, ann):
    res = client.post("/api/projects", headers=pm, json={
        "name": "Xliff", "source_lang": "en", "target_langs": ["ru"],
    })
    project = res.json()["id"]
    res = client.post(f"/api/projects/{project}/documents", headers=pm,
                      files={"file": ("tags.xliff", io.BytesIO(XLIFF_DOC), "application/x-xliff+xml")})
    assert res.status_code == 201
    document_id = res.json()["id"]
    assert client.get(f"/api/documents/{document_id}", headers=pm).json()["processing_status"] == "ready"

    job = next(j for j in client.get(f"/api/jobs?project_id={project}", headers=pm).json())
    members = client.get(f"/api/orgs/{ORG}/members", headers=pm).json()
    ann_id = next(m["user"]["id"] for m in members if m["user"]["username"] == "ann")
    client.post(f"/api/jobs/{job['id']}/assign", headers=pm, json={"assignee_id": ann_id})
    client.post(f"/api/jobs/{job['id']}/accept", headers=ann)
    detail = client.get(f"/api/jobs/{job['id']}", headers=ann).json()
    seg1, seg2 = detail["segments"]
    assert '<x id="1"/>' in seg1["source_text"]
    assert '<g id="b">' in seg2["source_text"] and "</g>" in seg2["source_text"]

    client.patch(f"/api/segments/{seg1['id']}", headers=ann,
                 json={"target_text": 'Привет <x id="1"/> мир', "confirmed": True})
    client.patch(f"/api/segments/{seg2['id']}", headers=ann,
                 json={"target_text": '<g id="b">Жирный</g> текст', "confirmed": True})
    assert client.post(f"/api/jobs/{job['id']}/submit", headers=ann).status_code == 200

    res = client.get(f"/api/jobs/{job['id']}/export", headers=pm)
    assert res.status_code == 200
    tree = etree.fromstring(res.content)

    def local(el):
        return etree.QName(el).localname if isinstance(el.tag, str) else ""

    units = [u for u in tree.iter() if local(u) == "trans-unit"]
    assert len(units) == 2
    # Видимый текст перевода: target.text + хвосты inline-элементов.
    # Внутренний текст bpt/ept — payload форматирования из исходника, он не считается.
    texts = []
    for unit in units:
        target = next(c for c in unit if local(c) == "target")
        visible = (target.text or "") + "".join((c.tail or "") for c in target)
        texts.append(visible)
    assert texts == ["Привет  мир", "Жирный текст"]
    target1 = next(c for c in units[0] if local(c) == "target")
    assert any(local(c) == "x" for c in target1)
    target2 = next(c for c in units[1] if local(c) == "target")
    inner = [local(c) for c in target2]
    assert "bpt" in inner and "ept" in inner
    # payload форматирования сохранен из исходника
    assert any((c.text or "") == "[b]" for c in target2 if local(c) == "bpt")


def test_docx_roundtrip(client, pm, ann):
    from docx import Document as make_docx

    buffer = io.BytesIO()
    doc = make_docx()
    doc.add_paragraph("First paragraph.")
    doc.add_paragraph("")
    doc.add_paragraph("Second paragraph.")
    doc.save(buffer)

    res = client.post("/api/projects", headers=pm, json={
        "name": "Docx", "source_lang": "en", "target_langs": ["ru"],
    })
    project = res.json()["id"]
    res = client.post(f"/api/projects/{project}/documents", headers=pm,
                      files={"file": ("round.docx", io.BytesIO(buffer.getvalue()),
                                      "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})
    assert res.status_code == 201
    document_id = res.json()["id"]
    status_body = client.get(f"/api/documents/{document_id}", headers=pm).json()
    assert status_body["processing_status"] == "ready"

    job = next(j for j in client.get(f"/api/jobs?project_id={project}", headers=pm).json())
    members = client.get(f"/api/orgs/{ORG}/members", headers=pm).json()
    ann_id = next(m["user"]["id"] for m in members if m["user"]["username"] == "ann")
    client.post(f"/api/jobs/{job['id']}/assign", headers=pm, json={"assignee_id": ann_id})
    client.post(f"/api/jobs/{job['id']}/accept", headers=ann)
    detail = client.get(f"/api/jobs/{job['id']}", headers=ann).json()
    assert [s["source_text"] for s in detail["segments"]] == ["First paragraph.", "Second paragraph."]
    for seg in detail["segments"]:
        client.patch(f"/api/segments/{seg['id']}", headers=ann,
                     json={"target_text": f"Перевод {seg['position'] + 1}.", "confirmed": True})
    client.post(f"/api/jobs/{job['id']}/submit", headers=ann)

    res = client.get(f"/api/jobs/{job['id']}/export", headers=pm)
    exported = make_docx(io.BytesIO(res.content))
    assert [p.text for p in exported.paragraphs if p.text.strip()] == ["Перевод 1.", "Перевод 2."]


# --- FR-12: импорты TMX и CSV ---


def test_tmx_and_csv_import(client, pm):
    res = client.post("/api/tm/tms", headers=pm, json={"name": "Tmx TM", "source_lang": "en", "target_lang": "ru"})
    tm = res.json()["id"]
    tmx = """<?xml version="1.0"?>
<tmx version="1.4"><body>
 <tu><tuv xml:lang="en"><seg>Cat</seg></tuv><tuv xml:lang="ru"><seg>Кот</seg></tuv></tu>
 <tu><tuv xml:lang="en"><seg>Dog</seg></tuv><tuv xml:lang="ru"><seg>Пес</seg></tuv></tu>
 <tu><tuv xml:lang="fr"><seg>Chat</seg></tuv></tu>
</body></tmx>""".encode("utf-8")
    res = client.post(f"/api/tm/tms/{tm}/import-tmx", headers=pm,
                      files={"file": ("tm.tmx", io.BytesIO(tmx), "application/xml")})
    assert res.json() == {"added": 2, "updated": 0, "skipped": 1}
    entries = client.get(f"/api/tm/tms/{tm}/entries", headers=pm).json()
    assert {e["source_text"] for e in entries} == {"Cat", "Dog"}

    # CSV с неверным заголовком -> 422
    res = client.post("/api/tb/tbs", headers=pm, json={"name": "Csv TB", "source_lang": "en", "target_lang": "ru"})
    tb = res.json()["id"]
    bad = b"foo,bar\ncat,kot"
    res = client.post(f"/api/tb/tbs/{tb}/import-csv", headers=pm,
                      files={"file": ("tb.csv", io.BytesIO(bad), "text/csv")})
    assert res.status_code == 422
    good = "term,translation,note,status\nplatform,платформа,,preferred\nfast,быстренько,,forbidden\n".encode()
    res = client.post(f"/api/tb/tbs/{tb}/import-csv", headers=pm,
                      files={"file": ("tb.csv", io.BytesIO(good), "text/csv")})
    assert res.json() == {"added": 2, "skipped": 0}
    entries = client.get(f"/api/tb/tbs/{tb}/entries", headers=pm).json()
    assert any(e["status"] == "forbidden" for e in entries)


# --- FR-08: MT-политики ---


def test_mt_policies(client, pm, ann):
    res = client.post("/api/projects", headers=pm, json={
        "name": "MtOn", "source_lang": "en", "target_langs": ["ru"], "mt_enabled": True,
    })
    project_on = res.json()["id"]
    res = client.post("/api/projects", headers=pm, json={
        "name": "MtOff", "source_lang": "en", "target_langs": ["ru"], "mt_enabled": False,
    })
    project_off = res.json()["id"]

    def make_job(project_id: int) -> dict:
        client.post(f"/api/projects/{project_id}/documents", headers=pm,
                    files={"file": ("mt.txt", io.BytesIO(b"Text."), "text/plain")})
        return next(j for j in client.get(f"/api/jobs?project_id={project_id}", headers=pm).json())

    job_on, job_off = make_job(project_on), make_job(project_off)
    members = client.get(f"/api/orgs/{ORG}/members", headers=pm).json()
    ann_id = next(m["user"]["id"] for m in members if m["user"]["username"] == "ann")
    for job in (job_on, job_off):
        client.post(f"/api/jobs/{job['id']}/assign", headers=pm, json={"assignee_id": ann_id})
        client.post(f"/api/jobs/{job['id']}/accept", headers=ann)
        seg = client.get(f"/api/jobs/{job['id']}", headers=ann).json()["segments"][0]
        res = client.get(f"/api/jobs/{job['id']}/segments/{seg['id']}/mt", headers=ann)
        if job is job_on:
            assert res.status_code == 200 and res.json()["text"].startswith("[MT:")
        else:
            assert res.status_code == 403


# --- §8: напоминания о дедлайне ---


def test_deadline_reminder(client, pm, ann):
    from datetime import datetime, timedelta

    from app.helpers import utcnow
    from app.main import _check_deadlines

    deadline = (utcnow() + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
    res = client.post("/api/projects", headers=pm, json={
        "name": "Reminder", "source_lang": "en", "target_langs": ["ru"], "deadline": deadline,
    })
    project = res.json()["id"]
    client.post(f"/api/projects/{project}/documents", headers=pm,
                files={"file": ("rem.txt", io.BytesIO(b"Soon."), "text/plain")})
    job = next(j for j in client.get(f"/api/jobs?project_id={project}", headers=pm).json())
    members = client.get(f"/api/orgs/{ORG}/members", headers=pm).json()
    ann_id = next(m["user"]["id"] for m in members if m["user"]["username"] == "ann")
    client.post(f"/api/jobs/{job['id']}/assign", headers=pm, json={"assignee_id": ann_id})

    _check_deadlines()
    res = client.get("/api/notifications", headers=ann)
    reminders = [n for n in res.json()
                 if n["type"] == "deadline_reminder" and n["payload"]["job_id"] == job["id"]]
    assert any(r["payload"]["offset"] == "24h" for r in reminders)
    assert any(r["payload"]["offset"] == "2h" for r in reminders)
    # повторный прогон не дублирует
    _check_deadlines()
    res = client.get("/api/notifications", headers=ann)
    reminders = [n for n in res.json()
                 if n["type"] == "deadline_reminder" and n["payload"]["job_id"] == job["id"]]
    assert len(reminders) == 2


# --- права: переводчик не создает проекты ---


def test_translator_cannot_manage(client, ann):
    assert client.post("/api/projects", headers=ann,
                       json={"name": "X", "source_lang": "en", "target_langs": ["ru"]}).status_code == 403
    assert client.get("/api/tm/tms", headers=ann).status_code == 403
    assert client.get("/api/tb/tbs", headers=ann).status_code == 403


# --- регрессия: дедлайн с таймзоной сохраняется как UTC ---


def test_deadline_timezone_normalized(client, pm):
    # 18:00+03:00 = 15:00 UTC; ранее SQLite сохранял 18:00 как есть (сдвиг на 3 часа)
    res = client.post("/api/projects", headers=pm, json={
        "name": "Tz", "source_lang": "en", "target_langs": ["ru"],
        "deadline": "2026-12-31T18:00:00+03:00",
    })
    assert res.status_code == 201, res.text
    assert res.json()["deadline"].startswith("2026-12-31T15:00:00")

    # сортировка списка заданий не падает на древних датах (Windows: OSError в timestamp())
    res = client.post("/api/projects", headers=pm, json={
        "name": "Old", "source_lang": "en", "target_langs": ["ru"],
        "deadline": "1960-01-01T00:00:00",
    })
    assert res.status_code == 201
    assert client.get("/api/jobs?sort=deadline", headers=pm).status_code == 200


# --- регрессия: CSV-импорт принимает заголовок в любом регистре ---


def test_csv_import_case_insensitive_headers(client, pm):
    res = client.post("/api/tb/tbs", headers=pm, json={"name": "Case TB", "source_lang": "en", "target_lang": "ru"})
    tb = res.json()["id"]
    csv_data = "Term,Translation,Note,Status\nplatform,платформа,,preferred\n".encode()
    res = client.post(f"/api/tb/tbs/{tb}/import-csv", headers=pm,
                      files={"file": ("tb.csv", io.BytesIO(csv_data), "text/csv")})
    assert res.json() == {"added": 1, "skipped": 0}
    entries = client.get(f"/api/tb/tbs/{tb}/entries", headers=pm).json()
    assert any(e["term"] == "platform" for e in entries)


# --- QA: дополнительные проверки по переводческим практикам (только предупреждения) ---


def test_qa_best_practice_checks(client, pm, ann):
    res = client.post("/api/projects", headers=pm, json={
        "name": "QA practices", "source_lang": "en", "target_langs": ["ru"],
    })
    project = res.json()["id"]
    content = "\n".join([
        "Press the button.",          # двойной пробел и повтор слова
        "See details (attached).",    # несбалансированная скобка
        "Save the file.",             # нет конечной пунктуации
        "Terms and conditions.",      # перевод совпадает с исходником
    ])
    client.post(f"/api/projects/{project}/documents", headers=pm,
                files={"file": ("qa2.txt", io.BytesIO(content.encode()), "text/plain")})
    job = next(j for j in client.get(f"/api/jobs?project_id={project}", headers=pm).json())

    members = client.get(f"/api/orgs/{ORG}/members", headers=pm).json()
    ann_id = next(m["user"]["id"] for m in members if m["user"]["username"] == "ann")
    client.post(f"/api/jobs/{job['id']}/assign", headers=pm, json={"assignee_id": ann_id})
    client.post(f"/api/jobs/{job['id']}/accept", headers=ann)
    detail = client.get(f"/api/jobs/{job['id']}", headers=ann).json()
    seg_space, seg_bracket, seg_punct, seg_identical = detail["segments"]

    for seg, text in (
        (seg_space, "Нажмите  кнопку кнопку  сейчас."),   # double space + repeated word
        (seg_bracket, "Смотрите подробности (приложено."),  # unbalanced bracket
        (seg_punct, "Сохраните файл"),                     # нет конечной точки
        (seg_identical, "Terms and conditions."),          # идентично исходнику
    ):
        client.patch(f"/api/segments/{seg['id']}", headers=ann,
                    json={"target_text": text, "confirmed": True})

    report = client.get(f"/api/jobs/{job['id']}/qa", headers=ann).json()
    codes = [i["code"] for i in report["items"]]
    assert "whitespace" in codes
    assert "repeated_word" in codes
    assert "brackets" in codes
    assert "final_punctuation" in codes
    assert "identical_translation" in codes
    # предупреждения не блокируют сдачу: блокирующих ошибок нет
    assert report["blocking_count"] == 0
    assert report["can_submit"] is True
    assert client.post(f"/api/jobs/{job['id']}/submit", headers=ann).status_code == 200


# --- TMX: экспорт и обратный импорт (roundtrip) ---


def test_tmx_export_roundtrip(client, pm):
    res = client.post("/api/tm/tms", headers=pm, json={"name": "Export TM", "source_lang": "en", "target_lang": "ru"})
    tm = res.json()["id"]
    client.post(f"/api/tm/tms/{tm}/entries", headers=pm,
                json={"source_text": "Save & exit", "target_text": "Сохранить и выйти"})
    client.post(f"/api/tm/tms/{tm}/entries", headers=pm,
                json={"source_text": 'Quotes "test"', "target_text": "Кавычки «тест»"})

    res = client.get(f"/api/tm/tms/{tm}/export", headers=pm)
    assert res.status_code == 200
    assert "tmx" in res.headers["content-type"]
    root = etree.fromstring(res.content)
    assert root.get("version") == "1.4"
    tus = root.findall("body/tu")
    assert len(tus) == 2
    segs = [t.findtext("tuv/seg") for t in tus]
    assert "Save & exit" in segs  # спецсимволы экранированы и восстановлены

    # roundtrip: экспорт импортируется в новую TM без потерь
    export_bytes = client.get(f"/api/tm/tms/{tm}/export", headers=pm).content
    res = client.post("/api/tm/tms", headers=pm, json={"name": "Roundtrip TM", "source_lang": "en", "target_lang": "ru"})
    tm2 = res.json()["id"]
    res = client.post(f"/api/tm/tms/{tm2}/import-tmx", headers=pm,
                      files={"file": ("export.tmx", io.BytesIO(export_bytes), "application/x-tmx+xml")})
    assert res.json() == {"added": 2, "updated": 0, "skipped": 0}
    entries = client.get(f"/api/tm/tms/{tm2}/entries", headers=pm).json()
    assert {e["source_text"]: e["target_text"] for e in entries} == {
        "Save & exit": "Сохранить и выйти",
        'Quotes "test"': "Кавычки «тест»",
    }


# --- TB: CSV-экспорт и обратный импорт (roundtrip) ---


def test_tb_csv_export_roundtrip(client, pm):
    res = client.post("/api/tb/tbs", headers=pm, json={"name": "Export TB", "source_lang": "en", "target_lang": "ru"})
    tb = res.json()["id"]
    client.post(f"/api/tb/tbs/{tb}/entries", headers=pm,
                json={"term": "dashboard", "translation": "панель", "note": "не «дашборд»", "status": "preferred"})
    client.post(f"/api/tb/tbs/{tb}/entries", headers=pm,
                json={"term": "fast", "translation": "быстренько", "note": "разговорное", "status": "forbidden"})

    res = client.get(f"/api/tb/tbs/{tb}/export", headers=pm)
    assert res.status_code == 200
    assert "csv" in res.headers["content-type"]
    csv_bytes = res.content
    text = csv_bytes.decode("utf-8-sig")
    assert text.splitlines()[0] == "term,translation,note,status"
    assert "быстренько" in text

    res = client.post("/api/tb/tbs", headers=pm, json={"name": "Roundtrip TB", "source_lang": "en", "target_lang": "ru"})
    tb2 = res.json()["id"]
    res = client.post(f"/api/tb/tbs/{tb2}/import-csv", headers=pm,
                      files={"file": ("export.csv", io.BytesIO(csv_bytes), "text/csv")})
    assert res.json()["added"] == 2
    entries = client.get(f"/api/tb/tbs/{tb2}/entries", headers=pm).json()
    by_term = {e["term"]: e for e in entries}
    assert by_term["dashboard"]["translation"] == "панель"
    assert by_term["dashboard"]["status"] == "preferred"
    assert by_term["fast"]["status"] == "forbidden"


# --- конкорданс по целевому языку (side=target) ---


def test_concordance_target_side(client, pm, ann):
    res = client.post("/api/projects", headers=pm, json={
        "name": "Concordance", "source_lang": "en", "target_langs": ["ru"],
    })
    project = res.json()["id"]
    res = client.post("/api/tm/tms", headers=pm, json={"name": "Conc TM", "source_lang": "en", "target_lang": "ru"})
    tm = res.json()["id"]
    client.post(f"/api/tm/tms/{tm}/entries", headers=pm,
                json={"source_text": "The invoice has been paid.", "target_text": "Счет оплачен полностью."})
    client.post(f"/api/projects/{project}/resources", headers=pm, json={"tm_id": tm})
    # задание переводчику, чтобы был доступ к конкордансу проекта
    client.post(f"/api/projects/{project}/documents", headers=pm,
                files={"file": ("conc.txt", io.BytesIO(b"Some text."), "text/plain")})
    job = next(j for j in client.get(f"/api/jobs?project_id={project}", headers=pm).json())
    members = client.get(f"/api/orgs/{ORG}/members", headers=pm).json()
    ann_id = next(m["user"]["id"] for m in members if m["user"]["username"] == "ann")
    client.post(f"/api/jobs/{job['id']}/assign", headers=pm, json={"assignee_id": ann_id})

    # поиск по целевому языку находит перевод
    res = client.get(f"/api/projects/{project}/tm/search", headers=ann,
                     params={"text": "Счет оплачен полностью.", "target_lang": "ru", "side": "target"})
    matches = res.json()
    assert matches and matches[0]["match_percentage"] == 100
    assert matches[0]["source_match"] == "The invoice has been paid."

    # а поиск по исходникам этот текст не находит
    res = client.get(f"/api/projects/{project}/tm/search", headers=ann,
                     params={"text": "Счет оплачен полностью.", "target_lang": "ru"})
    assert res.json() == []

    # конкорданс по слову: «как переводили слово invoice» — поиск вхождения
    res = client.get(f"/api/projects/{project}/tm/search", headers=ann,
                     params={"text": "invoice", "target_lang": "ru", "mode": "concordance"})
    matches = res.json()
    assert matches and matches[0]["match_type"] == "concordance"
    assert matches[0]["target_suggestion"] == "Счет оплачен полностью."

    # конкорданс по слову из перевода (side=target)
    res = client.get(f"/api/projects/{project}/tm/search", headers=ann,
                     params={"text": "оплачен", "target_lang": "ru", "side": "target", "mode": "concordance"})
    matches = res.json()
    assert matches and matches[0]["match_type"] == "concordance"

    # слово с тем же корнем, но другим окончанием не матчится (граница слова)
    res = client.get(f"/api/projects/{project}/tm/search", headers=ann,
                     params={"text": "оплачено", "target_lang": "ru", "side": "target", "mode": "concordance"})
    assert res.json() == []

    # некорректный side отвергается
    res = client.get(f"/api/projects/{project}/tm/search", headers=ann,
                     params={"text": "x", "target_lang": "ru", "side": "both"})
    assert res.status_code == 422


# --- подсчет слов: языки без пробелов (CJK) ---


def test_cjk_word_count():
    from app.services.words import count_words

    assert count_words("Hello world") == 2
    assert count_words("你好世界") == 4              # каждая идеограмма — слово
    assert count_words("こんにちは") == 5              # хирагана
    assert count_words("안녕하세요") == 5              # хангыль
    assert count_words("Mix 混合 text") == 4          # 1 + 2 + 1
    assert count_words('Тег <x id="1"/> не в счет') == 4  # тег не считается
