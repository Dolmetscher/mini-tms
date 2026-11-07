import os
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from .. import models, schemas
from ..deps import Context, get_context
from ..formats import segmentation
from ..helpers import (
    can_manage,
    ensure_job_access,
    job_deadline,
    job_or_404,
    naive_utc,
    org_member_ids,
    serialize_job,
)
from ..services import audit, notify, qa
from ..services.mt import MTError, get_provider
from ..ws import make_event, manager

router = APIRouter(prefix="/api", tags=["jobs"])

EDITABLE_STATUSES = ("accepted", "in_progress", "rework")


@router.get("/jobs", response_model=list[schemas.JobOut])
def list_jobs(
    status_filter: str | None = Query(default=None, alias="status"),
    project_id: int | None = None,
    mine: bool = False,
    q: str | None = None,
    sort: str = "deadline",
    ctx: Context = Depends(get_context),
):
    """Список заданий (FR-02). Переводчик видит только назначенные ему."""
    query = ctx.db.query(models.Job).filter(models.Job.org_id == ctx.org_id)
    if not can_manage(ctx) or mine:
        query = query.filter(models.Job.assignee_id == ctx.user_id)
    if status_filter:
        query = query.filter(models.Job.status == status_filter)
    if project_id:
        query = query.filter(models.Job.project_id == project_id)
    jobs = query.all()
    if q:
        needle = q.casefold()
        jobs = [
            job for job in jobs
            if needle in job.document.name.casefold() or needle in job.project.name.casefold()
        ]
    if sort == "deadline":
        def deadline_key(job: models.Job) -> datetime:
            # Сравниваем наивные UTC datetime напрямую: datetime.timestamp()
            # интерпретирует наивное время как локальное (ошибка на границе DST)
            # и падает с OSError на Windows для дат до 1970 года.
            deadline = job_deadline(job)
            return naive_utc(deadline) if deadline else datetime.max

        jobs.sort(key=deadline_key)
    return [serialize_job(ctx.db, job) for job in jobs]


@router.get("/jobs/{job_id}", response_model=schemas.JobDetail)
def get_job(job_id: int, ctx: Context = Depends(get_context)):
    job = job_or_404(ctx, job_id)
    ensure_job_access(ctx, job)
    return schemas.JobDetail(
        **serialize_job(ctx.db, job),
        rework_comment=job.rework_comment,
        decline_reason=job.decline_reason,
        segments=[schemas.SegmentOut.model_validate(s) for s in job.segments],
    )


def _write_translations_to_tm(db: Session, job: models.Job, segment: models.Segment) -> None:
    """Подтвержденный перевод записывается в TM проекта с правом записи (FR-06/FR-12)."""
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


@router.patch("/segments/{segment_id}", response_model=schemas.SegmentOut)
async def save_segment(segment_id: int, body: schemas.SegmentUpdate, ctx: Context = Depends(get_context)):
    """Сохранение сегмента: только исполнителю, только в редактируемых статусах (FR-03/FR-04)."""
    segment = ctx.db.get(models.Segment, segment_id)
    if segment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Segment not found")
    job = segment.job
    if job.org_id != ctx.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Segment not found")
    if not can_manage(ctx) and job.assignee_id != ctx.user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Job is not assigned to you")
    if job.status not in EDITABLE_STATUSES:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Job status '{job.status}' does not allow editing",
        )

    # Оптимистичная блокировка: конфликт версий не решается скрытой перезаписью (FR-04).
    if body.base_updated_at is not None:
        base = naive_utc(body.base_updated_at)
        if base != segment.updated_at:
            return Response(
                status_code=status.HTTP_409_CONFLICT,
                content=schemas.SegmentOut.model_validate(segment).model_dump_json(),
                media_type="application/json",
            )

    if body.target_text is not None:
        if segment.confirmed and body.target_text != segment.target_text:
            segment.confirmed = False  # редактирование подтвержденного снимает подтверждение
        segment.target_text = body.target_text
        segment.translation_status = "translated" if body.target_text.strip() else "drafted"
    if body.confirmed is not None:
        if body.confirmed and not segment.target_text.strip():
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Cannot confirm an empty segment")
        segment.confirmed = body.confirmed
    segment.updated_by = ctx.user_id

    if job.status == "accepted":
        job.status = "in_progress"  # принято -> в работе при первой правке
    if segment.confirmed and segment.translation_status == "translated":
        _write_translations_to_tm(ctx.db, job, segment)

    ctx.db.commit()
    ctx.db.refresh(segment)

    await manager.send_users(
        org_member_ids(ctx.db, ctx.org_id),
        make_event(
            "segment_updated",
            job_id=job.id,
            segment_id=segment.id,
            position=segment.position,
            translation_status=segment.translation_status,
            confirmed=segment.confirmed,
            target_text=segment.target_text,
            job_status=job.status,
            user=ctx.user.username,
        ),
    )
    return segment


@router.post("/jobs/{job_id}/assign", response_model=schemas.JobOut)
def assign_job(job_id: int, body: schemas.AssignRequest, ctx: Context = Depends(get_context)):
    ctx.require_role("admin", "pm")
    job = job_or_404(ctx, job_id)
    if job.status not in ("unassigned", "assigned", "declined"):
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"Cannot assign a job in status '{job.status}'")
    membership = (
        ctx.db.query(models.Membership)
        .filter(models.Membership.org_id == ctx.org_id, models.Membership.user_id == body.assignee_id)
        .first()
    )
    if membership is None or membership.role != "translator":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Assignee must be a translator of this organization")

    previous = job.assignee_id
    job.assignee_id = body.assignee_id
    if body.deadline is not None:
        # нормализуем к наивному UTC (клиент может прислать дедлайн с офсетом)
        job.deadline = naive_utc(body.deadline)  # индивидуальный дедлайн (FR-11)
    job.status = "assigned"
    audit.audit(ctx.db, ctx.org_id, ctx.user_id, "job.assign", "job", job.id, {"assignee_id": body.assignee_id})
    notify.notify(ctx.db, ctx.org_id, body.assignee_id, "job_assigned", {
        "job_id": job.id, "document": job.document.name, "project": job.project.name,
        "target_lang": job.target_lang,
    })
    if previous is not None and previous != body.assignee_id:
        # Переназначение: перевод сохраняется, доступ предыдущего исполнителя отзывается.
        notify.notify(ctx.db, ctx.org_id, previous, "job_reassigned", {"job_id": job.id})
    ctx.db.commit()
    return serialize_job(ctx.db, job)


@router.post("/jobs/{job_id}/accept", response_model=schemas.JobOut)
def accept_job(job_id: int, ctx: Context = Depends(get_context)):
    job = job_or_404(ctx, job_id)
    if job.assignee_id != ctx.user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Job is not assigned to you")
    if job.status != "assigned":
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"Cannot accept a job in status '{job.status}'")
    job.status = "accepted"
    audit.audit(ctx.db, ctx.org_id, ctx.user_id, "job.accept", "job", job.id, {})
    notify.notify(ctx.db, ctx.org_id, job.project.manager_id, "job_accepted", {"job_id": job.id})
    ctx.db.commit()
    return serialize_job(ctx.db, job)


@router.post("/jobs/{job_id}/decline", response_model=schemas.JobOut)
def decline_job(job_id: int, body: schemas.DeclineRequest, ctx: Context = Depends(get_context)):
    job = job_or_404(ctx, job_id)
    if job.assignee_id != ctx.user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Job is not assigned to you")
    if job.status != "assigned":
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"Cannot decline a job in status '{job.status}'")
    job.status = "declined"
    job.decline_reason = body.reason
    audit.audit(ctx.db, ctx.org_id, ctx.user_id, "job.decline", "job", job.id, {"reason": body.reason})
    notify.notify(ctx.db, ctx.org_id, job.project.manager_id, "job_declined", {
        "job_id": job.id, "reason": body.reason,
    })
    ctx.db.commit()
    return serialize_job(ctx.db, job)


@router.get("/jobs/{job_id}/qa", response_model=schemas.QAReport)
def get_qa(job_id: int, ctx: Context = Depends(get_context)):
    job = job_or_404(ctx, job_id)
    ensure_job_access(ctx, job)
    return qa.run_qa(ctx.db, job)


@router.post("/jobs/{job_id}/submit", response_model=schemas.JobOut)
def submit_job(job_id: int, ctx: Context = Depends(get_context)):
    """Сдача задания: только после прохождения QA (FR-09)."""
    job = job_or_404(ctx, job_id)
    if job.assignee_id != ctx.user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Job is not assigned to you")
    if job.status not in EDITABLE_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"Cannot submit a job in status '{job.status}'")
    report = qa.run_qa(ctx.db, job)
    if not report["can_submit"]:
        return Response(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content=schemas.QAReport(**report).model_dump_json(),
            media_type="application/json",
        )
    job.status = "submitted"
    audit.audit(ctx.db, ctx.org_id, ctx.user_id, "job.submit", "job", job.id, {})
    notify.notify(ctx.db, ctx.org_id, job.project.manager_id, "job_submitted", {"job_id": job.id})
    ctx.db.commit()
    return serialize_job(ctx.db, job)


@router.post("/jobs/{job_id}/complete", response_model=schemas.JobOut)
def complete_job(job_id: int, ctx: Context = Depends(get_context)):
    ctx.require_role("admin", "pm")
    job = job_or_404(ctx, job_id)
    if job.status != "submitted":
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"Cannot complete a job in status '{job.status}'")
    job.status = "completed"
    audit.audit(ctx.db, ctx.org_id, ctx.user_id, "job.complete", "job", job.id, {})
    ctx.db.commit()
    return serialize_job(ctx.db, job)


@router.post("/jobs/{job_id}/return", response_model=schemas.JobOut)
def return_job(job_id: int, body: schemas.ReturnRequest, ctx: Context = Depends(get_context)):
    ctx.require_role("admin", "pm")
    job = job_or_404(ctx, job_id)
    if job.status != "submitted":
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"Cannot return a job in status '{job.status}'")
    job.status = "rework"
    job.rework_comment = body.comment
    audit.audit(ctx.db, ctx.org_id, ctx.user_id, "job.return", "job", job.id, {"comment": body.comment})
    notify.notify(ctx.db, ctx.org_id, job.assignee_id, "job_returned", {
        "job_id": job.id, "comment": body.comment,
    })
    ctx.db.commit()
    return serialize_job(ctx.db, job)


@router.post("/jobs/{job_id}/cancel", response_model=schemas.JobOut)
def cancel_job(job_id: int, ctx: Context = Depends(get_context)):
    ctx.require_role("admin", "pm")
    job = job_or_404(ctx, job_id)
    if job.status in ("completed", "canceled"):
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"Cannot cancel a job in status '{job.status}'")
    previous_assignee = job.assignee_id
    job.status = "canceled"
    audit.audit(ctx.db, ctx.org_id, ctx.user_id, "job.cancel", "job", job.id, {})
    notify.notify(ctx.db, ctx.org_id, previous_assignee, "job_canceled", {"job_id": job.id})
    ctx.db.commit()
    return serialize_job(ctx.db, job)


@router.get("/jobs/{job_id}/export")
def export_job(job_id: int, ctx: Context = Depends(get_context)):
    """Выгрузка переведенного файла (FR-14): менеджеру после сдачи."""
    ctx.require_role("admin", "pm")
    job = job_or_404(ctx, job_id)
    if job.status not in ("submitted", "completed"):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Export is available after submission")
    if job.document.processing_status != "ready":
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Document is not processed yet")
    if not job.document.storage_path:
        targets = [segment.target_text for segment in job.segments]
        exported = ("\n".join(targets) + "\n").encode("utf-8")
        fmt = job.document.format
    else:
        with open(job.document.storage_path, "rb") as fh:
            original = fh.read()
        targets = [segment.target_text for segment in job.segments]
        exported = segmentation.export_file(job.document.format, original, targets)
        fmt = job.document.format
    base, _ = os.path.splitext(job.document.name)
    filename = f"{base}_{job.target_lang}.{fmt}"
    media_types = {
        "txt": "text/plain; charset=utf-8",
        "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "xliff": "application/x-xliff+xml",
    }
    return Response(
        content=exported,
        media_type=media_types[fmt],
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/jobs/{job_id}/segments/{segment_id}/mt", response_model=schemas.MTSuggestion)
async def mt_suggest(job_id: int, segment_id: int, ctx: Context = Depends(get_context)):
    """Подсказка машинного перевода (FR-08): всегда по действию пользователя."""
    job = job_or_404(ctx, job_id)
    ensure_job_access(ctx, job)
    if not ctx.org.mt_allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="MT is not allowed for this organization")
    if not job.project.mt_enabled:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="MT is disabled for this project")
    segment = ctx.db.get(models.Segment, segment_id)
    if segment is None or segment.job_id != job.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Segment not found")
    provider = get_provider()
    if provider.name == "none":
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="MT provider is not configured")
    try:
        text = await provider.translate(segment.source_text, job.project.source_lang, job.target_lang)
    except MTError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc))
    return schemas.MTSuggestion(text=text, provider=provider.name)
