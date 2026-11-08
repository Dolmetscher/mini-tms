"""Выравнивание параллельных файлов -> TM: предложение пар и подтверждение.

Технический текст с числами и именами выравнивается якорями; предложение —
НЕ запись: человек подтверждает пары отдельно. Пропущенный в переводе
абзац не должен склеиваться с соседним. Подтверждение — upsert в TM.
"""
import io
import os
import tempfile

_TEST_DIR = tempfile.mkdtemp(prefix="mini-tms-align-")
os.environ.setdefault("TMS_DATABASE_URL", f"sqlite:///{_TEST_DIR}/test.db".replace("\\", "/"))
os.environ.setdefault("TMS_DATA_DIR", os.path.join(_TEST_DIR, "data").replace("\\", "/"))
os.environ.setdefault("TMS_MT_PROVIDER", "mock")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.services.align import align_units  # noqa: E402

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


@pytest.fixture(scope="module")
def tm(client, pm):
    res = client.post("/api/tm/tms", headers=pm, json={
        "name": "Align TM", "source_lang": "en", "target_lang": "ru",
    })
    assert res.status_code == 201, res.text
    return res.json()


SOURCE_TEXT = "\n".join([
    "Acme released version 5.2 in March.",
    "The API handles 10,000 requests per second.",  # запятая в числе
    "Contact John Smith for details.",
    "Pricing starts at 99 EUR.",
])

# Перевод: те же якоря (5.2, 10 000, Acme, John Smith, 99, EUR), но третий
# абзац перевода — про другое (переводчик пропустил абзац про API).
TARGET_TEXT = "\n".join([
    "Acme выпустила версию 5.2 в марте.",
    "API обрабатывает 10 000 запросов в секунду.",
    "Цены начинаются от 99 EUR.",
])


def test_align_units_anchor_scoring():
    """Прямой вызов сервиса: якоря дают пары, пропущенный абзац не склеивается."""
    source = SOURCE_TEXT.split("\n")
    target = TARGET_TEXT.split("\n")
    pairs = align_units(source, target)
    by_source = {p["source"]: p for p in pairs}
    assert set(by_source) == {
        "Acme released version 5.2 in March.",
        "The API handles 10,000 requests per second.",
        "Pricing starts at 99 EUR.",
    }, pairs  # абзац про John Smith остался без пары
    assert by_source["Acme released version 5.2 in March."]["target"] == \
        "Acme выпустила версию 5.2 в марте."
    assert by_source["Pricing starts at 99 EUR."]["target"] == \
        "Цены начинаются от 99 EUR."
    # Скоринг: все якоря первой пары совпали
    assert by_source["Acme released version 5.2 in March."]["score"] >= 0.5


def test_align_endpoint_flow(client, pm, ann, tm):
    files = {
        "source_file": ("old.txt", io.BytesIO(SOURCE_TEXT.encode()), "text/plain"),
        "target_file": ("old_ru.txt", io.BytesIO(TARGET_TEXT.encode()), "text/plain"),
    }
    # Переводчик выравниванием не управляет
    res = client.post(f"/api/tm/tms/{tm['id']}/align", headers=ann, files=files)
    assert res.status_code == 403

    res = client.post(f"/api/tm/tms/{tm['id']}/align", headers=pm, files=files)
    assert res.status_code == 200, res.text
    proposal = res.json()
    assert proposal["source_units"] == 4 and proposal["target_units"] == 3
    assert len(proposal["pairs"]) == 3  # пропущенный абзац не в парах

    # Подтверждаем только две пары из трех — человек снял галочку
    chosen = [p for p in proposal["pairs"] if "Pricing" not in p["source"]]
    res = client.post(f"/api/tm/tms/{tm['id']}/align/confirm", headers=pm,
                      json={"pairs": [{"source": p["source"], "target": p["target"]} for p in chosen]})
    assert res.status_code == 200, res.text
    assert res.json() == {"added": 2, "updated": 0, "skipped": 0}

    entries = client.get(f"/api/tm/tms/{tm['id']}/entries", headers=pm).json()
    sources = {e["source_text"] for e in entries}
    assert "Acme released version 5.2 in March." in sources
    assert "Pricing starts at 99 EUR." not in sources  # не подтверждена — не записана

    # Повторное подтверждение той же пары с новым переводом — upsert
    res = client.post(f"/api/tm/tms/{tm['id']}/align/confirm", headers=pm,
                      json={"pairs": [{"source": "Acme released version 5.2 in March.",
                                       "target": "Acme выпустила версию 5.2 в марте (обновлено)."}]})
    assert res.json() == {"added": 0, "updated": 1, "skipped": 0}
    entries = client.get(f"/api/tm/tms/{tm['id']}/entries", headers=pm).json()
    entry = next(e for e in entries if e["source_text"] == "Acme released version 5.2 in March.")
    assert entry["target_text"].endswith("(обновлено).")


def test_align_validation(client, pm, tm):
    # Не тот формат
    files = {
        "source_file": ("doc.pdf", io.BytesIO(b"%PDF"), "application/pdf"),
        "target_file": ("t.txt", io.BytesIO(b"text"), "text/plain"),
    }
    res = client.post(f"/api/tm/tms/{tm['id']}/align", headers=pm, files=files)
    assert res.status_code == 422

    # Пустой файл
    files = {
        "source_file": ("s.txt", io.BytesIO(b""), "text/plain"),
        "target_file": ("t.txt", io.BytesIO(b"text"), "text/plain"),
    }
    res = client.post(f"/api/tm/tms/{tm['id']}/align", headers=pm, files=files)
    assert res.status_code == 422


def test_align_no_anchors_positional():
    """Художественный текст без якорей при равном числе единиц — позиционная
    гипотеза со score 0 (слабая, unchecked по умолчанию)."""
    source = ["Роза упала на лапу Азора.", "Весна пришла."]
    target = ["Роза упала, весна настала.", "Пришла весна."]
    pairs = align_units(source, target)
    assert len(pairs) == 2
    assert all(p["score"] == 0.0 for p in pairs)
    assert pairs[0]["source"] == source[0]
