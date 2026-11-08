"""Тесты биллинга: прайс-листы, котировки заданий/проектов, экспорт котировки.

Используется общая тестовая БД всего набора (модули импортируются в одном
процессе); сценарии изолированы собственными проектами и уникальными
языковыми парами.
"""
import io
import os
import tempfile

_TEST_DIR = tempfile.mkdtemp(prefix="mini-tms-bill-")
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


# --- Прайс-листы: CRUD, дефолты, валидация, права ---


def test_rate_card_crud_and_permissions(client, pm, ann):
    # Переводчику ставки не видны (данные менеджмента)
    assert client.get("/api/rates", headers=ann).status_code == 403
    assert client.post("/api/rates", headers=ann, json={
        "name": "X", "source_lang": "en", "target_lang": "de",
    }).status_code == 403

    # Создание с полным набором ставок
    res = client.post("/api/rates", headers=pm, json={
        "name": "Main EN→DE", "source_lang": "en", "target_lang": "de",
        "currency": "eur",  # приводится к верхнему регистру
        "rates": FULL_RATES, "min_charge": 0.0,
    })
    assert res.status_code == 201, res.text
    card = res.json()
    assert card["currency"] == "EUR"
    assert card["rates"] == FULL_RATES

    # Частичные ставки дополняются дефолтами config
    res = client.post("/api/rates", headers=pm, json={
        "name": "Partial EN→IT", "source_lang": "en", "target_lang": "it",
        "rates": {"no_match": 0.20},
    })
    assert res.status_code == 201, res.text
    partial = res.json()
    assert partial["rates"]["no_match"] == 0.20
    assert partial["rates"]["exact"] == 0.03  # дефолт
    assert partial["min_charge"] == 0.0

    # Неизвестная категория ставки — 422
    assert client.post("/api/rates", headers=pm, json={
        "name": "Bad", "source_lang": "en", "target_lang": "es",
        "rates": {"oops": 1.0},
    }).status_code == 422

    # Дубль пары+валюты — 409
    assert client.post("/api/rates", headers=pm, json={
        "name": "Dup", "source_lang": "en", "target_lang": "de", "currency": "EUR",
    }).status_code == 409

    # Правка: минимальная сумма и точечная ставка
    res = client.patch(f"/api/rates/{card['id']}", headers=pm, json={
        "min_charge": 25.0, "rates": {"no_match": 0.12},
    })
    assert res.status_code == 200, res.text
    updated = res.json()
    assert updated["min_charge"] == 25.0
    assert updated["rates"]["no_match"] == 0.12
    assert updated["rates"]["exact"] == 0.02  # не переданное — не меняется

    # Список: обе карты организации
    names = {c["name"] for c in client.get("/api/rates", headers=pm).json()}
    assert {"Main EN→DE", "Partial EN→IT"} <= names

    # Удаление частичной карты (пара en→it освобождается для других тестов)
    assert client.delete(f"/api/rates/{partial['id']}", headers=pm).status_code == 204
    assert client.delete(f"/api/rates/{partial['id']}", headers=pm).status_code == 404


# --- Котировка задания: математика категорий и минимальная сумма ---


def _quoted_job(client: TestClient, pm: dict, name: str, lines: list[str], pair: str):
    """Проект + TM с точным и fuzzy-совпадением + документ; возвращает job."""
    src, tgt = pair.split("->")
    res = client.post("/api/projects", headers=pm, json={
        "name": name, "source_lang": src, "target_langs": [tgt],
    })
    assert res.status_code == 201, res.text
    project = res.json()["id"]

    tm = client.post("/api/tm/tms", headers=pm, json={
        "name": f"{name} TM", "source_lang": src, "target_lang": tgt,
    }).json()
    for source, target in [
        ("The quick brown fox jumps over the lazy dog.", "Быстрая бурая лиса."),
        ("Alpha beta gamma delta epsilon zeta eta theta iota kappa.", "Греческий алфавит."),
    ]:
        assert client.post(f"/api/tm/tms/{tm['id']}/entries", headers=pm, json={
            "source_text": source, "target_text": target,
        }).status_code == 201
    assert client.post(f"/api/projects/{project}/resources", headers=pm, json={
        "tm_id": tm["id"], "use_for_search": True, "use_for_write": False,
    }).status_code == 201

    doc = io.BytesIO("\n".join(lines).encode())
    res = client.post(f"/api/projects/{project}/documents", headers=pm,
                      files={"file": (f"{name}.txt", doc, "text/plain")})
    assert res.status_code == 201, res.text
    return client.get(f"/api/jobs?project_id={project}", headers=pm).json()[0]


def test_job_quote_math_and_min_charge(client, pm, ann):
    lines = [
        "The quick brown fox jumps over the lazy dog.",        # exact, 9 слов
        "Alpha beta gamma delta epsilon zeta eta theta iota kappa!",  # fuzzy 95–99, 10 слов
        "Totally unrelated sentence about weather and mountains.",   # no match, 7 слов
        "Totally unrelated sentence about weather and mountains.",   # повтор, 7 слов
    ]
    job = _quoted_job(client, pm, "Quote Math", lines, "en->fr")

    card = client.post("/api/rates", headers=pm, json={
        "name": "Quote FR", "source_lang": "en", "target_lang": "fr",
        "rates": FULL_RATES, "min_charge": 0.0,
    }).json()

    # Переводчику котировки недоступны
    assert client.get(f"/api/jobs/{job['id']}/quote", headers=ann).status_code == 403

    res = client.get(f"/api/jobs/{job['id']}/quote", headers=pm,
                     params={"rate_card_id": card["id"]})
    assert res.status_code == 200, res.text
    quote = res.json()
    by_code = {line["code"]: line for line in quote["lines"]}
    assert by_code["exact"]["words"] == 9 and by_code["exact"]["amount"] == pytest.approx(0.18)
    assert by_code["fuzzy_95_99"]["words"] == 10 and by_code["fuzzy_95_99"]["amount"] == pytest.approx(0.40)
    assert by_code["no_match"]["words"] == 7 and by_code["no_match"]["amount"] == pytest.approx(0.70)
    assert by_code["repetition"]["words"] == 7 and by_code["repetition"]["amount"] == pytest.approx(0.07)
    assert quote["subtotal"] == pytest.approx(1.35)
    assert not quote["min_charge_applied"]
    assert quote["total"] == pytest.approx(1.35)
    assert quote["currency"] == "EUR" and quote["rate_card"] == "Quote FR"

    # Минимальная сумма заказа: 1.35 < 25 -> итог 25.00
    assert client.patch(f"/api/rates/{card['id']}", headers=pm, json={"min_charge": 25.0}).status_code == 200
    quote = client.get(f"/api/jobs/{job['id']}/quote", headers=pm,
                       params={"rate_card_id": card["id"]}).json()
    assert quote["min_charge_applied"]
    assert quote["subtotal"] == pytest.approx(1.35)
    assert quote["total"] == 25.0

    # Нет прайс-листа для пары — 422 с подсказкой
    res = client.post("/api/projects", headers=pm, json={
        "name": "Quote NoCard", "source_lang": "en", "target_langs": ["es"],
    })
    doc = io.BytesIO(b"Some text here.\n")
    res2 = client.post(f"/api/projects/{res.json()['id']}/documents", headers=pm,
                       files={"file": ("nocard.txt", doc, "text/plain")})
    assert res2.status_code == 201
    job_es = client.get(f"/api/jobs?project_id={res.json()['id']}", headers=pm).json()[0]
    res = client.get(f"/api/jobs/{job_es['id']}/quote", headers=pm)
    assert res.status_code == 422
    assert "en->es" in res.json()["detail"]

    # Экспорт котировки в DOCX: таблица 7 категорий + итоги
    res = client.get(f"/api/jobs/{job['id']}/quote/export", headers=pm,
                     params={"rate_card_id": card["id"]})
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    assert "quote" in res.headers["content-disposition"]
    docx = open_docx(io.BytesIO(res.content))
    texts = [p.text for p in docx.paragraphs]
    assert any("Котировка перевода" in t for t in texts)
    assert any("Итого: 25.00 EUR" in t for t in texts)
    assert any("Применена минимальная сумма" in t for t in texts)
    assert len(docx.tables[0].rows) == 8  # заголовок + 7 категорий


# --- Котировка проекта: агрегация заданий и валют, пары без прайс-листа ---


def test_project_quote_aggregation(client, pm):
    # en→ru (сид-карта) + en→de (карта из CRUD-теста): оба задания котируются
    res = client.post("/api/projects", headers=pm, json={
        "name": "Quote Multi", "source_lang": "en", "target_langs": ["ru", "de"],
    })
    assert res.status_code == 201, res.text
    project = res.json()["id"]
    doc = io.BytesIO("Shared source text for two targets.\n".encode())
    res = client.post(f"/api/projects/{project}/documents", headers=pm,
                      files={"file": ("multi.txt", doc, "text/plain")})
    assert res.status_code == 201, res.text

    quote = client.get(f"/api/projects/{project}/quote", headers=pm).json()
    assert len(quote["jobs"]) == 2
    assert {job["target_lang"] for job in quote["jobs"]} == {"ru", "de"}
    assert quote["missing_pairs"] == []
    assert len(quote["totals"]) == 1
    assert quote["totals"][0]["currency"] == "EUR"
    assert quote["totals"][0]["total"] == pytest.approx(
        sum(job["total"] for job in quote["jobs"]))

    # Пара без прайс-листа попадает в missing_pairs, остальное котируется
    res = client.post("/api/projects", headers=pm, json={
        "name": "Quote Gap", "source_lang": "en", "target_langs": ["ru", "es"],
    })
    assert res.status_code == 201
    project = res.json()["id"]
    doc = io.BytesIO("Text with uncovered target.\n".encode())
    res = client.post(f"/api/projects/{project}/documents", headers=pm,
                      files={"file": ("gap.txt", doc, "text/plain")})
    assert res.status_code == 201
    quote = client.get(f"/api/projects/{project}/quote", headers=pm).json()
    assert quote["missing_pairs"] == ["en\u2192es"]
    assert [job["target_lang"] for job in quote["jobs"]] == ["ru"]
