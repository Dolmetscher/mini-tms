import os
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from .. import models, schemas
from ..deps import Context, get_context
from ..formats import docx_io
from ..helpers import (
    can_manage,
    content_disposition,
    ensure_job_access,
    job_deadline,
    job_or_404,
    naive_utc,
    org_member_ids,
    serialize_job,
)
from ..services import audit, delivery, lqa, leveraging, notify, qa
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
        if body.target_text != segment.target_text:
            # История правок (track changes): снапшот нового значения — кто/когда/что.
            ctx.db.add(models.SegmentRevision(
                segment_id=segment.id, author_id=ctx.user_id, target_text=body.target_text,
            ))
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
    propagated: list[models.Segment] = []
    if segment.confirmed and segment.translation_status == "translated":
        _write_translations_to_tm(ctx.db, job, segment)
        # Автопропагация: идентичные пустые сегменты получают подтвержденный
        # перевод (черновиком — вычитка контекста остается за переводчиком).
        propagated = leveraging.propagate_translation(ctx.db, job, segment)

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
            updated_at=segment.updated_at.isoformat(),
        ),
    )
    for seg in propagated:
        await manager.send_users(
            org_member_ids(ctx.db, ctx.org_id),
            make_event(
                "segment_updated",
                job_id=job.id,
                segment_id=seg.id,
                position=seg.position,
                translation_status=seg.translation_status,
                confirmed=seg.confirmed,
                target_text=seg.target_text,
                job_status=job.status,
                user=ctx.user.username,
                updated_at=seg.updated_at.isoformat(),
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


# --- LQA: оценка качества перевода (ревьюер размечает ошибки) ---


def _segment_of_job(db: Session, job: models.Job, segment_id: int) -> models.Segment:
    segment = db.get(models.Segment, segment_id)
    if segment is None or segment.job_id != job.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Segment not found in this job")
    return segment


@router.get("/jobs/{job_id}/lqa", response_model=schemas.LQAReport)
def get_lqa(job_id: int, ctx: Context = Depends(get_context)):
    """LQA-отчет: размеченные ошибки, штрафные баллы, оценка на 1000 слов."""
    job = job_or_404(ctx, job_id)
    ensure_job_access(ctx, job)
    return schemas.LQAReport(**lqa.job_lqa_report(ctx.db, job))


@router.post("/jobs/{job_id}/lqa", response_model=schemas.LQAErrorOut, status_code=status.HTTP_201_CREATED)
async def add_lqa_error(job_id: int, body: schemas.LQAErrorCreate, ctx: Context = Depends(get_context)):
    """Разметка ошибки качества ревьюером (pm/admin): категория × серьёзность."""
    ctx.require_role("admin", "pm")
    job = job_or_404(ctx, job_id)
    _segment_of_job(ctx.db, job, body.segment_id)
    error = models.LQAError(
        job_id=job.id,
        segment_id=body.segment_id,
        author_id=ctx.user_id,
        category=body.category,
        severity=body.severity,
        comment=body.comment,
    )
    ctx.db.add(error)
    ctx.db.flush()
    audit.audit(ctx.db, ctx.org_id, ctx.user_id, "job.lqa_error_add", "job", job.id, {
        "segment_id": body.segment_id, "category": body.category, "severity": body.severity,
    })
    ctx.db.commit()
    report = lqa.job_lqa_report(ctx.db, job)
    item = next(i for i in report["errors"] if i["id"] == error.id)
    await manager.send_users(
        org_member_ids(ctx.db, ctx.org_id),
        make_event(
            "lqa_error_added", job_id=job.id, segment_id=body.segment_id,
            severity=body.severity, category=body.category, score=report["score"],
            user=ctx.user.username,
        ),
    )
    return schemas.LQAErrorOut(**item)


@router.delete("/jobs/{job_id}/lqa/{error_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_lqa_error(job_id: int, error_id: int, ctx: Context = Depends(get_context)):
    """Снять ошибку (например, ревьюер ошибся при разметке) — pm/admin."""
    ctx.require_role("admin", "pm")
    job = job_or_404(ctx, job_id)
    error = (
        ctx.db.query(models.LQAError)
        .filter(models.LQAError.id == error_id, models.LQAError.job_id == job.id)
        .first()
    )
    if error is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="LQA error not found")
    ctx.db.delete(error)
    audit.audit(ctx.db, ctx.org_id, ctx.user_id, "job.lqa_error_delete", "job", job.id, {
        "segment_id": error.segment_id, "category": error.category,
    })
    ctx.db.commit()
    report = lqa.job_lqa_report(ctx.db, job)
    await manager.send_users(
        org_member_ids(ctx.db, ctx.org_id),
        make_event(
            "lqa_error_deleted", job_id=job.id, segment_id=error.segment_id,
            score=report["score"], user=ctx.user.username,
        ),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- История правок сегмента (track changes) ---


@router.get("/segments/{segment_id}/history", response_model=list[schemas.SegmentRevisionOut])
def segment_history(segment_id: int, ctx: Context = Depends(get_context)):
    """Все изменения перевода сегмента: автор, время, текст (хронология).

    Участники задания видят эволюцию перевода — кто и что менял, включая
    пред-перевод и автопропагацию (автор — инициатор действия).
    """
    segment = ctx.db.get(models.Segment, segment_id)
    if segment is None or segment.job.org_id != ctx.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Segment not found")
    ensure_job_access(ctx, segment.job)
    revisions = (
        ctx.db.query(models.SegmentRevision)
        .filter(models.SegmentRevision.segment_id == segment.id)
        .order_by(models.SegmentRevision.created_at, models.SegmentRevision.id)
        .all()
    )
    return [
        schemas.SegmentRevisionOut(
            id=rev.id,
            segment_id=segment.id,
            position=segment.position,
            author=schemas.UserRef(id=rev.author.id, username=rev.author.username),
            target_text=rev.target_text,
            created_at=rev.created_at,
        )
        for rev in revisions
    ]


# --- Комментарии к сегментам (диалог переводчик ↔ менеджер) ---


def _segment_notes(db: Session, segment: models.Segment) -> list[schemas.SegmentNoteOut]:
    notes = (
        db.query(models.SegmentNote)
        .filter(models.SegmentNote.segment_id == segment.id)
        .order_by(models.SegmentNote.created_at, models.SegmentNote.id)
        .all()
    )
    return [
        schemas.SegmentNoteOut(
            id=note.id,
            segment_id=segment.id,
            position=segment.position,
            author=schemas.UserRef(id=note.author.id, username=note.author.username),
            body=note.body,
            created_at=note.created_at,
        )
        for note in notes
    ]


@router.get("/segments/{segment_id}/notes", response_model=list[schemas.SegmentNoteOut])
def list_notes(segment_id: int, ctx: Context = Depends(get_context)):
    segment = ctx.db.get(models.Segment, segment_id)
    if segment is None or segment.job.org_id != ctx.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Segment not found")
    ensure_job_access(ctx, segment.job)
    return _segment_notes(ctx.db, segment)


@router.post("/segments/{segment_id}/notes", response_model=schemas.SegmentNoteOut,
             status_code=status.HTTP_201_CREATED)
async def add_note(segment_id: int, body: schemas.SegmentNoteCreate, ctx: Context = Depends(get_context)):
    """Комментарий к сегменту: участник задания (исполнитель или менеджмент).

    Разрешен в любом статусе — обсуждение ведется и на ревью после сдачи.
    """
    segment = ctx.db.get(models.Segment, segment_id)
    if segment is None or segment.job.org_id != ctx.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Segment not found")
    job = segment.job
    if not can_manage(ctx) and job.assignee_id != ctx.user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Job is not assigned to you")
    note = models.SegmentNote(
        segment_id=segment.id,
        author_id=ctx.user_id,
        body=body.body,
    )
    ctx.db.add(note)
    ctx.db.flush()
    ctx.db.commit()
    await manager.send_users(
        org_member_ids(ctx.db, ctx.org_id),
        make_event(
            "note_added", job_id=job.id, segment_id=segment.id,
            position=segment.position, body=note.body,
            user=ctx.user.username,
        ),
    )
    return _segment_notes(ctx.db, segment)[-1]


@router.get("/jobs/{job_id}/analysis", response_model=schemas.AnalysisReport)
def get_analysis(job_id: int, ctx: Context = Depends(get_context)):
    """Анализ объема: совпадения TM (exact/fuzzy-банды), повторы, взвешенные
    слова — котировка и оценка «остатка» до начала перевода."""
    job = job_or_404(ctx, job_id)
    ensure_job_access(ctx, job)
    return leveraging.analyze_job(ctx.db, job)


@router.post("/jobs/{job_id}/pre-translate", response_model=schemas.PreTranslateResult)
async def pre_translate_job(
    job_id: int, body: schemas.PreTranslateRequest, ctx: Context = Depends(get_context)
):
    """Пред-перевод из TM (batch): пустые сегменты заполняются лучшими
    совпадениями >= min_score, затем повторами внутри задания.

    Результат — черновики без подтверждения: даже 100% совпадения вычитываются
    переводчиком (практика CAT), QA требует подтверждения каждого сегмента.
    """
    job = job_or_404(ctx, job_id)
    if not can_manage(ctx) and job.assignee_id != ctx.user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Job is not assigned to you")
    if job.status not in EDITABLE_STATUSES:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Job status '{job.status}' does not allow editing",
        )
    result = leveraging.pre_translate(ctx.db, job, ctx.user_id, body.min_score)
    audit.audit(
        ctx.db, ctx.org_id, ctx.user_id, "job.pre_translate", "job", job.id,
        {**result, "min_score": body.min_score},
    )
    ctx.db.commit()
    await manager.send_users(
        org_member_ids(ctx.db, ctx.org_id),
        make_event("job_pretranslated", job_id=job.id, user=ctx.user.username, **result),
    )
    return result


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
    # Если ревьюер уже размечал ошибки LQA — переводчик получает оценку сразу
    # (контекст возврата: что именно и насколько серьезно).
    report = lqa.job_lqa_report(ctx.db, job)
    payload = {"job_id": job.id, "comment": body.comment}
    if report["error_count"]:
        payload["lqa_score"] = report["score"]
        payload["lqa_errors"] = report["error_count"]
    notify.notify(ctx.db, ctx.org_id, job.assignee_id, "job_returned", payload)
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
    exported, fmt = delivery.export_target_bytes(job)
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
        headers={"Content-Disposition": content_disposition(filename)},
    )


@router.get("/jobs/{job_id}/export-bilingual")
def export_bilingual(job_id: int, ctx: Context = Depends(get_context)):
    """Двуязычная выгрузка DOCX для ревью/заказчика: таблица №/исходник/перевод.

    Доступна исполнителю и менеджменту на любом статусе — в отличие от
    экспорта перевода (только после сдачи), ревью-копия не является
    «готовым продуктом» и не раскрывается вне круга проекта.
    """
    job = job_or_404(ctx, job_id)
    ensure_job_access(ctx, job)
    rows = [(s.position + 1, s.source_text, s.target_text) for s in job.segments]
    payload = docx_io.render_bilingual(
        f"{job.document.name} — {job.project.source_lang}\u2192{job.target_lang}",
        job.project.source_lang,
        job.target_lang,
        rows,
    )
    base, _ = os.path.splitext(job.document.name)
    audit.audit(
        ctx.db, ctx.org_id, ctx.user_id, "job.export_bilingual", "job", job.id,
        {"segments": len(rows)},
    )
    ctx.db.commit()
    return Response(
        content=payload,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": content_disposition(f"{base}_{job.target_lang}_bilingual.docx")},
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
