"""Тесты доставки проекта (ZIP-пакет целевых файлов) и инвойса.

Закрытие цикла заказа: после сдачи менеджер забирает все готовые переводы
одним архивом (практика delivery package) и выставляет закрывающий документ
(инвойс) — только по фактически сданной работе.
"""
import io
import os
import tempfile
import zipfile
from datetime import datetime, timezone

_TEST_DIR = tempfile.mkdtemp(prefix="mini-tms-dlv-")
os.environ.setdefault("TMS_DATABASE_URL", f"sqlite:///{_TEST_DIR}/test.db".replace("\\", "/"))
os.environ.setdefault("TMS_DATA_DIR", os.path.join(_TEST_DIR, "data").replace("\\", "/"))
os.environ.setdefault("TMS_MT_PROVIDER", "mock")

import pytest  # noqa: E402
from docx import Document as open_docx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

ORG = 1

FULL_RATES = {
    "exact": 0.02, "fuzzy_95_99": 0.04, "fuzzy_85_94": 0.05,
    "fuzzy_75_84": 0.06, "fuzzy_60_74": 0.08, "repetition": 0.01,
    "no_match": 0.10,
}


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
def project(client, pm, ann):
    """Проект en→[sv, da]: sv-задание сдается, da-задание остается в работе.

    Инвойс и доставка обязаны учитывать только сданное — это и проверяется.
    """
    for target in ("sv", "da"):
        res = client.post("/api/rates", headers=pm, json={
            "name": f"Delivery EN→{target.upper()}", "source_lang": "en",
            "target_lang": target, "currency": "EUR", "rates": FULL_RATES,
        })
        assert res.status_code in (201, 409), res.text  # 409 — пара занята другим тестом

    res = client.post("/api/projects", headers=pm, json={
        "name": "Delivery Package", "source_lang": "en", "target_langs": ["sv", "da"],
    })
    assert res.status_code == 201, res.text
    project_id = res.json()["id"]
    doc = io.BytesIO("First delivery line.\nSecond delivery line.\n".encode())
    res = client.post(f"/api/projects/{project_id}/documents", headers=pm,
                      files={"file": ("Delivery Pkg.txt", doc, "text/plain")})
    assert res.status_code == 201, res.text

    members = client.get(f"/api/orgs/{ORG}/members", headers=pm).json()
    ann_id = next(m["user"]["id"] for m in members if m["user"]["username"] == "ann")
    jobs = client.get(f"/api/jobs?project_id={project_id}", headers=pm).json()
    assert len(jobs) == 2
    yield project_id, [job["id"] for job in jobs], ann_id

    # «Сдать» sv-задание (fixture с yield выполняется после тестов модуля):
    # фактические действия делаются в самих тестах ниже.


@pytest.fixture(scope="module")
def submitted(client, pm, ann, project):
    """sv-задание переведено и сдано (однократно для всего модуля); da — в работе."""
    project_id, job_ids, ann_id = project
    sv_job = next(
        job for job in client.get(f"/api/jobs?project_id={project_id}", headers=pm).json()
        if job["target_lang"] == "sv"
    )
    assert client.post(f"/api/jobs/{sv_job['id']}/assign", headers=pm,
                       json={"assignee_id": ann_id}).status_code == 200
    assert client.post(f"/api/jobs/{sv_job['id']}/accept", headers=ann).status_code == 200
    segments = client.get(f"/api/jobs/{sv_job['id']}", headers=ann).json()["segments"]
    translations = ["Первая строка доставки.", "Вторая строка доставки."]
    for segment, target in zip(segments, translations):
        res = client.patch(f"/api/segments/{segment['id']}", headers=ann,
                           json={"target_text": target, "confirmed": True})
        assert res.status_code == 200, res.text
    assert client.post(f"/api/jobs/{sv_job['id']}/submit", headers=ann).status_code == 200
    return sv_job


def test_delivery_requires_submission(client, pm, ann, project):
    """До сдачи: доставки и инвойса нет (409)."""
    # Отдельный проект — «сырой», без сданных заданий
    res = client.post("/api/projects", headers=pm, json={
        "name": "Delivery Empty", "source_lang": "en", "target_langs": ["sv"],
    })
    assert res.status_code == 201, res.text
    empty_id = res.json()["id"]
    assert client.get(f"/api/projects/{empty_id}/deliver", headers=pm).status_code == 409
    assert client.get(f"/api/projects/{empty_id}/invoice", headers=pm).status_code == 409
    # Переводчику доставка не доступна (данные менеджмента)
    assert client.get(f"/api/projects/{empty_id}/deliver", headers=ann).status_code == 403


def test_delivery_zip_package(client, pm, ann, submitted):
    """ZIP: только сданные задания, манифест, права менеджмента."""
    project_id = submitted["project_id"]
    res = client.get(f"/api/projects/{project_id}/deliver", headers=pm)
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith("application/zip")

    archive = zipfile.ZipFile(io.BytesIO(res.content))
    names = set(archive.namelist())
    # sv-задание сдано -> его файл в пакете; da-задание в работе -> его нет
    assert names == {"Delivery Pkg_sv.txt", "manifest.txt"}
    payload = archive.read("Delivery Pkg_sv.txt").decode("utf-8")
    assert "Первая строка доставки." in payload
    assert "Вторая строка доставки." in payload
    manifest = archive.read("manifest.txt").decode("utf-8")
    assert "Delivery Pkg_sv.txt" in manifest
    assert "submitted" in manifest
    assert "da" not in manifest  # несданное задание не фигурирует в манифесте

    # Переводчику — 403 (пакет содержит готовый продукт)
    assert client.get(f"/api/projects/{project_id}/deliver", headers=ann).status_code == 403


def test_invoice_only_billed_work(client, pm, submitted):
    """Инвойс: только сданные задания, детерминированный номер, итоги."""
    project_id = submitted["project_id"]

    res = client.get(f"/api/projects/{project_id}/invoice", headers=pm)
    assert res.status_code == 200, res.text
    invoice = res.json()
    # Только sv-задание: da в работе и не инвойсируется
    assert [job["target_lang"] for job in invoice["jobs"]] == ["sv"]
    assert invoice["totals"] and invoice["totals"][0]["total"] > 0
    assert invoice["totals"][0]["currency"] == "EUR"
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    assert invoice["invoice_no"] == f"INV-{project_id:04d}-{today}"
    assert invoice["issued_on"] == datetime.now(timezone.utc).date().isoformat()
    assert invoice["missing_pairs"] == []

    # Котировка проекта считает ОБА задания (продажа), инвойс — только сданное
    quote = client.get(f"/api/projects/{project_id}/quote", headers=pm).json()
    assert {job["target_lang"] for job in quote["jobs"]} == {"sv", "da"}
    assert quote["totals"][0]["total"] > invoice["totals"][0]["total"]


def test_invoice_docx_export(client, pm, ann, submitted):
    """Инвойс в DOCX: реквизиты, строки, итог; переводчику не доступен."""
    project_id = submitted["project_id"]
    res = client.get(f"/api/projects/{project_id}/invoice/export", headers=pm)
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument"
    )
    assert res.content[:2] == b"PK"  # DOCX — это ZIP

    doc = open_docx(io.BytesIO(res.content))
    texts = [p.text for p in doc.paragraphs]
    joined = "\n".join(texts)
    assert "Счет №" in joined
    assert f"INV-{project_id:04d}" in joined
    assert "Delivery Package" in joined
    table = doc.tables[0]
    row = [cell.text for cell in table.rows[1].cells]
    assert row[0] == "Delivery Pkg.txt"
    assert row[1] == "sv"
    assert "К оплате:" in joined
    assert "EUR" in joined

    # Имя файла — номер инвойса
    assert "INV-" in res.headers["Content-Disposition"]
    # Переводчику закрывающий документ не выдается
    assert client.get(f"/api/projects/{project_id}/invoice/export", headers=ann).status_code == 403


def test_invoice_without_rate_card(client, pm, ann):
    """Сданные задания без прайс-листа: JSON сообщает пары, экспорт — 422."""
    res = client.post("/api/projects", headers=pm, json={
        "name": "Delivery NoCard", "source_lang": "en", "target_langs": ["fi"],
    })
    assert res.status_code == 201, res.text
    project_id = res.json()["id"]
    doc = io.BytesIO("No card line.\n".encode())
    res = client.post(f"/api/projects/{project_id}/documents", headers=pm,
                      files={"file": ("NoCard.txt", doc, "text/plain")})
    assert res.status_code == 201, res.text

    members = client.get(f"/api/orgs/{ORG}/members", headers=pm).json()
    ann_id = next(m["user"]["id"] for m in members if m["user"]["username"] == "ann")
    job = client.get(f"/api/jobs?project_id={project_id}", headers=pm).json()[0]
    assert client.post(f"/api/jobs/{job['id']}/assign", headers=pm,
                       json={"assignee_id": ann_id}).status_code == 200
    assert client.post(f"/api/jobs/{job['id']}/accept", headers=ann).status_code == 200
    seg = client.get(f"/api/jobs/{job['id']}", headers=ann).json()["segments"][0]
    assert client.patch(f"/api/segments/{seg['id']}", headers=ann, json={
        "target_text": "Rivi ilman hinnastoa.", "confirmed": True,
    }).status_code == 200
    assert client.post(f"/api/jobs/{job['id']}/submit", headers=ann).status_code == 200

    # Задание сдано, но пара en→fi не покрыта прайс-листом
    invoice = client.get(f"/api/projects/{project_id}/invoice", headers=pm)
    assert invoice.status_code == 200, invoice.text
    assert invoice.json()["jobs"] == []
    assert invoice.json()["missing_pairs"] == ["en→fi"]
    assert client.get(f"/api/projects/{project_id}/invoice/export", headers=pm).status_code == 422
