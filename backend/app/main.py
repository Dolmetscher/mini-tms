import asyncio
import os
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

import logging

import jwt
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from sqlalchemy import inspect, text

from . import config
from .auth import decode_token
from .database import Base, SessionLocal, engine
from .deps import ROLE_TRANSLATOR
from .helpers import naive_utc, utcnow
from .models import Job, JobReminder, Membership, User
from .routers import auth, billing, jobs, notifications, orgs, projects, qa_rules, reports, tb, tm
from .seed import seed_if_empty
from .services import notify
from .ws import manager

logger = logging.getLogger("mini-tms")


def _ensure_schema() -> None:
    """create_all не добавляет колонки к существующим таблицам (SQLite):
    мини-миграция pwd_ver (stateless-ревокация токенов при смене пароля)."""
    columns = {c["name"] for c in inspect(engine).get_columns("users")}
    if "pwd_ver" not in columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE users ADD COLUMN pwd_ver INTEGER NOT NULL DEFAULT 1"))


@asynccontextmanager
async def lifespan(_: FastAPI):
    if config.SECRET_KEY.startswith("dev-secret"):
        logger.warning(
            "TMS_SECRET_KEY is not set: using the built-in development key. "
            "Set TMS_SECRET_KEY before exposing the server to other users."
        )
    Base.metadata.create_all(bind=engine)
    _ensure_schema()
    with SessionLocal() as db:
        seed_if_empty(db)
    manager.set_loop(asyncio.get_running_loop())
    reminder_task = asyncio.create_task(_reminder_loop())
    yield
    reminder_task.cancel()


async def _reminder_loop() -> None:
    """Напоминания о дедлайне за 24ч и 2ч (§8). Дедлайны в прошлом не напоминают."""
    while True:
        try:
            await asyncio.to_thread(_check_deadlines)
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        await asyncio.sleep(config.REMINDER_CHECK_SECONDS)


def _check_deadlines() -> None:
    now = utcnow()
    with SessionLocal() as db:
        jobs = (
            db.query(Job)
            .filter(
                Job.deadline.isnot(None),
                Job.status.in_(("assigned", "accepted", "in_progress", "rework")),
            )
            .all()
        )
        for job in jobs:
            if job.assignee_id is None:
                continue
            deadline = naive_utc(job.deadline)
            for offset_hours in config.REMINDER_OFFSETS_HOURS:
                kind = f"{offset_hours}h"
                window_start, window_end = now, now + timedelta(hours=offset_hours)
                if not (window_start <= deadline <= window_end):
                    continue
                already = db.query(JobReminder).filter_by(job_id=job.id, kind=kind).first()
                if already is not None:
                    continue
                db.add(JobReminder(job_id=job.id, kind=kind))
                notify.notify(db, job.org_id, job.assignee_id, "deadline_reminder", {
                    "job_id": job.id,
                    "document": job.document.name,
                    "deadline": job.deadline.isoformat(),
                    "offset": kind,
                })
        db.commit()


app = FastAPI(title="Mini-TMS API", version="0.3.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition"],
)

app.include_router(auth.router)
app.include_router(orgs.router)
app.include_router(projects.router)
app.include_router(jobs.router)
app.include_router(tm.router)
app.include_router(tb.router)
app.include_router(billing.router)
app.include_router(qa_rules.router)
app.include_router(reports.router)
app.include_router(notifications.router)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.websocket("/ws/updates")
async def ws_updates(websocket: WebSocket, token: str = Query(), org_id: int = Query()):
    """Live-канал: события организации + персональные уведомления.

    Подключение валидируется: токен + членство в организации (переводчик
    получает только свои задания).
    """
    try:
        username, pwd_ver = decode_token(token)
    except jwt.PyJWTError:
        await websocket.close(code=1008)
        return

    with SessionLocal() as db:
        user = db.query(User).filter(User.username == username).first()
        if user is None or user.pwd_ver != pwd_ver:
            # Пароль сменили после выдачи токена — сессия отозвана.
            await websocket.close(code=1008)
            return
        membership = (
            db.query(Membership)
            .filter(Membership.org_id == org_id, Membership.user_id == user.id)
            .first()
        )
        if membership is None:
            await websocket.close(code=1008)
            return
        user_id = user.id

    await manager.connect(user_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(user_id, websocket)


# --- Веб-версия одним URL: FastAPI раздает собранный SPA (web/dist) ---
# Роутер регистрируется последним, поэтому /api и /ws имеют приоритет.
WEB_DIST = Path(
    os.environ.get("TMS_WEB_DIR", str(Path(__file__).resolve().parents[2] / "web" / "dist"))
)

if WEB_DIST.is_dir():
    _index = WEB_DIST / "index.html"
    _dist_root = str(WEB_DIST.resolve())

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        if full_path.startswith(("api/", "ws")):
            raise HTTPException(status_code=404, detail="Not found")
        candidate = (WEB_DIST / full_path).resolve()
        if full_path and candidate.is_file() and str(candidate).startswith(_dist_root):
            return FileResponse(candidate)
        return FileResponse(_index)
