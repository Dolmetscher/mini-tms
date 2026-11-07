"""Уведомления (§8): запись в центр уведомлений + адресная доставка по WebSocket."""
from sqlalchemy.orm import Session

from .. import models
from ..ws import manager, make_event

PUSH_PROVIDER = None  # Точка подключения FCM/APNs в будущем (интерфейс-заглушка).


def notify(
    db: Session,
    org_id: int,
    recipient_id: int | None,
    type_: str,
    payload: dict,
) -> None:
    if recipient_id is None:
        return
    notification = models.Notification(
        org_id=org_id,
        recipient_id=recipient_id,
        type=type_,
        payload=payload,
    )
    db.add(notification)
    db.flush()
    manager.push(
        [recipient_id],
        make_event("notification", notification={
            "id": notification.id,
            "type": type_,
            "payload": payload,
            "created_at": notification.created_at.isoformat(),
        }),
    )
    if PUSH_PROVIDER is not None:  # pragma: no cover — расширение на будущее
        PUSH_PROVIDER.send(recipient_id, type_, payload)
