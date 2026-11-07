"""Контроль качества перед сдачей (FR-09).

Блокирующие ошибки: отсутствие перевода у обязательного сегмента, неподтвержденные
обязательные сегменты, ошибки тегов. Предупреждения: расхождения чисел,
нарушение терминологии (в т.ч. запрещенные варианты из TB).
"""
import re

from sqlalchemy.orm import Session

from .. import models
from . import tags, words

_DIGITS_RE = re.compile(r"\d+")


def _numbers(text: str) -> list[str]:
    return sorted(_DIGITS_RE.findall(tags.strip_tags(text)))


def _check_numbers(segment: models.Segment) -> str | None:
    if not segment.target_text.strip():
        return None
    if _numbers(segment.source_text) != _numbers(segment.target_text):
        return "Numbers in the translation differ from the source"
    return None


def _check_terms(db: Session, job: models.Job, segment: models.Segment) -> list[tuple[str, str]]:
    """(сообщение, severity) по терминам из TB проекта для этой языковой пары."""
    issues: list[tuple[str, str]] = []
    if not segment.target_text.strip():
        return issues

    resources = (
        db.query(models.ProjectResource, models.TB)
        .join(models.TB, models.ProjectResource.tb_id == models.TB.id)
        .filter(
            models.ProjectResource.project_id == job.project_id,
            models.ProjectResource.use_for_search.is_(True),
            models.TB.source_lang == job.project.source_lang,
            models.TB.target_lang == job.target_lang,
        )
        .all()
    )
    target_lower = segment.target_text.casefold()
    source_lower = segment.source_text.casefold()
    for _, tb in resources:
        for entry in db.query(models.TBEntry).filter(models.TBEntry.tb_id == tb.id).all():
            term_lower = entry.term.casefold()
            if entry.status == "forbidden":
                if entry.translation.casefold() in target_lower:
                    issues.append((
                        f'Forbidden term variant "{entry.translation}" '
                        f'(term "{entry.term}", TB "{tb.name}")',
                        "warning",
                    ))
                continue
            # preferred/admitted: термин должен быть переведен разрешенным вариантом
            if term_lower in source_lower:
                allowed = (
                    db.query(models.TBEntry)
                    .filter(
                        models.TBEntry.tb_id == tb.id,
                        models.TBEntry.term == entry.term,
                        models.TBEntry.status.in_(("preferred", "admitted")),
                    )
                    .all()
                )
                if not any(a.translation.casefold() in target_lower for a in allowed):
                    issues.append((
                        f'Term "{entry.term}" (TB "{tb.name}") is not translated '
                        f'with an approved variant ("{entry.translation}")',
                        "warning",
                    ))
    return issues


def run_qa(db: Session, job: models.Job) -> dict:
    items: list[dict] = []
    for segment in job.segments:
        if not segment.is_mandatory:
            continue
        if not segment.target_text.strip():
            items.append({
                "segment_id": segment.id,
                "position": segment.position,
                "code": "missing_translation",
                "severity": "error",
                "message": "Mandatory segment has no translation",
            })
            continue
        if not segment.confirmed:
            items.append({
                "segment_id": segment.id,
                "position": segment.position,
                "code": "unconfirmed",
                "severity": "error",
                "message": "Mandatory segment is not confirmed",
            })
        for error in tags.validate_tags(segment.source_text, segment.target_text):
            items.append({
                "segment_id": segment.id,
                "position": segment.position,
                "code": "tag_error",
                "severity": "error",
                "message": error,
            })
        number_issue = _check_numbers(segment)
        if number_issue:
            items.append({
                "segment_id": segment.id,
                "position": segment.position,
                "code": "number_mismatch",
                "severity": "warning",
                "message": number_issue,
            })
        for message, severity in _check_terms(db, job, segment):
            items.append({
                "segment_id": segment.id,
                "position": segment.position,
                "code": "terminology",
                "severity": severity,
                "message": message,
            })

    blocking = [i for i in items if i["severity"] == "error"]
    return {
        "can_submit": not blocking,
        "blocking_count": len(blocking),
        "warning_count": len(items) - len(blocking),
        "items": items,
    }
