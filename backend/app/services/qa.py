"""Контроль качества перед сдачей (FR-09).

Блокирующие ошибки: отсутствие перевода у обязательного сегмента, неподтвержденные
обязательные сегменты, ошибки тегов. Предупреждения: расхождения чисел,
нарушение терминологии (в т.ч. запрещенные варианты из TB), и дополнительные
проверки по переводческим практикам CAT-инструментов: пробелы, повторяющиеся
слова, баланс скобок, конечная пунктуация, совпадение с исходником.

Все дополнительные проверки — только предупреждения: они не блокируют сдачу
(в соответствии с ТЗ блокируют только три вида ошибок выше).
"""
import re

from sqlalchemy.orm import Session

from .. import models
from . import tags

_DIGITS_RE = re.compile(r"\d+")
_DOUBLE_SPACE_RE = re.compile(r"(?<=\S) {2,}(?=\S)")
_REPEATED_WORD_RE = re.compile(r"(\S+)(\s+\1)(?=\s|$)", re.IGNORECASE)
_HAS_LETTER_RE = re.compile(r"\w", re.UNICODE)
# URL/email/пути часто законно совпадают с исходником — не шумим.
_URLISH_RE = re.compile(r"^(https?://|www\.)\S*$|^\S+@\S+$", re.IGNORECASE)

# Парные скобки и кавычки: открывающая -> закрывающая.
_BRACKETS = {"(": ")", "[": "]", "{": "}", "«": "»", "„": "“"}
# Символы, которые могут завершать предложение (вкл. CJK).
_TERMINALS = ".!?…。？！"
# Символы, которые допустимы в конце перевода помимо терминальных
# (двоеточие, запятая, закрывающая скобка/кавычка).
_TRAILING_OK = set(_TERMINALS) | {":", ";", ",", ")", "»", '"', "”", "'"}


def _numbers(text: str) -> list[str]:
    return sorted(_DIGITS_RE.findall(tags.strip_tags(text)))


def _check_numbers(segment: models.Segment) -> str | None:
    if not segment.target_text.strip():
        return None
    if _numbers(segment.source_text) != _numbers(segment.target_text):
        return "Numbers in the translation differ from the source"
    return None


def _check_whitespace(segment: models.Segment) -> list[str]:
    issues: list[str] = []
    source, target = segment.source_text, segment.target_text
    if not target.strip():
        return issues
    if _DOUBLE_SPACE_RE.search(target):
        issues.append("Double space in the translation")
    # Согласованность ведущих/замыкающих пробелов с исходником.
    if target != target.lstrip() and source == source.lstrip():
        issues.append("Translation starts with a space while the source does not")
    if target != target.rstrip() and source == source.rstrip():
        issues.append("Translation ends with a space while the source does not")
    return issues


def _check_repeated_word(segment: models.Segment) -> list[str]:
    target = segment.target_text
    if not target.strip():
        return []
    plain = tags.strip_tags(target)
    return [f'Repeated word "{match.group(1)}" in the translation'
            for match in _REPEATED_WORD_RE.finditer(plain)]


def _check_brackets(segment: models.Segment) -> list[str]:
    issues: list[str] = []
    source, target = tags.strip_tags(segment.source_text), tags.strip_tags(segment.target_text)
    if not target.strip():
        return issues
    for opening, closing in _BRACKETS.items():
        target_opens, target_closes = target.count(opening), target.count(closing)
        if target_opens != target_closes:
            issues.append(
                f'Unbalanced bracket "{closing}" in the translation '
                f"({target_opens} opening vs {target_closes} closing)"
            )
            continue
        source_opens = source.count(opening)
        if source_opens and source_opens != target_opens:
            issues.append(
                f'Bracket "{opening}{closing}" count differs from the source '
                f"({source_opens} -> {target_opens})"
            )
    return issues


def _check_final_punctuation(segment: models.Segment) -> str | None:
    source, target = tags.strip_tags(segment.source_text), tags.strip_tags(segment.target_text)
    if not target.strip():
        return None
    src_last = source.rstrip()[-1:] if source.strip() else ""
    tgt_last = target.rstrip()[-1:] if target.strip() else ""
    if src_last and src_last in _TERMINALS and tgt_last and tgt_last not in _TRAILING_OK:
        return f'Translation ends with "{tgt_last}" while the source ends with "{src_last}"'
    return None


def _check_identical(segment: models.Segment, source_lang: str, target_lang: str) -> str | None:
    """Перевод совпадает с исходником при разных языках — частый признак пропуска."""
    if source_lang.lower() == target_lang.lower():
        return None
    source = tags.strip_tags(segment.source_text).strip()
    target = tags.strip_tags(segment.target_text).strip()
    if not target:
        return None
    if not _HAS_LETTER_RE.search(source):
        return None  # числа/теги: совпадение допустимо
    if _URLISH_RE.match(source):
        return None  # URL/email обычно не переводятся
    if source.casefold() == target.casefold():
        return "Translation is identical to the source"
    return None


def _load_tb_terms(db: Session, job: models.Job) -> list[tuple[str, list[models.TBEntry]]]:
    """Все записи TB проекта для языковой пары задания — за 2 запроса, а не N+1."""
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
    if not resources:
        return []
    entries = (
        db.query(models.TBEntry)
        .filter(models.TBEntry.tb_id.in_([tb.id for _, tb in resources]))
        .all()
    )
    by_tb: dict[int, list[models.TBEntry]] = {}
    for entry in entries:
        by_tb.setdefault(entry.tb_id, []).append(entry)
    return [(tb.name, by_tb.get(tb.id, [])) for _, tb in resources]


def _check_terms(
    tb_data: list[tuple[str, list[models.TBEntry]]],
    segment: models.Segment,
) -> list[tuple[str, str]]:
    """(сообщение, severity) по терминам из TB проекта для этой языковой пары."""
    issues: list[tuple[str, str]] = []
    if not segment.target_text.strip():
        return issues

    target_lower = segment.target_text.casefold()
    source_lower = segment.source_text.casefold()
    for tb_name, entries in tb_data:
        # Варианты статуса preferred/admitted для одного термина собираем заранее:
        # проверка «термин переведен разрешенным вариантом» смотрит на все варианты.
        allowed_by_term: dict[str, list[models.TBEntry]] = {}
        for entry in entries:
            if entry.status in ("preferred", "admitted"):
                allowed_by_term.setdefault(entry.term, []).append(entry)

        for entry in entries:
            term_lower = entry.term.casefold()
            if entry.status == "forbidden":
                if entry.translation.casefold() in target_lower:
                    issues.append((
                        f'Forbidden term variant "{entry.translation}" '
                        f'(term "{entry.term}", TB "{tb_name}")',
                        "warning",
                    ))
                continue
            # preferred/admitted: термин должен быть переведен разрешенным вариантом
            if term_lower in source_lower:
                allowed = allowed_by_term.get(entry.term, [])
                if not any(a.translation.casefold() in target_lower for a in allowed):
                    issues.append((
                        f'Term "{entry.term}" (TB "{tb_name}") is not translated '
                        f'with an approved variant ("{entry.translation}")',
                        "warning",
                    ))
    return issues


def run_qa(db: Session, job: models.Job) -> dict:
    items: list[dict] = []
    tb_data = _load_tb_terms(db, job)
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

        def add(code: str, message: str) -> None:
            items.append({
                "segment_id": segment.id,
                "position": segment.position,
                "code": code,
                "severity": "warning",
                "message": message,
            })

        number_issue = _check_numbers(segment)
        if number_issue:
            add("number_mismatch", number_issue)
        for message, severity in _check_terms(tb_data, segment):
            items.append({
                "segment_id": segment.id,
                "position": segment.position,
                "code": "terminology",
                "severity": severity,
                "message": message,
            })
        for message in _check_whitespace(segment):
            add("whitespace", message)
        for message in _check_repeated_word(segment):
            add("repeated_word", message)
        for message in _check_brackets(segment):
            add("brackets", message)
        punctuation_issue = _check_final_punctuation(segment)
        if punctuation_issue:
            add("final_punctuation", punctuation_issue)
        identical_issue = _check_identical(segment, job.project.source_lang, job.target_lang)
        if identical_issue:
            add("identical_translation", identical_issue)

    blocking = [i for i in items if i["severity"] == "error"]
    return {
        "can_submit": not blocking,
        "blocking_count": len(blocking),
        "warning_count": len(items) - len(blocking),
        "items": items,
    }
