"""LQA — оценка качества перевода (Language Quality Assessment).

Практика CAT/TMS и переводческих компаний: ревьюер размечает ошибки на
сегментах сданного задания по упрощенной типологии MQM (категория ×
серьёзность), каждому уровню соответствует штрафной балл, и считается
оценка на 1000 слов:

    score = max(0, 100 − Σ penalty × 1000 / words)

Такая нормализация (на 1000 слов) — стандарт LQA-отчетов (memoQ LQA,
TAUS DQF): оценка сравнима между заданиями разного объема. «Зачет» —
оценка не ниже порога (по умолчанию 95).

Категории (MQM top-level, сокращено):
- accuracy — точность передачи смысла (пропуски, добавления, искажения);
- terminology — несоблюдение терминологии;
- fluency — грамматика, орфография, синтаксис;
- style — стиль и регистр;
- locale — локальные конвенции (форматы дат/чисел, валюта);
- consistency — согласованность с TM/TB и внутри документа.
"""
from sqlalchemy.orm import Session

from .. import config, models

# Подписи категорий для отчетов (порядок фиксирован).
CATEGORY_LABELS = {
    "accuracy": "Accuracy (смысл)",
    "terminology": "Terminology (термины)",
    "fluency": "Fluency (грамотность)",
    "style": "Style (стиль)",
    "locale": "Locale (локализация)",
    "consistency": "Consistency (согласованность)",
}
SEVERITY_LABELS = {"minor": "Minor", "major": "Major", "critical": "Critical"}


def penalty_for(severity: str) -> float:
    return config.LQA_PENALTY.get(severity, 0.0)


def job_lqa_report(db: Session, job: models.Job) -> dict:
    """Отчет LQA по заданию: ошибки с позициями сегментов, штрафные баллы,
    итоговая оценка и «зачет/незачет».

    Слова — обязательные сегменты (та же база, что в прогрессе и анализе),
    чтобы оценка была нормализована на оплачиваемый объем.
    """
    errors = (
        db.query(models.LQAError)
        .filter(models.LQAError.job_id == job.id)
        .order_by(models.LQAError.created_at, models.LQAError.id)
        .all()
    )
    positions = {s.id: s.position for s in job.segments}
    words = sum(s.source_words for s in job.segments if s.is_mandatory)

    items = [
        {
            "id": error.id,
            "job_id": job.id,
            "segment_id": error.segment_id,
            "position": positions.get(error.segment_id, 0),
            "category": error.category,
            "severity": error.severity,
            "penalty": penalty_for(error.severity),
            "comment": error.comment,
            "author": {"id": error.author.id, "username": error.author.username},
            "created_at": error.created_at,
        }
        for error in errors
    ]
    total_penalty = sum(i["penalty"] for i in items)

    by_category: dict[str, dict[str, int]] = {}
    for item in items:
        bucket = by_category.setdefault(item["category"], {"count": 0, "penalty": 0})
        bucket["count"] += 1
        bucket["penalty"] += item["penalty"]

    if words > 0:
        score = max(0.0, 100.0 - total_penalty * 1000.0 / words)
    else:
        # Нулевой объем (нет обязательных сегментов) — без ошибок зачет,
        # с ошибками незачет: деления на ноль избегаем явно.
        score = 100.0 if not items else 0.0

    return {
        "job_id": job.id,
        "words": words,
        "errors": items,
        "error_count": len(items),
        "penalty_points": round(total_penalty, 2),
        "score": round(score, 1),
        "pass_score": config.LQA_PASS_SCORE,
        "passed": score >= config.LQA_PASS_SCORE,
        "by_category": [
            {
                "category": code,
                "label": CATEGORY_LABELS.get(code, code),
                "count": data["count"],
                "penalty": round(data["penalty"], 2),
            }
            for code, data in sorted(by_category.items())
        ],
    }
