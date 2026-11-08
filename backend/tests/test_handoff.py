"""XLIFF-хендофф: экспорт задания для внешнего CAT-инструмента и вливание
возвращенного файла.

Сценарий: pm экспортирует пакет -> «внешний переводчик» (тест) правит target
в XLIFF, проставляет state=translated -> файл возвращается import-xliff ->
сегменты обновлены, подтвержденные переводы записаны в TM (use_for_write),
повторы автопропагированы, история правок полна. Чужой свежий лок сегмента
обходит этот сегмент (skip_locked). Битый XML и XLIFF 2.0 — 422.
"""
import io
import os
import tempfile

_TEST_DIR = tempfile.mkdtemp(prefix="mini-tms-handoff-")
os.environ.setdefault("TMS_DATABASE_URL", f"sqlite:///{_TEST_DIR}/test.db".replace("\\", "/"))
os.environ.setdefault("TMS_DATA_DIR", os.path.join(_TEST_DIR, "data").replace("\\", "/"))
os.environ.setdefault("TMS_MT_PROVIDER", "mock")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from lxml import etree  # noqa: E402

from app.main import app  # noqa: E402

ORG = 1
NS = {"x": "urn:oasis:names:tc:xliff:document:1.2"}


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


@pytest.fixture(scope="module")
def ivan(client):
    return login(client, "ivan", "ivan123")


@pytest.fixture(scope="module")
def job(client, pm, ann):
    """Задание с TM проекта (use_for_write) — подтверждения должны попадать в TM."""
    res = client.post("/api/projects", headers=pm, json={
        "name": "Handoff project", "source_lang": "en", "target_langs": ["ru"],
    })
    assert res.status_code == 201, res.text
    project = res.json()["id"]

    res = client.post("/api/tm/tms", headers=pm, json={
        "name": "Handoff TM", "source_lang": "en", "target_lang": "ru",
    })
    assert res.status_code == 201, res.text
    tm_id = res.json()["id"]
    assert client.post(f"/api/projects/{project}/resources", headers=pm, json={
        "tm_id": tm_id, "use_for_search": True, "use_for_write": True,
    }).status_code == 201

    source = 'Open <x id="1"/> file.\nSecond line here.'
    res = client.post(f"/api/projects/{project}/documents", headers=pm,
                      files={"file": ("handoff.txt", io.BytesIO(source.encode()), "text/plain")})
    assert res.status_code == 201, res.text
    job_id = client.get(f"/api/jobs?project_id={project}", headers=pm).json()[0]["id"]
    members = client.get(f"/api/orgs/{ORG}/members", headers=pm).json()
    ann_id = next(m["user"]["id"] for m in members if m["user"]["username"] == "ann")
    assert client.post(f"/api/jobs/{job_id}/assign", headers=pm,
                       json={"assignee_id": ann_id}).status_code == 200
    assert client.post(f"/api/jobs/{job_id}/accept", headers=ann).status_code == 200
    return client.get(f"/api/jobs/{job_id}", headers=ann).json()


def test_export_structure(client, pm, job):
    res = client.get(f"/api/jobs/{job['id']}/export-xliff", headers=pm)
    assert res.status_code == 200, res.text
    assert res.headers["Content-Type"].startswith("application/x-xliff+xml")

    root = etree.fromstring(res.content)
    assert root.get("version") == "1.2"
    file_el = root.find("x:file", NS)
    assert file_el.get("source-language") == "en" and file_el.get("target-language") == "ru"
    units = root.findall("x:file/x:body/x:trans-unit", NS)
    assert len(units) == 2
    # Позиции сегментов — id единиц; теги восстановлены в inline-элементы
    assert [u.get("id") for u in units] == ["0", "1"]
    first_source = units[0].find("x:source", NS)
    assert first_source is not None
    x_el = first_source.find("x:x", NS)
    assert x_el is not None and x_el.get("id") == "1"
    # target пустых сегментов без содержимого; state нет
    first_target = units[0].find("x:target", NS)
    assert first_target is not None and not (first_target.text or "").strip()


def test_roundtrip_import(client, pm, ann, job):
    """Внешний инструмент переводит и подтверждает; вливаем обратно."""
    package = client.get(f"/api/jobs/{job['id']}/export-xliff", headers=pm).content
    root = etree.fromstring(package)
    units = root.findall("x:file/x:body/x:trans-unit", NS)

    # «Внешний переводчик»: первый — подтвержден (state), второй — черновик
    tgt0 = units[0].find("x:target", NS)
    tgt0.text = "Откройте "
    x_el = etree.SubElement(tgt0, f"{{{NS['x']}}}x", id="1")
    x_el.tail = " файл."
    tgt0.set("state", "translated")
    tgt1 = units[1].find("x:target", NS)
    tgt1.text = "Вторая строка."
    modified = etree.tostring(root, xml_declaration=True, encoding="UTF-8")

    res = client.post(f"/api/jobs/{job['id']}/import-xliff", headers=ann,
                      files={"file": ("back.xliff", io.BytesIO(modified), "application/x-xliff+xml")})
    assert res.status_code == 200, res.text
    stats = res.json()
    assert stats["updated"] == 2 and stats["confirmed"] == 1
    assert stats["skipped"] == 0 and stats["skipped_locked"] == 0

    detail = client.get(f"/api/jobs/{job['id']}", headers=ann).json()
    seg0, seg1 = detail["segments"]
    assert seg0["target_text"] == 'Откройте <x id="1"/> файл.'
    assert seg0["confirmed"] is True and seg0["translation_status"] == "translated"
    assert seg1["target_text"] == "Вторая строка."
    assert seg1["confirmed"] is False  # без state — черновик

    # Подтвержденный перевод попал в TM проекта (use_for_write)
    tms = client.get("/api/tm/tms", headers=pm).json()
    handoff = next(t for t in tms if t["name"] == "Handoff TM")
    entries = client.get(f"/api/tm/tms/{handoff['id']}/entries", headers=pm).json()
    assert any(e["source_text"] == 'Open <x id="1"/> file.'
               and e["target_text"] == 'Откройте <x id="1"/> файл.' for e in entries)

    # История правок фиксирует хендофф-импорт
    history = client.get(f"/api/segments/{seg0['id']}/history", headers=ann).json()
    assert any(h["target_text"] == 'Откройте <x id="1"/> файл.' for h in history)


def test_foreign_lock_skips_segment(client, pm, ann, ivan, job):
    """Сегмент под чужим свежим локом не затирается импортом."""
    # ivan — другой переводчик: он не участник задания, лок недоступен...
    # лок ставит участник: ann уже assignee, поэтому «чужой» = pm (менеджмент).
    detail = client.get(f"/api/jobs/{job['id']}", headers=pm).json()
    seg0 = detail["segments"][0]
    assert client.post(f"/api/segments/{seg0['id']}/lock", headers=ivan).status_code in (403, 404, 423)

    # pm не assignee: сохранение сегментов ему запрещено, но импорт хендоффа
    # менеджменту разрешен — на сегменте лок ann (heartbeat редактора):
    assert client.post(f"/api/segments/{seg0['id']}/lock", headers=ann).status_code == 200

    package = client.get(f"/api/jobs/{job['id']}/export-xliff", headers=pm).content
    root = etree.fromstring(package)
    units = root.findall("x:file/x:body/x:trans-unit", NS)
    units[0].find("x:target", NS).text = "Затирающий текст."
    modified = etree.tostring(root, xml_declaration=True, encoding="UTF-8")

    res = client.post(f"/api/jobs/{job['id']}/import-xliff", headers=pm,
                      files={"file": ("back2.xliff", io.BytesIO(modified), "application/x-xliff+xml")})
    assert res.status_code == 200, res.text
    assert res.json()["skipped_locked"] == 1

    detail = client.get(f"/api/jobs/{job['id']}", headers=pm).json()
    assert detail["segments"][0]["target_text"] != "Затирающий текст."
    # прибираем за собой: снятие лока ann
    assert client.delete(f"/api/segments/{seg0['id']}/lock", headers=ann).status_code == 204


def test_invalid_xliff_rejected(client, pm, job):
    res = client.post(f"/api/jobs/{job['id']}/import-xliff", headers=pm,
                      files={"file": ("bad.xliff", io.BytesIO(b"not xml at <all"), "application/x-xliff+xml")})
    assert res.status_code == 422
    assert "xliff" in res.json()["detail"].lower()

    xliff2 = (
        '<xliff xmlns="urn:oasis:names:tc:xliff:document:2.0" version="2.0">'
        "<file id=\"f\"><units/></file></xliff>"
    )
    res = client.post(f"/api/jobs/{job['id']}/import-xliff", headers=pm,
                      files={"file": ("v2.xliff", io.BytesIO(xliff2.encode()), "application/x-xliff+xml")})
    assert res.status_code == 422
    assert "2.0" in res.json()["detail"]
