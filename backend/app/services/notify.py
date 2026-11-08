"""Уведомления (§8): центр уведомлений + WebSocket + внешние каналы (бета).

Внешние каналы — email (SMTP) и Telegram-бот — подключаются администратором
через env (TMS_SMTP_*, TMS_TG_BOT_TOKEN); контакты пользователи задают себе
сами (PATCH /api/me). Доставка best-effort в фоновом потоке: ошибка SMTP или
Telegram не ломает запрос, породивший уведомление (иначе падение внешнего
сервиса останавливало бы работу с заданиями).

Наружу уходят только «действенные» события (назначение, сдача, возврат,
дедлайн) — спамить почтой каждый сегмент никто не хотел бы.
"""
import logging
import smtplib
import threading
from email.mime.text import MIMEText

from sqlalchemy.orm import Session

from .. import config, models
from ..ws import manager, make_event

logger = logging.getLogger("mini-tms")

PUSH_PROVIDER = None  # Точка подключения FCM/APNs в будущем (интерфейс-заглушка).

# События, дублирующиеся во внешние каналы (email/Telegram).
EXTERNAL_TYPES = frozenset({
    "job_assigned", "job_accepted", "job_declined", "job_submitted",
    "job_returned", "job_reassigned", "job_canceled", "deadline_reminder",
})

_EVENT_TEXT = {
    "job_assigned": "New job assigned",
    "job_accepted": "Job accepted",
    "job_declined": "Job declined",
    "job_submitted": "Job submitted for review",
    "job_returned": "Job returned to rework",
    "job_reassigned": "Job reassigned to another translator",
    "job_canceled": "Job canceled",
    "deadline_reminder": "Deadline reminder",
}

# Поля payload, интересные человеку (порядок = порядок строк в письме).
_PAYLOAD_KEYS = ("document", "project", "target_lang", "deadline", "offset", "comment", "reason")


def _render(type_: str, payload: dict) -> tuple[str, str]:
    title = _EVENT_TEXT.get(type_, type_)
    lines = [f"Mini-TMS: {title}"]
    for key in _PAYLOAD_KEYS:
        if payload.get(key) is not None:
            lines.append(f"{key}: {payload[key]}")
    if payload.get("job_id") is not None:
        lines.append(f"job: #{payload['job_id']}")
    return lines[0], "\n".join(lines)


def _send_smtp(to_email: str, subject: str, body: str) -> None:
    """Один email через SMTP (STARTTLS по умолчанию). Вызывается в фоне."""
    message = MIMEText(body, "plain", "utf-8")
    message["Subject"] = subject
    message["From"] = config.SMTP_FROM
    message["To"] = to_email
    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=10) as smtp:
        if config.SMTP_STARTTLS and config.SMTP_USER:
            smtp.starttls()
        if config.SMTP_USER:
            smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
        smtp.send_message(message)


def _send_telegram(chat_id: str, text: str) -> None:
    """Сообщение ботом (api.telegram.org). Вызывается в фоне."""
    import httpx

    response = httpx.post(
        f"https://api.telegram.org/bot{config.TG_BOT_TOKEN}/sendMessage",
        json={"chat_id": chat_id, "text": text},
        timeout=10,
    )
    response.raise_for_status()


def _deliver(to_email: str | None, tg_chat_id: str | None, subject: str, body: str) -> None:
    """Фоновая доставка: каждый канал — независимо, падение одного не трогает другой."""
    if to_email and config.SMTP_HOST:
        try:
            _send_smtp(to_email, subject, body)
        except Exception:  # noqa: BLE001 — внешний канал не должен ломать основной поток
            logger.warning("SMTP delivery to %s failed", to_email, exc_info=True)
    if tg_chat_id and config.TG_BOT_TOKEN:
        try:
            _send_telegram(tg_chat_id, f"{subject}\n\n{body}")
        except Exception:  # noqa: BLE001
            logger.warning("Telegram delivery to %s failed", tg_chat_id, exc_info=True)


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

    # Внешние каналы (бета): контакты читаем сейчас (в сессии запроса),
    # сеть трогаем только в фоновом потоке.
    if type_ in EXTERNAL_TYPES:
        user = db.get(models.User, recipient_id)
        if user is None:
            return
        email = user.email if user.email and config.SMTP_HOST else None
        tg = user.telegram_chat_id if user.telegram_chat_id and config.TG_BOT_TOKEN else None
        if email or tg:
            subject, body = _render(type_, payload)
            threading.Thread(
                target=_deliver, args=(email, tg, subject, body), daemon=True,
            ).start()
