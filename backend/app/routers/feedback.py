"""Обратная связь из интерфейса (бета): кнопка «Feedback» на всех страницах.

Баги, идеи и вопросы уходят прямо из UI — вместе с адресом страницы, где
нажали кнопку, поэтому репродуцировать баг проще. Сообщения пишутся в БД
организации; ленту читает менеджмент (GET — последние сообщения, новыми
вперед). Анти-спам: не более FEEDBACK_MAX_PER_HOUR сообщений от одного
пользователя в час (скользящее окно по created_at).
"""
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status

from .. import config, models, schemas
from ..deps import Context, get_context
from ..helpers import naive_utc, utcnow

router = APIRouter(prefix="/api/feedback", tags=["feedback"])


@router.post("", response_model=schemas.FeedbackOut, status_code=status.HTTP_201_CREATED)
def create_feedback(body: schemas.FeedbackCreate, ctx: Context = Depends(get_context)):
    """Отправить сообщение обратной связи — любой участник организации."""
    hour_ago = naive_utc(utcnow() - timedelta(hours=1))
    recent = (
        ctx.db.query(models.Feedback)
        .filter(
            models.Feedback.user_id == ctx.user_id,
            models.Feedback.created_at >= hour_ago,
        )
        .count()
    )
    if recent >= config.FEEDBACK_MAX_PER_HOUR:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Слишком много сообщений, попробуйте позже",
        )
    fb = models.Feedback(
        org_id=ctx.org_id,
        user_id=ctx.user_id,
        kind=body.kind,
        message=body.message.strip(),
        page=body.page.strip(),
    )
    ctx.db.add(fb)
    ctx.db.commit()
    ctx.db.refresh(fb)
    return fb


@router.get("", response_model=list[schemas.FeedbackOut])
def list_feedback(
    limit: int = Query(default=100, ge=1, le=200),
    ctx: Context = Depends(get_context),
):
    """Лента обратной связи для менеджмента: последние сообщения организации."""
    ctx.require_role("admin", "pm")
    return (
        ctx.db.query(models.Feedback)
        .filter(models.Feedback.org_id == ctx.org_id)
        .order_by(models.Feedback.id.desc())
        .limit(limit)
        .all()
    )
