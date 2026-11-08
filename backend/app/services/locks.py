"""Сегментные локи: кто редактирует — тот блокирует.

Оптимистичная блокировка (409 по base_updated_at) защищает текст от потери,
но конфликт обнаруживается уже после работы. Мягкий лок закрывает UX-дыру:
первый открывший сегмент держит лок с TTL, остальные видят владельца заранее
и не могут сохранить (423), пока лок свежий. Клиент продлевает свой лок
heartbeat-ом; вкладка, умершая без явного снятия, освобождает сегмент через
TTL — отдельный фоновый чистильщик не нужен.
"""
from datetime import timedelta

from sqlalchemy.orm import Session

from .. import config, models
from ..helpers import utcnow


def fresh_lock(db: Session, segment_id: int) -> models.SegmentLock | None:
    """Свежий (не просроченный по TTL) лок сегмента или None."""
    lock = (
        db.query(models.SegmentLock)
        .filter(models.SegmentLock.segment_id == segment_id)
        .first()
    )
    if lock is None:
        return None
    expires = lock.locked_at + timedelta(seconds=config.SEGMENT_LOCK_TTL_SECONDS)
    return lock if expires >= utcnow() else None


def acquire(
    db: Session, segment: models.Segment, user_id: int
) -> tuple[models.SegmentLock | None, models.SegmentLock | None]:
    """Захват или продление лока.

    Возвращает (мой_лок, чужой_лок): чужой свежий лок не пуст -> захват
    отвергнут; иначе лок создается/обновляется (в т.ч. перехват просроченного).
    """
    other = fresh_lock(db, segment.id)
    if other is not None and other.user_id != user_id:
        return None, other
    lock = (
        db.query(models.SegmentLock)
        .filter(models.SegmentLock.segment_id == segment.id)
        .first()
    )
    if lock is None:
        lock = models.SegmentLock(segment_id=segment.id, user_id=user_id, locked_at=utcnow())
        db.add(lock)
    else:
        lock.user_id = user_id
        lock.locked_at = utcnow()
    db.flush()
    return lock, None


def release(db: Session, segment_id: int, user_id: int | None = None) -> models.SegmentLock | None:
    """Снять лок сегмента (свой, либо любой при user_id=None — принудительно)."""
    query = db.query(models.SegmentLock).filter(models.SegmentLock.segment_id == segment_id)
    if user_id is not None:
        query = query.filter(models.SegmentLock.user_id == user_id)
    lock = query.first()
    if lock is not None:
        db.delete(lock)
        db.flush()
    return lock


def release_job_locks(db: Session, job: models.Job) -> int:
    """Снять все локи сегментов задания (смена статуса закрывает редактирование)."""
    ids = [s.id for s in job.segments]
    if not ids:
        return 0
    return (
        db.query(models.SegmentLock)
        .filter(models.SegmentLock.segment_id.in_(ids))
        .delete(synchronize_session=False)
    )
