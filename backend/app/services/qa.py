"""Контроль качества перед сдачей (FR-09).

Блокирующие ошибки: отсутствие перевода у обязательного сегмента, неподтвержденные
обязательные сегменты, ошибки тегов. Предупреждения: расхождения чисел,
нарушение терминологии (в т.ч. запрещенные варианты из TB), и дополнительные
проверки по переводческим практикам CAT-инструментов: пробелы, повторяющиеся
слова, баланс скобок, конечная пунктуация, совпадение с исходником,
плейсхолдеры форматов (%s, {0}, Qt %1) и аномальное соотношение длины.

Поверх встроенных проверок применяются RX-правила организации (QaRegexRule):
паттерн «должен / не должен встречаться» в исходнике или переводе — практика
регулярных проверок Trados/memoQ; severity=error делает нарушение блокирующим.

Все дополнительные проверки — только предупреждения: они не блокируют сдачу
(в соответствии с ТЗ блокируют только три вида ошибок выше).
"""
import re
from collections import Counter

from sqlalchemy.orm import Session

from .. import models
from . import tags

_DIGITS_RE = re.compile(r"\d+")
_DOUBLE_SPACE_RE = re.compile(r"(?<=\S) {2,}(?=\S)")
_REPEATED_WORD_RE = re.compile(r"(\S+)(\s+\1)(?=\s|$)", re.IGNORECASE)
_HAS_LETTER_RE = re.compile(r"\w", re.UNICODE)
# URL/email/пути часто законно совпадают с исходником — не шумим.
_URLISH_RE = re.compile(r"^(https?://|www\.)\S*$|^\S+@\S+$", re.IGNORECASE)
# Плейсхолдеры программных форматов: printf %s/%d/%f и %1$s, Qt %1, .NET {0}/{name}.
# Встречаются в UI-строках и документации ПО; потеря или искажение — критичная
# ошибка локализации, поэтому предупреждаем даже без ТЗ-требования.
_PLACEHOLDER_RE = re.compile(r"%(?:\d+\$)?[sdf]|%\d+|\{[^{}\s]+\}")
# Языки с письмом без пробелов: длина в символах несравнима с алфавитными парами.
_CJK_LANG_PREFIXES = ("zh", "ja", "ko")

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
        return "Числа в переводе не совпадают с исходником"
    return None


def _check_whitespace(segment: models.Segment) -> list[str]:
    issues: list[str] = []
    source, target = segment.source_text, segment.target_text
    if not target.strip():
        return issues
    if _DOUBLE_SPACE_RE.search(target):
        issues.append("Двойной пробел в переводе")
    # Согласованность ведущих/замыкающих пробелов с исходником.
    if target != target.lstrip() and source == source.lstrip():
        issues.append("Перевод начинается с пробела, исходник — нет")
    if target != target.rstrip() and source == source.rstrip():
        issues.append("Перевод заканчивается пробелом, исходник — нет")
    return issues


def _check_repeated_word(segment: models.Segment) -> list[str]:
    target = segment.target_text
    if not target.strip():
        return []
    plain = tags.strip_tags(target)
    return [f'Повтор слова «{match.group(1)}» в переводе'
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
                f'Непарная скобка «{closing}» в переводе '
                f"({target_opens} открывающих против {target_closes} закрывающих)"
            )
            continue
        source_opens = source.count(opening)
        if source_opens and source_opens != target_opens:
            issues.append(
                f'Число скобок «{opening}{closing}» отличается от исходника '
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
        return f'Перевод заканчивается на «{tgt_last}», исходник — на «{src_last}»'
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
        return "Перевод идентичен исходнику"
    return None


def _check_placeholders(segment: models.Segment) -> str | None:
    """Мультимножество плейсхолдеров перевода должно совпадать с исходником.

    Пример: «Error %s in file {0}» при переводе «Ошибка %d в файле {0}» —
    %s потерян, %d появился. Сравнение по множеству с кратностями (Counter),
    а не просто по вхождению каждого токена.
    """
    source = tags.strip_tags(segment.source_text)
    target = tags.strip_tags(segment.target_text)
    if not target.strip():
        return None
    src = Counter(_PLACEHOLDER_RE.findall(source))
    tgt = Counter(_PLACEHOLDER_RE.findall(target))
    if src == tgt:
        return None
    missing = sorted((src - tgt).elements())
    extra = sorted((tgt - src).elements())
    parts = []
    if missing:
        parts.append(f"нет: {' '.join(missing)}")
    if extra:
        parts.append(f"лишние: {' '.join(extra)}")
    return f"Плейсхолдеры не совпадают: {', '.join(parts)}"


def _check_length_ratio(
    segment: models.Segment, source_lang: str, target_lang: str
) -> str | None:
    """Аномальное соотношение длин (симптом неполного перевода или воды).

    Только для алфавитных пар (для CJK длина в символах несравнима) и
    исходников от 40 символов — на коротких сегментах проверка шумит.
    """
    if source_lang[:2].lower() in _CJK_LANG_PREFIXES or target_lang[:2].lower() in _CJK_LANG_PREFIXES:
        return None
    source = tags.strip_tags(segment.source_text).strip()
    target = tags.strip_tags(segment.target_text).strip()
    if not target or len(source) < 40:
        return None
    ratio = len(target) / len(source)
    if ratio > 2.0 or ratio < 0.5:
        return (
            f"Длина перевода {int(round(ratio * 100))}% от исходника "
            "(норма 80–120%) — проверьте пропуски и лишний текст"
        )
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
                        f'Запрещенный вариант «{entry.translation}» '
                        f'(термин «{entry.term}», TB «{tb_name}»)',
                        "warning",
                    ))
                continue
            # preferred/admitted: термин должен быть переведен разрешенным вариантом
            if term_lower in source_lower:
                allowed = allowed_by_term.get(entry.term, [])
                if not any(a.translation.casefold() in target_lower for a in allowed):
                    issues.append((
                        f'Термин «{entry.term}» (TB «{tb_name}») не переведен '
                        f'утвержденным вариантом («{entry.translation}»)',
                        "warning",
                    ))
    return issues


def _load_regex_rules(db: Session, org_id: int) -> list[models.QaRegexRule]:
    """Включенные RX-правила организации — одним запросом на весь QA-отчет."""
    return (
        db.query(models.QaRegexRule)
        .filter(models.QaRegexRule.org_id == org_id, models.QaRegexRule.enabled.is_(True))
        .order_by(models.QaRegexRule.id)
        .all()
    )


def _check_regex_rules(
    rules: list[models.QaRegexRule], segment: models.Segment
) -> list[tuple[str, str]]:
    """(сообщение, severity) по нарушенным RX-правилам организации.

    Текст проверяется без inline-тегов (как у встроенных проверок): тегами
    занимается отдельная блокирующая проверка tag_error. Правило с невалидным
    паттерном пропускается — валидность гарантируется при создании, здесь QA
    не должен падать из-за испорченной вручную записи.
    """
    issues: list[tuple[str, str]] = []
    for rule in rules:
        text = segment.source_text if rule.side == "source" else segment.target_text
        if not text.strip():
            continue
        flags = 0 if rule.case_sensitive else re.IGNORECASE
        try:
            pattern = re.compile(rule.pattern, flags)
        except re.error:
            continue
        plain = tags.strip_tags(text)
        matched = pattern.search(plain)
        # Нарушение: «должен совпадать», но не нашелся, либо «запрещен» и нашелся (XOR).
        violated = rule.must_match != bool(matched)
        if not violated:
            continue
        if matched:
            fragment = matched.group(0)[:60]
            message = (
                f'Правило «{rule.name}»: запрещенный паттерн найден в {rule.side}: «{fragment}»'
            )
        else:
            message = f'Правило «{rule.name}»: обязательный паттерн не найден в {rule.side}'
        issues.append((message, rule.severity))
    return issues


def run_qa(db: Session, job: models.Job) -> dict:
    items: list[dict] = []
    tb_data = _load_tb_terms(db, job)
    rx_rules = _load_regex_rules(db, job.org_id)
    for segment in job.segments:
        if not segment.is_mandatory:
            continue
        if not segment.target_text.strip():
            items.append({
                "segment_id": segment.id,
                "position": segment.position,
                "code": "missing_translation",
                "severity": "error",
                "message": "Обязательный сегмент без перевода",
            })
            continue
        if not segment.confirmed:
            items.append({
                "segment_id": segment.id,
                "position": segment.position,
                "code": "unconfirmed",
                "severity": "error",
                "message": "Обязательный сегмент не подтвержден",
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
        placeholder_issue = _check_placeholders(segment)
        if placeholder_issue:
            add("placeholder", placeholder_issue)
        length_issue = _check_length_ratio(segment, job.project.source_lang, job.target_lang)
        if length_issue:
            add("length_ratio", length_issue)
        for message, severity in _check_regex_rules(rx_rules, segment):
            items.append({
                "segment_id": segment.id,
                "position": segment.position,
                "code": "regex_rule",
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
