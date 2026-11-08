"""Леверидж TM: анализ объема, пред-перевод и автопропагация повторов.

Классические возможности CAT/TMS (Trados, memoQ), отсутствовавшие в MVP:

- **Анализ (analysis)** — раскладка объема задания по категориям совпадений
  с TM (exact / fuzzy-банды) и внутренним повторам, со взвешенными словами
  (weighted words). Основа котировки и планирования сроков: переводчик и
  менеджер видят, какая часть объема «уже переведена» в TM.
- **Пред-перевод (pre-translate)** — пакетное применение TM к непереведенным
  сегментам. Результат — черновик БЕЗ подтверждения: по переводческой
  практике даже 100% совпадение требует вычитки (контекст мог измениться),
  а QA-гейт требует подтверждения каждого сегмента.
- **Автопропагация** — при подтверждении сегмента перевод копируется в
  идентичные ПУСТЫЕ сегменты того же задания (непустые черновики не
  затираются — это осознанно консервативная политика).

Категории анализа и приоритет разложения одного сегмента:
TM exact > TM fuzzy-банд > повтор внутри задания > no match.
"""
from sqlalchemy.orm import Session

from .. import config, models
from ..fuzzy import length_bound_ratio, normalize, similarity_ratio

# (код, подпись, вес) — фиксированный порядок строк отчета.
_CATEGORIES: list[tuple[str, str, float]] = [
    ("exact", "Exact (100%)", config.ANALYSIS_EXACT_WEIGHT),
    ("fuzzy_95_99", "Fuzzy 95–99%", config.ANALYSIS_BANDS[0][2]),
    ("fuzzy_85_94", "Fuzzy 85–94%", config.ANALYSIS_BANDS[1][2]),
    ("fuzzy_75_84", "Fuzzy 75–84%", config.ANALYSIS_BANDS[2][2]),
    ("fuzzy_60_74", "Fuzzy 60–74%", config.ANALYSIS_BANDS[3][2]),
    ("repetition", "Repetitions", config.ANALYSIS_REPETITION_WEIGHT),
    ("no_match", "No match", 1.00),
]
_BAND_CODE = {
    f"fuzzy_{low}_{high}": (low, high) for low, high, _ in config.ANALYSIS_BANDS
}
# Публичные подписи категорий (общие для анализа, котировок и отчетов).
CATEGORY_LABELS: dict[str, str] = {code: label for code, label, _ in _CATEGORIES}


def _project_tm_entries(db: Session, job: models.Job) -> list[tuple[models.TMEntry, str]]:
    """Записи TM проекта (use_for_search) для языковой пары задания — 2 запроса."""
    rows = (
        db.query(models.ProjectResource, models.TM)
        .join(models.TM, models.ProjectResource.tm_id == models.TM.id)
        .filter(
            models.ProjectResource.project_id == job.project_id,
            models.ProjectResource.use_for_search.is_(True),
            models.TM.source_lang == job.project.source_lang,
            models.TM.target_lang == job.target_lang,
        )
        .all()
    )
    if not rows:
        return []
    entries = (
        db.query(models.TMEntry)
        .filter(models.TMEntry.tm_id.in_([tm.id for _, tm in rows]))
        .all()
    )
    names = {tm.id: tm.name for _, tm in rows}
    return [(entry, names[entry.tm_id]) for entry in entries]


def best_tm_match(source_text: str, tm_entries) -> tuple[int, str, str] | None:
    """Лучшее совпадение по TM: (процент, перевод, имя TM) или None.

    При равном счете выигрывает свежая по времени запись (updated_at):
    практика «последний перевод — самый правильный».
    """
    exact: tuple[models.TMEntry, str] | None = None
    best_fuzzy: tuple[int, models.TMEntry, str] | None = None
    wanted = normalize(source_text)
    for entry, tm_name in tm_entries:
        candidate = normalize(entry.source_text)
        if candidate == wanted:
            if exact is None or entry.updated_at > exact[0].updated_at:
                exact = (entry, tm_name)
            continue
        # Дешевый pre-filter по длинам: Левенштейн не нужен для явных промахов.
        if length_bound_ratio(source_text, entry.source_text) < config.FUZZY_MIN_SCORE:
            continue
        score = similarity_ratio(source_text, entry.source_text)
        if score < config.FUZZY_MIN_SCORE:
            continue
        if (
            best_fuzzy is None
            or score > best_fuzzy[0]
            or (score == best_fuzzy[0] and entry.updated_at > best_fuzzy[1].updated_at)
        ):
            best_fuzzy = (score, entry, tm_name)
    if exact is not None:
        return 100, exact[0].target_text, exact[1]
    if best_fuzzy is not None:
        return best_fuzzy[0], best_fuzzy[1].target_text, best_fuzzy[2]
    return None


def _bucket_code(match: tuple[int, str, str] | None) -> str | None:
    """Код категории по совпадению TM (без учета повторов)."""
    if match is None:
        return None
    score = match[0]
    if score == 100:
        return "exact"
    for code, (low, high) in _BAND_CODE.items():
        if low <= score <= high:
            return code
    return None


def analyze_job(db: Session, job: models.Job) -> dict:
    """Анализ объема задания (обязательные сегменты, как в прогрессе FR-13).

    Повтор = второе и далее вхождение того же нормализованного исходника внутри
    задания; при наличии совпадения TM приоритет у TM-категории.
    """
    tm_entries = _project_tm_entries(db, job)
    segments = [s for s in job.segments if s.is_mandatory]
    counts: dict[str, list[int]] = {code: [0, 0] for code, _, _ in _CATEGORIES}
    seen_sources: set[str] = set()

    for segment in segments:
        words = segment.source_words
        match = best_tm_match(segment.source_text, tm_entries) if tm_entries else None
        code = _bucket_code(match)
        if code is None:
            key = normalize(segment.source_text)
            code = "repetition" if key in seen_sources else "no_match"
            seen_sources.add(key)
        counts[code][0] += 1
        counts[code][1] += words

    categories = [
        {
            "code": code,
            "label": label,
            "segments": counts[code][0],
            "words": counts[code][1],
            "weighted": round(counts[code][1] * weight),
        }
        for code, label, weight in _CATEGORIES
    ]
    return {
        "job_id": job.id,
        "source_lang": job.project.source_lang,
        "target_lang": job.target_lang,
        "tm_names": sorted({name for _, name in tm_entries}),
        "segments": sum(c[0] for c in counts.values()),
        "words": sum(c[1] for c in counts.values()),
        "weighted_words": sum(c["weighted"] for c in categories),
        "categories": categories,
    }


def pre_translate(db: Session, job: models.Job, user_id: int, min_score: int = 100) -> dict:
    """Пред-перевод: заполняет ПУСТЫЕ сегменты из TM (score >= min_score),
    затем — повторами уже переведенных сегментов этого задания.

    min_score=100 — только точные совпадения (консервативный режим);
    можно опустить до fuzzy-порога (FUZZY_MIN_SCORE). Все результаты —
    черновики без подтверждения.
    """
    tm_entries = _project_tm_entries(db, job)
    filled_exact = filled_fuzzy = 0

    if tm_entries:
        for segment in job.segments:
            if segment.target_text.strip():
                continue
            match = best_tm_match(segment.source_text, tm_entries)
            if match is None or match[0] < min_score:
                continue
            segment.target_text = match[1]
            segment.translation_status = "translated"
            segment.updated_by = user_id
            db.add(models.SegmentRevision(
                segment_id=segment.id, author_id=user_id, target_text=match[1],
            ))
            if match[0] == 100:
                filled_exact += 1
            else:
                filled_fuzzy += 1

    # Повторы: пустые сегменты берут перевод уже заполненных «близнецов»
    # (в т.ч. только что заполненных из TM — один источник правды).
    translated: dict[str, str] = {}
    for segment in job.segments:
        if segment.target_text.strip():
            translated.setdefault(normalize(segment.source_text), segment.target_text)
    propagated = 0
    for segment in job.segments:
        if segment.target_text.strip():
            continue
        donor = translated.get(normalize(segment.source_text))
        if donor is not None:
            segment.target_text = donor
            segment.translation_status = "translated"
            segment.updated_by = user_id
            db.add(models.SegmentRevision(
                segment_id=segment.id, author_id=user_id, target_text=donor,
            ))
            propagated += 1

    mandatory_left = sum(
        1 for s in job.segments if s.is_mandatory and not s.target_text.strip()
    )
    return {
        "filled_exact": filled_exact,
        "filled_fuzzy": filled_fuzzy,
        "propagated": propagated,
        "skipped": mandatory_left,
    }


def propagate_translation(db: Session, job: models.Job, segment: models.Segment) -> list[models.Segment]:
    """Автопропагация подтвержденного перевода: идентичные ПУСТЫЕ сегменты
    того же задания получают тот же текст (статус translated, без подтверждения).

    Непустые черновики не затираются — переводчик мог осознанно написать иное.
    """
    key = normalize(segment.source_text)
    propagated: list[models.Segment] = []
    for other in job.segments:
        if other.id == segment.id or other.target_text.strip():
            continue
        if normalize(other.source_text) == key:
            other.target_text = segment.target_text
            other.translation_status = "translated"
            other.updated_by = segment.updated_by
            db.add(models.SegmentRevision(
                segment_id=other.id, author_id=segment.updated_by, target_text=segment.target_text,
            ))
            propagated.append(other)
    return propagated


def write_translation_to_tm(db: Session, job: models.Job, segment: models.Segment) -> None:
    """Подтвержденный перевод записывается в TM проекта с правом записи (FR-06/FR-12).

    Жила в роутере заданий; перенесена сюда — теперь и хендофф-импорт XLIFF
    пишет подтвержденные переводы в TM тем же правилом, что и ручное подтверждение.
    """
    resources = (
        db.query(models.ProjectResource, models.TM)
        .join(models.TM, models.ProjectResource.tm_id == models.TM.id)
        .filter(
            models.ProjectResource.project_id == job.project_id,
            models.ProjectResource.use_for_write.is_(True),
            models.TM.source_lang == job.project.source_lang,
            models.TM.target_lang == job.target_lang,
        )
        .all()
    )
    for _, tm in resources:
        entry = (
            db.query(models.TMEntry)
            .filter(models.TMEntry.tm_id == tm.id, models.TMEntry.source_text == segment.source_text)
            .first()
        )
        if entry is None:
            db.add(models.TMEntry(
                tm_id=tm.id, source_text=segment.source_text, target_text=segment.target_text,
            ))
        else:
            entry.target_text = segment.target_text
