"""Биллинг: прайс-листы организации и котировки заданий/проектов.

Прайс-листы и котировки — данные менеджмента (pm/admin): переводчику
ставки не раскрываются. Котировка строится из анализа объема (те же
категории совпадений) × ставки прайс-листа; итог экспортируется в DOCX
для отправки заказчику.
"""
import os

from fastapi import APIRouter, Depends, HTTPException, Response, status

from .. import config, models, schemas
from ..deps import Context, get_context
from ..formats import docx_io
from ..helpers import content_disposition, job_or_404, project_or_404
from ..services import audit, quotes

router = APIRouter(prefix="/api", tags=["billing"])


def _card_or_404(ctx: Context, card_id: int) -> models.RateCard:
    card = ctx.db.get(models.RateCard, card_id)
    if card is None or card.org_id != ctx.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Rate card not found")
    return card


def _check_categories(rates: dict[str, float]) -> None:
    unknown = set(rates) - set(config.RATE_CATEGORIES)
    if unknown:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown rate categories: {', '.join(sorted(unknown))}",
        )


def _full_rates(rates: dict[str, float]) -> dict[str, float]:
    """Полный набор ставок: переданное + дефолты по недостающим категориям."""
    _check_categories(rates)
    return {code: float(rates.get(code, config.RATE_DEFAULTS[code])) for code in config.RATE_CATEGORIES}


@router.get("/rates", response_model=list[schemas.RateCardOut])
def list_rate_cards(ctx: Context = Depends(get_context)):
    ctx.require_role("admin", "pm")
    return (
        ctx.db.query(models.RateCard)
        .filter(models.RateCard.org_id == ctx.org_id)
        .order_by(models.RateCard.source_lang, models.RateCard.target_lang)
        .all()
    )


@router.post("/rates", response_model=schemas.RateCardOut, status_code=status.HTTP_201_CREATED)
def create_rate_card(body: schemas.RateCardCreate, ctx: Context = Depends(get_context)):
    ctx.require_role("admin", "pm")
    duplicate = (
        ctx.db.query(models.RateCard)
        .filter(
            models.RateCard.org_id == ctx.org_id,
            models.RateCard.source_lang == body.source_lang,
            models.RateCard.target_lang == body.target_lang,
            models.RateCard.currency == body.currency.upper(),
        )
        .first()
    )
    if duplicate is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Rate card for {body.source_lang}->{body.target_lang} in {body.currency.upper()} already exists",
        )
    card = models.RateCard(
        org_id=ctx.org_id,
        name=body.name,
        source_lang=body.source_lang,
        target_lang=body.target_lang,
        currency=body.currency.upper(),
        rates=_full_rates(body.rates),
        min_charge=body.min_charge,
    )
    ctx.db.add(card)
    ctx.db.flush()
    audit.audit(ctx.db, ctx.org_id, ctx.user_id, "ratecard.create", "rate_card", card.id, {
        "name": card.name, "pair": f"{card.source_lang}->{card.target_lang}", "currency": card.currency,
    })
    ctx.db.commit()
    ctx.db.refresh(card)
    return card


@router.patch("/rates/{card_id}", response_model=schemas.RateCardOut)
def update_rate_card(card_id: int, body: schemas.RateCardUpdate, ctx: Context = Depends(get_context)):
    """Правка прайс-листа: имя, валюта, ставки (частично), минимальная сумма."""
    ctx.require_role("admin", "pm")
    card = _card_or_404(ctx, card_id)
    if body.name is not None:
        card.name = body.name
    if body.currency is not None:
        card.currency = body.currency.upper()
    if body.rates is not None:
        # Частичная правка: переданные категории меняются, остальные сохраняются
        _check_categories(body.rates)
        merged = {code: float(v) for code, v in (card.rates or {}).items()}
        merged.update(body.rates)
        card.rates = merged
    if body.min_charge is not None:
        card.min_charge = body.min_charge
    audit.audit(ctx.db, ctx.org_id, ctx.user_id, "ratecard.update", "rate_card", card.id,
                body.model_dump(exclude_unset=True))
    ctx.db.commit()
    ctx.db.refresh(card)
    return card


@router.delete("/rates/{card_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rate_card(card_id: int, ctx: Context = Depends(get_context)):
    ctx.require_role("admin", "pm")
    card = _card_or_404(ctx, card_id)
    ctx.db.delete(card)
    audit.audit(ctx.db, ctx.org_id, ctx.user_id, "ratecard.delete", "rate_card", card_id, {
        "name": card.name,
    })
    ctx.db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _quote_card(ctx: Context, job: models.Job, rate_card_id: int | None) -> models.RateCard:
    project = job.project
    card = quotes.resolve_rate_card(ctx.db, ctx.org_id, project.source_lang, job.target_lang, rate_card_id)
    if card is None:
        pair = f"{project.source_lang}->{job.target_lang}"
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"No rate card for language pair {pair}: create one in /api/rates",
        )
    return card


@router.get("/jobs/{job_id}/quote", response_model=schemas.JobQuote)
def get_job_quote(
    job_id: int,
    rate_card_id: int | None = None,
    ctx: Context = Depends(get_context),
):
    """Котировка задания: анализ × ставки. Данные менеджмента (pm/admin)."""
    ctx.require_role("admin", "pm")
    job = job_or_404(ctx, job_id)
    card = _quote_card(ctx, job, rate_card_id)
    return schemas.JobQuote(**quotes.job_quote(ctx.db, job, card))


@router.get("/projects/{project_id}/quote", response_model=schemas.ProjectQuote)
def get_project_quote(
    project_id: int,
    rate_card_id: int | None = None,
    ctx: Context = Depends(get_context),
):
    """Котировка проекта по всем заданиям; итоги сгруппированы по валюте."""
    ctx.require_role("admin", "pm")
    project = project_or_404(ctx, project_id)
    quote = quotes.project_quote(ctx.db, project, rate_card_id)
    if not quote["jobs"] and not quote["missing_pairs"]:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="Project has no jobs to quote",
        )
    return schemas.ProjectQuote(**quote)


@router.get("/jobs/{job_id}/quote/export")
def export_job_quote(
    job_id: int,
    rate_card_id: int | None = None,
    ctx: Context = Depends(get_context),
):
    """Котировка задания в DOCX — документ для отправки заказчику."""
    ctx.require_role("admin", "pm")
    job = job_or_404(ctx, job_id)
    card = _quote_card(ctx, job, rate_card_id)
    quote = quotes.job_quote(ctx.db, job, card)
    audit.audit(ctx.db, ctx.org_id, ctx.user_id, "job.quote_export", "job", job.id, {
        "rate_card_id": card.id, "total": quote["total"], "currency": quote["currency"],
    })
    ctx.db.commit()
    payload = docx_io.render_quote(quote, job.project.source_lang)
    base, _ = os.path.splitext(job.document.name)
    filename = f"{base}_{job.target_lang}_quote.docx"
    return Response(
        content=payload,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": content_disposition(filename)},
    )


@router.get("/projects/{project_id}/invoice", response_model=schemas.ProjectInvoice)
def get_project_invoice(
    project_id: int,
    rate_card_id: int | None = None,
    ctx: Context = Depends(get_context),
):
    """Инвойс проекта: та же математика, что в котировке, но только сданные задания.

    Данные менеджмента (pm/admin). 409, если сданных заданий нет совсем;
    задания без прайс-листа отражаются в missing_pairs.
    """
    ctx.require_role("admin", "pm")
    project = project_or_404(ctx, project_id)
    has_submitted = (
        ctx.db.query(models.Job.id)
        .filter(
            models.Job.project_id == project.id,
            models.Job.status.in_(("submitted", "completed")),
        )
        .first()
        is not None
    )
    if not has_submitted:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="No submitted jobs to invoice yet",
        )
    return schemas.ProjectInvoice(**quotes.project_invoice(ctx.db, project, rate_card_id))


@router.get("/projects/{project_id}/invoice/export")
def export_project_invoice(
    project_id: int,
    rate_card_id: int | None = None,
    ctx: Context = Depends(get_context),
):
    """Инвойс в DOCX — закрывающий документ для заказчика (pm/admin)."""
    ctx.require_role("admin", "pm")
    project = project_or_404(ctx, project_id)
    invoice = quotes.project_invoice(ctx.db, project, rate_card_id)
    if not invoice["jobs"] and not invoice["missing_pairs"]:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="No submitted jobs to invoice yet",
        )
    if not invoice["jobs"]:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"No rate card for language pairs: {', '.join(invoice['missing_pairs'])}",
        )
    audit.audit(ctx.db, ctx.org_id, ctx.user_id, "project.invoice_export", "project", project.id, {
        "invoice_no": invoice["invoice_no"],
        "totals": invoice["totals"],
    })
    ctx.db.commit()
    payload = docx_io.render_invoice(invoice, project.source_lang)
    return Response(
        content=payload,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": content_disposition(f"{invoice['invoice_no']}.docx")},
    )
