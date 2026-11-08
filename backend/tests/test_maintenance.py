"""Тесты обслуживания записей TM/TB (maintenance): правка и удаление.

Практика ведения ресурсов: устаревший перевод в памяти или неверный термин
должны правиться/удаляться на месте — иначе ресурс работает против
качества. Права — менеджмент (pm/admin); переводчик читает через проект.
"""
import io
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


# --- CSV-импорт TM (паритет с TB) и пагинация записей ---


def test_tm_import_csv_and_pagination(client, pm):
    res = client.post("/api/tm/tms", headers=pm, json={
        "name": "CSV TM", "source_lang": "en", "target_lang": "de",
    })
    tm_id = res.json()["id"] if res.status_code == 201 else next(
        t["id"] for t in client.get("/api/tm/tms", headers=pm).json() if t["name"] == "CSV TM"
    )

    # Шаблон source,target; заголовок в смешанном регистре принимается;
    # строка с пустым переводом skip-ается; дубль source — обновление.
    csv_data = (
        "Source,Target\n"
        "Press the button.,Drücken Sie die Taste.\n"
        "Broken row,\n"
        "Press the button.,Druecken Sie die Taste. (v2)\n"
        "Close the door.,Schließen Sie die Tür.\n"
        "Open the window.,Öffnen Sie das Fenster.\n"
    ).encode("utf-8")
    res = client.post(f"/api/tm/tms/{tm_id}/import-csv", headers=pm,
                      files={"file": ("tm.csv", io.BytesIO(csv_data), "text/csv")})
    assert res.status_code == 200, res.text
    assert res.json() == {"added": 3, "updated": 1, "skipped": 1}, res.json()

    # upsert: дубль обновил перевод, а не создал вторую запись
    res = client.get(f"/api/tm/tms/{tm_id}/entries", headers=pm, params={"q": "Press the button"})
    entries = res.json()
    assert len(entries) == 1
    assert entries[0]["target_text"] == "Druecken Sie die Taste. (v2)"

    # неверный шаблон — 422
    res = client.post(f"/api/tm/tms/{tm_id}/import-csv", headers=pm,
                      files={"file": ("bad.csv", io.BytesIO(b"foo,bar\nx,y\n"), "text/csv")})
    assert res.status_code == 422

    # пагинация: X-Total-Count + offset/limit
    res = client.get(f"/api/tm/tms/{tm_id}/entries", headers=pm,
                     params={"limit": 2, "offset": 0})
    assert res.status_code == 200
    assert res.headers["X-Total-Count"] == "3"
    page1 = res.json()
    assert len(page1) == 2
    res = client.get(f"/api/tm/tms/{tm_id}/entries", headers=pm,
                     params={"limit": 2, "offset": 2})
    page2 = res.json()
    assert len(page2) == 1
    # страницы не пересекаются и покрывают всё
    ids = [e["id"] for e in page1 + page2]
    assert len(set(ids)) == 3
    all_entries = client.get(f"/api/tm/tms/{tm_id}/entries", headers=pm,
                             params={"limit": 100}).json()
    assert {e["id"] for e in all_entries} == set(ids)

    # поиск учитывается в X-Total-Count
    res = client.get(f"/api/tm/tms/{tm_id}/entries", headers=pm,
                     params={"q": "door", "limit": 2})
    assert res.headers["X-Total-Count"] == "1" and len(res.json()) == 1


def test_tb_entries_pagination(client, pm):
    res = client.post("/api/tb/tbs", headers=pm, json={
        "name": "Page TB", "source_lang": "en", "target_lang": "fr",
    })
    tb_id = res.json()["id"] if res.status_code == 201 else next(
        t["id"] for t in client.get("/api/tb/tbs", headers=pm).json() if t["name"] == "Page TB"
    )
    for i in range(5):
        client.post(f"/api/tb/tbs/{tb_id}/entries", headers=pm, json={
            "term": f"term {i}", "translation": f"terme {i}",
        })
    res = client.get(f"/api/tb/tbs/{tb_id}/entries", headers=pm, params={"limit": 3, "offset": 3})
    assert res.headers["X-Total-Count"] == "5"
    assert len(res.json()) == 2


def test_tmx_import_duplicate_source_in_one_file(client, pm):
    """Дубль source внутри одного TMX не должен ронять UNIQUE-ограничение."""
    res = client.post("/api/tm/tms", headers=pm, json={
        "name": "Dup TM", "source_lang": "en", "target_lang": "es",
    })
    tm_id = res.json()["id"] if res.status_code == 201 else next(
        t["id"] for t in client.get("/api/tm/tms", headers=pm).json() if t["name"] == "Dup TM"
    )
    tmx = (
        b'<?xml version="1.0" encoding="UTF-8"?>\n'
        b'<tmx version="1.4"><header creationtool="t" datatype="plaintext" '
        b'segtype="sentence" o-tmf="x" adminlang="en" srclang="en"/>'
        b"<body>"
        b'<tu><tuv xml:lang="en"><seg>Save the file.</seg></tuv>'
        b'<tuv xml:lang="es"><seg>Guarde el archivo.</seg></tuv></tu>'
        b'<tu><tuv xml:lang="en"><seg>Save the file.</seg></tuv>'
        b'<tuv xml:lang="es"><seg>Guarde el fichero. (v2)</seg></tuv></tu>'
        b"</body></tmx>"
    )
    res = client.post(f"/api/tm/tms/{tm_id}/import-tmx", headers=pm,
                      files={"file": ("dup.tmx", io.BytesIO(tmx), "application/x-tmx+xml")})
    assert res.status_code == 200, res.text
    assert res.json() == {"added": 1, "updated": 1, "skipped": 0}, res.json()
    entries = client.get(f"/api/tm/tms/{tm_id}/entries", headers=pm).json()
    assert len(entries) == 1
    assert entries[0]["target_text"] == "Guarde el fichero. (v2)"


def test_tm_export_csv_roundtrip_and_target_search(client, pm):
    res = client.post("/api/tm/tms", headers=pm, json={
        "name": "CSV Export TM", "source_lang": "en", "target_lang": "ru",
    })
    tm_id = res.json()["id"] if res.status_code == 201 else next(
        t["id"] for t in client.get("/api/tm/tms", headers=pm).json() if t["name"] == "CSV Export TM"
    )
    for src, tgt in (
        ("Save the file.", "Сохраните файл."),
        ('Close the "door".', 'Закройте "дверь".'),
        ("Multi\nline, entry", "Многострочная\nзапись, с запятой"),
    ):
        client.post(f"/api/tm/tms/{tm_id}/entries", headers=pm,
                    json={"source_text": src, "target_text": tgt})

    res = client.get(f"/api/tm/tms/{tm_id}/export-csv", headers=pm)
    assert res.status_code == 200, res.text
    assert res.headers["content-type"].startswith("text/csv")
    # BOM для Excel, как у TB-экспорта
    assert res.content.startswith(b"\xef\xbb\xbf")
    # RFC 5987 в имени файла
    assert "attachment" in res.headers.get("content-disposition", "")

    # round-trip: экспорт импортируется обратно без потерь (в новый TM)
    res2 = client.post("/api/tm/tms", headers=pm, json={
        "name": "CSV Roundtrip TM", "source_lang": "en", "target_lang": "ru",
    })
    tm2 = res2.json()["id"] if res2.status_code == 201 else next(
        t["id"] for t in client.get("/api/tm/tms", headers=pm).json() if t["name"] == "CSV Roundtrip TM"
    )
    imported = client.post(f"/api/tm/tms/{tm2}/import-csv", headers=pm,
                           files={"file": ("rt.csv", io.BytesIO(res.content), "text/csv")})
    assert imported.status_code == 200, imported.text
    assert imported.json()["added"] == 3, imported.json()
    entries2 = client.get(f"/api/tm/tms/{tm2}/entries", headers=pm).json()
    pairs = {(e["source_text"], e["target_text"]) for e in entries2}
    assert ("Multi\nline, entry", "Многострочная\nзапись, с запятой") in pairs  # запятые и \n пережили CSV
    assert ('Close the "door".', 'Закройте "дверь".') in pairs

    # поиск по ПЕРЕВОДУ (обе стороны) — паритет с TB
    res = client.get(f"/api/tm/tms/{tm_id}/entries", headers=pm, params={"q": "Сохраните"})
    assert res.headers["X-Total-Count"] == "1"
    assert res.json()[0]["source_text"] == "Save the file."
    # и по исходнику по-прежнему
    res = client.get(f"/api/tm/tms/{tm_id}/entries", headers=pm, params={"q": "save the"})
    assert res.headers["X-Total-Count"] == "1"
