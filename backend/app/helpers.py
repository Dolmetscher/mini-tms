"""Общие проверки доступа и сериализация заданий."""
import re
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from . import models
from .deps import Context


def utcnow() -> datetime:
    """Наивный UTC: SQLite хранит datetime без tz, сравнения идут по UTC."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def content_disposition(filename: str) -> str:
    """Заголовок Content-Disposition для скачивания (RFC 5987).

    HTTP-заголовки кодируются в latin-1: кириллица и прочий не-ASCII падают с
    UnicodeEncodeError. Отдаем ASCII-fallback плюс filename*=UTF-8''...,
    который браузеры (и наш веб-клиент) предпочитают при наличии.
    """
    fallback = re.sub(r"[^\x20-\x7e]", "_", filename).replace('"', "")
    quoted = quote(filename)
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quoted}"


def naive_utc(dt: datetime) -> datetime:
    """SQLite возвращает наивные datetime; нормализуем все к наивному UTC."""
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def job_or_404(ctx: Context, job_id: int) -> models.Job:
    job = ctx.db.get(models.Job, job_id)
    if job is None or job.org_id != ctx.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Job not found")
    return job


def project_or_404(ctx: Context, project_id: int) -> models.Project:
    project = ctx.db.get(models.Project, project_id)
    if project is None or project.org_id != ctx.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


def can_manage(ctx: Context) -> bool:
    return ctx.role in ("admin", "pm")


def ensure_job_access(ctx: Context, job: models.Job) -> None:
    """Переводчик видит только свои задания; менеджмент — все задания организации."""
    if can_manage(ctx):
        return
    if job.assignee_id != ctx.user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Job is not assigned to you")


def ensure_project_access(ctx: Context, project: models.Project) -> None:
    """Для конкорданса/TB/MT: менеджмент или переводчик с заданием в проекте."""
    if can_manage(ctx):
        return
    has_job = (
        ctx.db.query(models.Job)
        .filter(models.Job.project_id == project.id, models.Job.assignee_id == ctx.user_id)
        .first()
    )
    if has_job is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail="No jobs in this project for you"
        )


def job_deadline(job: models.Job) -> datetime | None:
    return job.deadline or job.project.deadline


def serialize_job(db: Session, job: models.Job) -> dict:
    mandatory = [s for s in job.segments if s.is_mandatory]
    deadline = job_deadline(job)
    return {
        "id": job.id,
        "project_id": job.project_id,
        "project_name": job.project.name,
        "document_id": job.document_id,
        "document_name": job.document.name,
        "document_format": job.document.format,
        "source_lang": job.project.source_lang,
        "target_lang": job.target_lang,
        "status": job.status,
        "deadline": deadline,
        "project_deadline": job.project.deadline,
        "instructions": job.project.instructions,
        "is_overdue": bool(
            deadline
            and naive_utc(deadline) < utcnow()
            and job.status not in ("completed", "canceled")
        ),
        "assignee": (
            {"id": job.assignee.id, "username": job.assignee.username}
            if job.assignee
            else None
        ),
        # Прогресс по формуле ТЗ (FR-13): подтвержденные обязательные / все обязательные.
        "total_segments": len(mandatory),
        "confirmed_segments": sum(1 for s in mandatory if s.confirmed),
        "total_words": sum(s.source_words for s in mandatory),
        "updated_at": job.updated_at,
    }


def org_member_ids(db: Session, org_id: int) -> list[int]:
    return [m.user_id for m in db.query(models.Membership).filter(models.Membership.org_id == org_id)]


def org_managers(db: Session, org_id: int) -> list[int]:
    rows = (
        db.query(models.Membership)
        .filter(
            models.Membership.org_id == org_id,
            models.Membership.role.in_(("admin", "pm")),
        )
        .all()
    )
    return [m.user_id for m in rows]
