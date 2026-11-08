"""Котировки: расчет стоимости перевода из анализа объема × прайс-листа.

Замыкает цепочку «анализ → деньги» (практика ТMS с биллингом — XTRF,
Plunet, котировки Trados/memoQ):

- Прайс-лист (RateCard) задает ставку за слово для каждой категории
  совпадений (exact / fuzzy-банды / повторы / no match) и минимальную
  сумму заказа.
- Котировка задания = Σ слова категории × ставка категории; скидка за
  совпадения зашита в ставку категории — так строятся котировки Trados
  (rate × matched words per band), а не «взвешенные слова».
- Минимальная сумма (min_charge) — стандарт индустрии: мелкий заказ
  оплачивается по минимуму, а не по 12 словам.
"""
from sqlalchemy.orm import Session

from .. import config, models
from ..services import leveraging
# Подписи категорий — те же, что в анализе (единый источник: leveraging).
_CATEGORY_LABELS = leveraging.CATEGORY_LABELS


def resolve_rate_card(
    db: Session, org_id: int, source_lang: str, target_lang: str, rate_card_id: int | None
) -> models.RateCard | None:
    """Прайс-лист для пары языков: явно указанный либо первый подходящий.

    Пара должна совпадать точно (en→ru ≠ ru→en) — как у TM/TB при подключении.
    """
    query = db.query(models.RateCard).filter(
        models.RateCard.org_id == org_id,
        models.RateCard.source_lang == source_lang,
        models.RateCard.target_lang == target_lang,
    )
    if rate_card_id is not None:
        return query.filter(models.RateCard.id == rate_card_id).first()
    return query.order_by(models.RateCard.id).first()


def job_quote(db: Session, job: models.Job, card: models.RateCard) -> dict:
    """Котировка одного задания: строки по категориям + итоги с min_charge."""
    analysis = leveraging.analyze_job(db, job)
    rates: dict[str, float] = {
        code: float(card.rates.get(code, config.RATE_DEFAULTS.get(code, 0.0)))
        for code in config.RATE_CATEGORIES
    }
    by_code = {c["code"]: c for c in analysis["categories"]}

    lines = []
    subtotal = 0.0
    for code in config.RATE_CATEGORIES:
        cat = by_code.get(code, {"segments": 0, "words": 0})
        rate = rates[code]
        amount = round(cat["words"] * rate, 2)
        subtotal += amount
        lines.append({
            "code": code,
            "label": _CATEGORY_LABELS.get(code, code),
            "segments": cat["segments"],
            "words": cat["words"],
            "rate": rate,
            "amount": amount,
        })
    subtotal = round(subtotal, 2)
    min_charge = float(card.min_charge or 0.0)
    min_applied = min_charge > subtotal > 0
    total = round(max(subtotal, min_charge) if subtotal > 0 else 0.0, 2)

    return {
        "job_id": job.id,
        "document_name": job.document.name,
        "target_lang": job.target_lang,
        "rate_card": card.name,
        "currency": card.currency,
        "lines": lines,
        "subtotal": subtotal,
        "min_charge": round(min_charge, 2),
        "min_charge_applied": min_applied,
        "total": total,
    }


def project_quote(db: Session, project: models.Project, rate_card_id: int | None) -> dict:
    """Котировка проекта: по прайс-листу на каждую языковую пару заданий.

    Итоги группируются по валюте: смешивать EUR и USD в одном числе нельзя.
    Задания без прайс-листа пропускаются (в отчете их нет) — вызывающая
    сторона сообщает пользователю, какие пары не покрыты.
    """
    jobs = (
        db.query(models.Job)
        .filter(models.Job.project_id == project.id)
        .order_by(models.Job.target_lang, models.Job.id)
        .all()
    )
    quoted, missing_pairs = [], []
    for job in jobs:
        card = resolve_rate_card(db, project.org_id, project.source_lang, job.target_lang, rate_card_id)
        if card is None:
            if f"{project.source_lang}\u2192{job.target_lang}" not in missing_pairs:
                missing_pairs.append(f"{project.source_lang}\u2192{job.target_lang}")
            continue
        quoted.append(job_quote(db, job, card))

    totals: dict[str, float] = {}
    for quote in quoted:
        totals[quote["currency"]] = round(totals.get(quote["currency"], 0.0) + quote["total"], 2)

    return {
        "project_id": project.id,
        "project_name": project.name,
        "source_lang": project.source_lang,
        "jobs": quoted,
        "totals": [{"currency": cur, "total": total} for cur, total in totals.items()],
        "missing_pairs": missing_pairs,
    }
