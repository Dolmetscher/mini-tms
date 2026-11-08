"""Отчеты для менеджмента: продуктивность переводчиков организации.

Практика ТMS с vendor management (XTRF, Plunet): PM видит по каждому
переводчику объем подтвержденных слов, активность по заданиям, просрочки
и среднюю LQA-оценку — данные для распределения заданий и ревью.
"""
from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from .. import models, schemas
from ..deps import Context, get_context
from ..helpers import job_deadline, naive_utc, utcnow
from ..services import lqa

router = APIRouter(prefix="/api/reports", tags=["reports"])


def _translator_row(db: Session, membership: models.Membership, jobs) -> schemas.ProductivityRow:
    completed = sum(1 for j in jobs if j.status == "completed")
    now = utcnow()
    overdue = sum(
        1
        for j in jobs
        if j.status not in ("completed", "canceled")
        and (deadline := job_deadline(j))
        and naive_utc(deadline) < now
    )
    words_confirmed = sum(
        s.source_words
        for j in jobs
        for s in j.segments
        if s.is_mandatory and s.confirmed
    )

    # LQA: средняя оценка по заданиям с размеченными ошибками
    # (без ошибок оценка тривиальна — 100 — и завысила бы среднее).
    scored = [r for r in (lqa.job_lqa_report(db, j) for j in jobs) if r["error_count"] > 0]
    avg_score = round(sum(r["score"] for r in scored) / len(scored), 1) if scored else None

    return schemas.ProductivityRow(
        user=schemas.UserRef(id=membership.user.id, username=membership.user.username),
        jobs_assigned=len(jobs),
        jobs_completed=completed,
        jobs_overdue=overdue,
        words_confirmed=words_confirmed,
        lqa_jobs=len(scored),
        avg_lqa_score=avg_score,
    )


@router.get("/productivity", response_model=list[schemas.ProductivityRow])
def productivity(
    updated_after: datetime | None = Query(default=None, alias="updated_after"),
    ctx: Context = Depends(get_context),
):
    """Продуктивность переводчиков организации (pm/admin).

    updated_after (ISO datetime, опционально) — учитывать только задания,
    обновлявшиеся с указанного момента (периодический отчет, срез периода).
    """
    ctx.require_role("admin", "pm")
    memberships = (
        ctx.db.query(models.Membership)
        .filter(models.Membership.org_id == ctx.org_id, models.Membership.role == "translator")
        .order_by(models.Membership.user_id)
        .all()
    )
    cutoff = naive_utc(updated_after) if updated_after is not None else None
    rows: list[schemas.ProductivityRow] = []
    for membership in memberships:
        query = ctx.db.query(models.Job).filter(
            models.Job.org_id == ctx.org_id, models.Job.assignee_id == membership.user_id
        )
        if cutoff is not None:
            query = query.filter(models.Job.updated_at >= cutoff)
        rows.append(_translator_row(ctx.db, membership, query.all()))
    return rows
