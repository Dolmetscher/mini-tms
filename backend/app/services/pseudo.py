"""Псевдо-перевод (pseudolocalization) — классика подготовки локализации.

Заполняет сегменты синтетическим текстом: латинские буквы получают диакритику,
слова удлиняются на ~40%, весь сегмент берется в скобки-маркеры. При этом
инлайн-теги, числа и плейсхолдеры форматиров (%s, {0}, Qt %1) сохраняются
дословно.

Назначение — проверка пайплайна ДО перевода: экспорт/импорт, теги, QA
(числа и плейсхолдеры должны проходить), размещение длинных строк в макете.
Псевдо-текст очевидно «не перевод» — его невозможно сдать по ошибке: QA
пропустит числа/теги, но человек увидит [Ý...] и переведет заново.
"""
import re

from sqlalchemy.orm import Session

from .. import models

# Токенизация: теги достаются из потока текста и возвращаются как были.
_TAG_TOKEN_RE = re.compile(r'(<x id="[^"]+"\s*/>|<g id="[^"]+">|</g>)')
# Слова с цифрами/плейсхолдерами не искажаем: QA чисел и плейсхолдеров
# обязан проходить на псевдо-тексте — это часть проверяемого пайплайна.
_KEEP_WORD_RE = re.compile(r"[\d%{}]")

_PSEUDO_MAP = str.maketrans({
    "a": "á", "e": "é", "i": "í", "o": "ó", "u": "ú", "y": "ý",
    "A": "Á", "E": "É", "I": "Í", "O": "Ó", "U": "Ú", "Y": "Ý",
})
# Целевое удлиннение текста (~40%): имитация «длинных» языков (нем., русск.)
# для проверки верстки и обрезки строк.
_PAD_FACTOR = 0.4


def _pseudo_word(word: str) -> str:
    if not word or _KEEP_WORD_RE.search(word):
        return word
    base = word.translate(_PSEUDO_MAP)
    pad = max(1, round(len(word) * _PAD_FACTOR))
    return base + base[-1] * pad


def pseudo_text(text: str) -> str:
    """Псевдо-версия одного сегмента: теги и числа сохраняются, текст — акцент,
    скобки по краям — маркер «это псевдо, не перевод»."""
    chunks: list[str] = []
    for part in _TAG_TOKEN_RE.split(text):
        if not part:
            continue
        if _TAG_TOKEN_RE.fullmatch(part):
            chunks.append(part)  # тег — как был
            continue
        # Разбиваем с сохранением пробелов: структура исходника важна для QA.
        words = re.split(r"(\s+)", part)
        chunks.append("".join(_pseudo_word(w) for w in words))
    body = "".join(chunks)
    return f"[{body}]"


def pseudo_translate(db: Session, job: models.Job, user_id: int, overwrite: bool) -> dict:
    """Заполнить сегменты задания псевдо-переводом (черновики, без подтверждения).

    По умолчанию не трогает уже переведенные сегменты (консервативная политика
    пред-перевода); overwrite=True — перезаписать всё, для полного прогона
    экспорт-пайплайна.
    """
    filled = skipped_nonempty = 0
    for segment in job.segments:
        if segment.target_text.strip() and not overwrite:
            skipped_nonempty += 1
            continue
        text = pseudo_text(segment.source_text)
        if text != segment.target_text:
            db.add(models.SegmentRevision(
                segment_id=segment.id, author_id=user_id, target_text=text,
            ))
        segment.target_text = text
        segment.translation_status = "translated"
        segment.confirmed = False  # псевдо-текст подтверждать нельзя
        segment.updated_by = user_id
        filled += 1
    return {"filled": filled, "skipped_nonempty": skipped_nonempty}
