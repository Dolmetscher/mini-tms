#!/usr/bin/env python3
"""Смоук-тест запущенного сервера Mini-TMS (venv, docker run или VPS).

Проверяет healthcheck, раздачу SPA с русской локалью и сквозной сценарий
жизненного цикла: логин → организация → проект → XLIFF-импорт → задание →
назначение переводчика → правка сегмента. Только urllib, без зависимостей.

Запуск:  TMS_SMOKE_URL=http://127.0.0.1:8080 python scripts/smoke.py
"""
import json
import os
import re
import sys
import urllib.request

BASE = os.environ.get("TMS_SMOKE_URL", "http://127.0.0.1:8000").rstrip("/")
OK, FAIL = "\x1b[32mOK\x1b[0m", "\x1b[31mFAIL\x1b[0m"
results: list[tuple[str, bool, str]] = []


def req(method: str, path: str, body=None, token: str | None = None, raw: bool = False,
        org: str | None = None):
    r = urllib.request.Request(BASE + path, method=method)
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    if org:
        r.add_header("X-Org-Id", org)
    data = None
    if body is not None:
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, data=data, timeout=15) as resp:
            payload = resp.read()
            return resp.status, payload if raw else (json.loads(payload) if payload else {})
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:300]


def check(name: str, cond: bool, detail: str = ""):
    results.append((name, cond, detail))
    print(f"[{OK if cond else FAIL}] {name}" + (f" — {detail}" if detail and not cond else ""))


# 1. Healthcheck — та же команда, что в HEALTHCHECK Dockerfile
try:
    with urllib.request.urlopen(BASE + "/api/health", timeout=4) as r:
        check("HEALTHCHECK /api/health", r.status == 200, f"status={r.status}")
        print(f"       payload: {r.read().decode()[:120]}")
except Exception as e:
    check("HEALTHCHECK /api/health", False, str(e))

# 2. SPA раздается с корня
st, body = req("GET", "/", raw=True)
index = body.decode("utf-8", "replace")
check("GET / (index.html)", st == 200 and 'id="root"' in index, f"status={st}")
m = re.search(r'src="(/assets/[^"]+\.js)"', index)
check("в index.html есть ссылка на JS-бандл", bool(m), index[:200])

# 3. Бандл собран и содержит русскую локаль
if m:
    st, js = req("GET", m.group(1), raw=True)
    bundle = js.decode("utf-8", "replace")
    check("GET JS-бандл", st == 200 and len(bundle) > 10000, f"status={st}, {len(bundle)} байт")
    for s in ("Войти", "Проекты", "Не удалось войти"):
        check(f"ру-локаль в бандле: «{s}»", s in bundle)

# 4. Логин seeded-админом
st, resp = req("POST", "/api/login", {"username": "admin", "password": "admin123"})
token = resp.get("access_token") if isinstance(resp, dict) else None
check("POST /api/login (admin)", st == 200 and bool(token), f"status={st}")
org_id = None
org_role = None
if isinstance(resp, dict):
    ms = resp.get("memberships") or []
    org_id = ms[0].get("org_id") if ms else None
    org_role = ms[0].get("role") if ms else None
    check("membership содержит организацию", bool(org_id), str(ms)[:120])
    check("роль админа (в membership)", org_role in ("admin", "owner", "root"), str(org_role))

# 5. Профиль (TokenResponse: user вложен)
if token:
    st, me = req("GET", "/api/me", token=token)
    user = me.get("user", {}) if isinstance(me, dict) else {}
    check("GET /api/me", st == 200 and user.get("username") == "admin", f"status={st}")

    # 6. Создание проекта (требует X-Org-Id)
    st, proj = req("POST", "/api/projects", {
        "name": "Docker smoke test", "source_lang": "en",
        "target_langs": ["ru"], "instructions": "проверка образа",
    }, token=token, org=org_id)
    pid = proj.get("id") if isinstance(proj, dict) else None
    check("POST /api/projects", st in (200, 201) and pid, f"status={st}: {str(proj)[:120]}")

    # 7. Загрузка XLIFF-документа (сквозной импорт — как в реальном цикле перевода)
    xlf = (b'<?xml version="1.0" encoding="utf-8"?>'
           b'<xliff version="1.2" xmlns="urn:oasis:names:tc:xliff:document:1.2">'
           b'<file source-language="en" target-language="ru" datatype="plaintext" original="smoke">'
           b'<body>'
           b'<trans-unit id="1"><source>Hello world</source></trans-unit>'
           b'<trans-unit id="2"><source>Second segment</source></trans-unit>'
           b'</body></file></xliff>')
    if pid:
        boundary = "----tmsboundary8f2c9"
        parts = []
        parts.append(f"--{boundary}\r\n"
                     'Content-Disposition: form-data; name="target_lang"\r\n\r\nru\r\n'.encode())
        parts.append(f"--{boundary}\r\n"
                     'Content-Disposition: form-data; name="file"; filename="smoke.xlf"\r\n'
                     "Content-Type: application/x-xliff+xml\r\n\r\n".encode() + xlf + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        payload = b"".join(parts)
        r = urllib.request.Request(BASE + f"/api/projects/{pid}/documents",
                                   data=payload, method="POST")
        r.add_header("Authorization", f"Bearer {token}")
        if org_id:
            r.add_header("X-Org-Id", str(org_id))
        r.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        try:
            with urllib.request.urlopen(r, timeout=15) as resp:
                doc = json.loads(resp.read())
            check("POST документа (XLIFF-импорт)", resp.status in (200, 201) and doc.get("id"),
                  f"status={resp.status}")
        except urllib.error.HTTPError as e:
            check("POST документа (XLIFF-импорт)", False, f"{e.code}: {e.read()[:200]}")
            doc = {}
        did = doc.get("id")

        # 8. Задание и сегменты (в модели TMS: документ → job, сегменты в JobDetail)
        if did:
            st, jobs = req("GET", f"/api/jobs?project_id={pid}", token=token, org=org_id)
            jid = jobs[0]["id"] if isinstance(jobs, list) and jobs else None
            check("импорт создал задание (job)", st == 200 and bool(jid), f"status={st}")

            # 8a. Создаем переводчика (как в реальном цикле: admin -> участник-переводчик)
            st, member = req("POST", f"/api/orgs/{org_id}/members", {
                "username": "smoke_translator", "password": "smoke123",
                "role": "translator",
            }, token=token, org=org_id)
            tuid = (member or {}).get("user", {}).get("id") if isinstance(member, dict) else None
            check("POST участника-переводчика", st in (200, 201) and tuid,
                  f"status={st}: {str(member)[:120]}")

            # 8b. Назначение и принятие задания
            st, _ = req("POST", f"/api/jobs/{jid}/assign", {"assignee_id": tuid},
                        token=token, org=org_id)
            check("POST /api/jobs/{id}/assign", st in (200, 201), f"status={st}")
            st, tr_login = req("POST", "/api/login",
                               {"username": "smoke_translator", "password": "smoke123"})
            ttoken = tr_login.get("access_token") if isinstance(tr_login, dict) else None
            check("логин переводчика", st == 200 and bool(ttoken), f"status={st}")
            st, _ = req("POST", f"/api/jobs/{jid}/accept", {}, token=ttoken, org=org_id)
            check("POST /api/jobs/{id}/accept", st in (200, 201), f"status={st}")

            # 9. Редактирование сегмента переводчиком
            if jid and ttoken:
                st, job = req("GET", f"/api/jobs/{jid}", token=ttoken, org=org_id)
                segs = job.get("segments", []) if isinstance(job, dict) else []
                check("сегменты импортированы (2 шт.)", st == 200 and len(segs) == 2,
                      f"status={st}, n={len(segs)}")
                if segs:
                    sid = segs[0]["id"]
                    st, _ = req("PATCH", f"/api/segments/{sid}",
                                {"target_text": "Привет, мир!"}, token=ttoken, org=org_id)
                    check("PATCH сегмента (в работу)", st in (200, 201), f"status={st}")

# 10. Статика и SPA-фолбэк для клиентских маршрутов (deep link)
st, _ = req("GET", "/projects", raw=True)
check("SPA-фолбэк /projects → index.html", st == 200)

# Итог
failed = [r for r in results if not r[1]]
print(f"\n{'=' * 60}")
print(f"Итого: {len(results) - len(failed)}/{len(results)} проверок пройдено")
if failed:
    print("Провалены:")
    for name, _, d in failed:
        print(f"  - {name}: {d}")
sys.exit(1 if failed else 0)
