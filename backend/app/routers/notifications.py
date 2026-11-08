from fastapi import APIRouter, Depends, HTTPException, status

from .. import models, schemas
from ..deps import Context, get_context

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


@router.get("", response_model=list[schemas.NotificationOut])
def list_notifications(limit: int = 50, ctx: Context = Depends(get_context)):
    return (
        ctx.db.query(models.Notification)
        .filter(models.Notification.org_id == ctx.org_id, models.Notification.recipient_id == ctx.user_id)
        .order_by(models.Notification.id.desc())
        .limit(min(limit, 200))
        .all()
    )


def _mine_or_404(ctx: Context, notification_id: int) -> models.Notification:
    notification = ctx.db.get(models.Notification, notification_id)
    if notification is None or notification.recipient_id != ctx.user_id or notification.org_id != ctx.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Уведомление не найдено")
    return notification


@router.post("/{notification_id}/read", response_model=schemas.NotificationOut)
def mark_read(notification_id: int, ctx: Context = Depends(get_context)):
    notification = _mine_or_404(ctx, notification_id)
    if notification.read_at is None:
        from ..helpers import utcnow

        notification.read_at = utcnow()
        ctx.db.commit()
        ctx.db.refresh(notification)
    return notification


@router.post("/read-all", status_code=status.HTTP_200_OK)
def mark_all_read(ctx: Context = Depends(get_context)):
    from ..helpers import utcnow

    unread = (
        ctx.db.query(models.Notification)
        .filter(
            models.Notification.org_id == ctx.org_id,
            models.Notification.recipient_id == ctx.user_id,
            models.Notification.read_at.is_(None),
        )
        .all()
    )
    for notification in unread:
        notification.read_at = utcnow()
    ctx.db.commit()
    return {"updated": len(unread)}
