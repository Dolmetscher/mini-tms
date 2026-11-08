import os
import re
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, Response, UploadFile, status
from sqlalchemy.orm import Session

from .. import config, models, schemas
from ..database import SessionLocal
from ..deps import Context, get_context
from ..fuzzy import length_bound_ratio, normalize, similarity_ratio
from ..formats import segmentation
from ..helpers import can_manage, content_disposition, ensure_project_access, naive_utc, project_or_404, utcnow
from ..services import audit, delivery, words

router = APIRouter(prefix="/api", tags=["projects"])


def _visible_projects(ctx: Context):
    query = ctx.db.query(models.Project).filter(
        models.Project.org_id == ctx.org_id, models.Project.status == "active"
    )
    if can_manage(ctx):
        return query.order_by(models.Project.id.desc()).all()
    # Переводчик видит проекты, в которых у него есть задания (FR-02).
    job_project_ids = [
        row[0]
        for row in ctx.db.query(models.Job.project_id)
        .filter(models.Job.assignee_id == ctx.user_id)
        .distinct()
    ]
    return query.filter(models.Project.id.in_(job_project_ids)).order_by(models.Project.id.desc()).all()


@router.get("/projects", response_model=list[schemas.ProjectOut])
def list_projects(ctx: Context = Depends(get_context)):
    return _visible_projects(ctx)


@router.post("/projects", response_model=schemas.ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(body: schemas.ProjectCreate, ctx: Context = Depends(get_context)):
    ctx.require_role("admin", "pm")
    project = models.Project(
        org_id=ctx.org_id,
        name=body.name,
        source_lang=body.source_lang,
        target_langs=body.target_langs,
        # Клиент может прислать дедлайн с офсетом: нормализуем к наивному UTC,
        # иначе SQLite сохранит локальное время как есть (сдвиг относительно UTC).
        deadline=naive_utc(body.deadline) if body.deadline is not None else None,
        instructions=body.instructions,
        mt_enabled=body.mt_enabled,
        manager_id=ctx.user_id,
    )
    ctx.db.add(project)
    ctx.db.flush()
    audit.audit(ctx.db, ctx.org_id, ctx.user_id, "project.create", "project", project.id, {"name": body.name})
    ctx.db.commit()
    ctx.db.refresh(project)
    return project


@router.patch("/projects/{project_id}", response_model=schemas.ProjectOut)
def update_project(project_id: int, body: schemas.ProjectUpdate, ctx: Context = Depends(get_context)):
    ctx.require_role("admin", "pm")
    project = project_or_404(ctx, project_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        if field == "deadline" and value is not None:
            value = naive_utc(value)
        setattr(project, field, value)
    audit.audit(ctx.db, ctx.org_id, ctx.user_id, "project.update", "project", project_id, body.model_dump(exclude_unset=True))
    ctx.db.commit()
    ctx.db.refresh(project)
    return project


@router.get("/projects/{project_id}", response_model=schemas.ProjectOut)
def get_project(project_id: int, ctx: Context = Depends(get_context)):
    project = project_or_404(ctx, project_id)
    ensure_project_access(ctx, project)
    return project


@router.get("/projects/{project_id}/documents", response_model=list[schemas.DocumentOut])
def list_documents(project_id: int, ctx: Context = Depends(get_context)):
    ctx.require_role("admin", "pm")
    project_or_404(ctx, project_id)
    return (
        ctx.db.query(models.Document)
        .filter(models.Document.project_id == project_id)
        .order_by(models.Document.id.desc())
        .all()
    )


@router.get("/documents/{document_id}", response_model=schemas.DocumentOut)
def get_document(document_id: int, ctx: Context = Depends(get_context)):
    ctx.require_role("admin", "pm")
    document = ctx.db.get(models.Document, document_id)
    if document is None or document.org_id != ctx.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Документ не найден")
    return document


def _process_document(document_id: int) -> None:
    """Фоновая обработка документа (FR-10): сегментация + создание заданий уже сделаны."""
    db = SessionLocal()
    try:
        document = db.get(models.Document, document_id)
        if document is None:
            return
        with open(document.storage_path, "rb") as fh:
            content = fh.read()
        try:
            segments, notes = segmentation.load_segments(document.format, content)
        except Exception as exc:
            document.processing_status = "failed"
            document.processing_error = f"Failed to parse document: {exc}"
            db.commit()
            return
        if not segments:
            document.processing_status = "failed"
            document.processing_error = "Документ не содержит текста"
            db.commit()
            return
        # Существующие переводы (XLIFF state/approved) — черновиками, как
        # пред-перевод: подтверждение и QA остаются на переводчике.
        translations = segmentation.load_translations(document.format, content)
        jobs = db.query(models.Job).filter(models.Job.document_id == document.id).all()
        for job in jobs:
            for position, source in enumerate(segments):
                translation = translations[position] if translations else None
                segment = models.Segment(
                    job_id=job.id,
                    position=position,
                    source_text=source,
                    source_words=words.count_words(source),
                )
                if translation:
                    segment.target_text = translation
                    segment.translation_status = "translated"
                db.add(segment)
                if translation:
                    db.flush()  # id для ревизии
                    db.add(models.SegmentRevision(
                        segment_id=segment.id,
                        author_id=document.uploaded_by,
                        target_text=translation,
                    ))
        document.processing_status = "ready"
        document.processing_notes = notes
        db.commit()
    finally:
        db.close()


@router.post("/projects/{project_id}/documents", response_model=schemas.DocumentOut, status_code=status.HTTP_201_CREATED)
async def upload_document(
    project_id: int,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    ctx: Context = Depends(get_context),
):
    ctx.require_role("admin", "pm")
    project = project_or_404(ctx, project_id)
    fmt = segmentation.detect_format(file.filename or "")
    if fmt is None:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Поддерживаемые форматы: TXT, DOCX, XLIFF")

    content = await file.read()
    os.makedirs(config.DATA_DIR, exist_ok=True)
    safe_name = os.path.basename(file.filename or "document")
    storage_path = os.path.join(config.DATA_DIR, f"{uuid.uuid4().hex}_{safe_name}")
    with open(storage_path, "wb") as fh:
        fh.write(content)

    document = models.Document(
        org_id=ctx.org_id,
        project_id=project.id,
        name=safe_name,
        format=fmt,
        storage_path=storage_path,
        uploaded_by=ctx.user_id,
    )
    ctx.db.add(document)
    ctx.db.flush()
    # На каждый целевой язык — отдельное задание (§4 ТЗ).
    for target_lang in project.target_langs:
        ctx.db.add(
            models.Job(
                org_id=ctx.org_id,
                project_id=project.id,
                document_id=document.id,
                target_lang=target_lang,
                deadline=project.deadline,
            )
        )
    audit.audit(
        ctx.db, ctx.org_id, ctx.user_id, "document.upload", "document", document.id, {"name": safe_name, "format": fmt}
    )
    ctx.db.commit()
    ctx.db.refresh(document)
    background_tasks.add_task(_process_document, document.id)
    return document


@router.post("/projects/{project_id}/resources", response_model=schemas.ResourceOut, status_code=status.HTTP_201_CREATED)
def connect_resource(project_id: int, body: schemas.ResourceCreate, ctx: Context = Depends(get_context)):
    ctx.require_role("admin", "pm")
    project = project_or_404(ctx, project_id)
    if bool(body.tm_id) == bool(body.tb_id):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Укажите ровно одно: tm_id или tb_id")

    if body.tm_id is not None:
        tm = ctx.db.get(models.TM, body.tm_id)
        if tm is None or tm.org_id != ctx.org_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Память переводов (TM) не найдена")
        # Проверка совместимости языков (FR-12).
        if tm.source_lang != project.source_lang or tm.target_lang not in project.target_langs:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Языковая пара TM {tm.source_lang}->{tm.target_lang} не совпадает с проектом",
            )
        audit.audit(ctx.db, ctx.org_id, ctx.user_id, "resource.connect_tm", "project", project.id, {"tm_id": tm.id})
    else:
        tb = ctx.db.get(models.TB, body.tb_id)
        if tb is None or tb.org_id != ctx.org_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Термбаза (TB) не найдена")
        if tb.source_lang != project.source_lang or tb.target_lang not in project.target_langs:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Языковая пара TB {tb.source_lang}->{tb.target_lang} не совпадает с проектом",
            )
        audit.audit(ctx.db, ctx.org_id, ctx.user_id, "resource.connect_tb", "project", project.id, {"tb_id": tb.id})

    resource = models.ProjectResource(
        project_id=project.id,
        tm_id=body.tm_id,
        tb_id=body.tb_id,
        use_for_search=body.use_for_search,
        use_for_write=body.use_for_write,
    )
    ctx.db.add(resource)
    ctx.db.commit()
    ctx.db.refresh(resource)
    return _serialize_resource(ctx.db, resource)


def _serialize_resource(db: Session, resource: models.ProjectResource) -> schemas.ResourceOut:
    tm = db.get(models.TM, resource.tm_id) if resource.tm_id else None
    tb = db.get(models.TB, resource.tb_id) if resource.tb_id else None
    return schemas.ResourceOut(
        id=resource.id,
        tm=schemas.TMRef.model_validate(tm) if tm else None,
        tb=schemas.TBRef.model_validate(tb) if tb else None,
        use_for_search=resource.use_for_search,
        use_for_write=resource.use_for_write,
    )


@router.get("/projects/{project_id}/resources", response_model=list[schemas.ResourceOut])
def list_resources(project_id: int, ctx: Context = Depends(get_context)):
    project = project_or_404(ctx, project_id)
    ensure_project_access(ctx, project)
    resources = ctx.db.query(models.ProjectResource).filter(models.ProjectResource.project_id == project_id).all()
    return [_serialize_resource(ctx.db, r) for r in resources]


@router.delete("/projects/{project_id}/resources/{resource_id}", status_code=status.HTTP_204_NO_CONTENT)
def disconnect_resource(project_id: int, resource_id: int, ctx: Context = Depends(get_context)):
    ctx.require_role("admin", "pm")
    project_or_404(ctx, project_id)
    resource = ctx.db.get(models.ProjectResource, resource_id)
    if resource is None or resource.project_id != project_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Ресурс не найден")
    ctx.db.delete(resource)
    audit.audit(ctx.db, ctx.org_id, ctx.user_id, "resource.disconnect", "project", project_id, {"resource_id": resource_id})
    ctx.db.commit()


@router.get("/projects/{project_id}/tm/search", response_model=list[schemas.TMSuggestionOut])
def concordance(
    project_id: int,
    text: str = Query(min_length=1),
    target_lang: str = Query(min_length=2),
    side: str = Query(default="source", pattern="^(source|target)$"),
    mode: str = Query(default="match", pattern="^(match|concordance)$"),
    ctx: Context = Depends(get_context),
):
    """Поиск по подключенным TM проекта (FR-06).

    mode=match — автоподсказка при выборе сегмента: точные и fuzzy совпадения
    полной фразы (side=source) либо ранее переведённого текста (side=target).
    mode=concordance — конкорданс: сегменты, СОДЕРЖАЩИЕ запрос как слово/фразу
    (поиск вхождения по границе слова — «как это переводилось раньше»).
    """
    project = project_or_404(ctx, project_id)
    ensure_project_access(ctx, project)
    if target_lang not in project.target_langs:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Неизвестный целевой язык")

    wanted = normalize(text)
    results: list[schemas.TMSuggestionOut] = []
    resources = (
        ctx.db.query(models.ProjectResource, models.TM)
        .join(models.TM, models.ProjectResource.tm_id == models.TM.id)
        .filter(
            models.ProjectResource.project_id == project_id,
            models.ProjectResource.use_for_search.is_(True),
            models.TM.source_lang == project.source_lang,
            models.TM.target_lang == target_lang,
        )
        .all()
    )

    if mode == "concordance":
        # Поиск вхождения по границе слова; сортировка по «покрытию» —
        # какую долю сегмента занимает запрос (точное совпадение = 100).
        word_re = re.compile(rf"(?<!\w){re.escape(wanted)}(?!\w)")
        for _, tm in resources:
            for entry in ctx.db.query(models.TMEntry).filter(models.TMEntry.tm_id == tm.id).all():
                haystack = entry.source_text if side == "source" else entry.target_text
                if not word_re.search(normalize(haystack)):
                    continue
                coverage = min(99, int(round(len(wanted) / max(1, len(normalize(haystack))) * 100)))
                if normalize(haystack) == wanted:
                    coverage = 100
                results.append(schemas.TMSuggestionOut(
                    source_match=entry.source_text, target_suggestion=entry.target_text,
                    match_percentage=coverage, tm_name=tm.name, match_type="concordance",
                ))
        results.sort(key=lambda r: r.match_percentage, reverse=True)
        return results[: config.CONCORDANCE_MAX_RESULTS]

    for _, tm in resources:
        exact, scored = [], []
        for entry in ctx.db.query(models.TMEntry).filter(models.TMEntry.tm_id == tm.id).all():
            haystack = entry.source_text if side == "source" else entry.target_text
            if normalize(haystack) == wanted:
                exact.append(entry)
            elif length_bound_ratio(text, haystack) >= config.FUZZY_MIN_SCORE:
                score = similarity_ratio(text, haystack)
                if score >= config.FUZZY_MIN_SCORE:
                    scored.append((score, entry))
        for entry in exact:
            results.append(schemas.TMSuggestionOut(
                source_match=entry.source_text, target_suggestion=entry.target_text,
                match_percentage=100, tm_name=tm.name, match_type="exact",
            ))
        for score, entry in sorted(scored, key=lambda p: p[0], reverse=True):
            results.append(schemas.TMSuggestionOut(
                source_match=entry.source_text, target_suggestion=entry.target_text,
                match_percentage=score, tm_name=tm.name, match_type="fuzzy",
            ))
    results.sort(key=lambda r: r.match_percentage, reverse=True)
    return results[: config.FUZZY_MAX_SUGGESTIONS]


@router.get("/projects/{project_id}/tb/lookup", response_model=list[schemas.TBHitOut])
def tb_lookup(
    project_id: int,
    text: str = Query(min_length=1),
    target_lang: str = Query(min_length=2),
    ctx: Context = Depends(get_context),
):
    """Термины из TB, встречающиеся в тексте (FR-07)."""
    project = project_or_404(ctx, project_id)
    ensure_project_access(ctx, project)
    text_lower = text.casefold()
    hits: list[schemas.TBHitOut] = []
    resources = (
        ctx.db.query(models.ProjectResource, models.TB)
        .join(models.TB, models.ProjectResource.tb_id == models.TB.id)
        .filter(
            models.ProjectResource.project_id == project_id,
            models.ProjectResource.use_for_search.is_(True),
            models.TB.source_lang == project.source_lang,
            models.TB.target_lang == target_lang,
        )
        .all()
    )
    seen: set[int] = set()
    for _, tb in resources:
        for entry in ctx.db.query(models.TBEntry).filter(models.TBEntry.tb_id == tb.id).all():
            if entry.id in seen:
                continue
            if entry.term.casefold() in text_lower:
                seen.add(entry.id)
                hits.append(schemas.TBHitOut(
                    term=entry.term, translation=entry.translation,
                    note=entry.note, status=entry.status, tb_name=tb.name,
                ))
    return hits


@router.get("/projects/{project_id}/deliver")
def deliver_project(project_id: int, ctx: Context = Depends(get_context)):
    """Пакет доставки проекта (pm/admin): все сданные целевые файлы одним ZIP.

    Практика закрытия цикла: после сдачи/приемки заданий менеджер забирает
    готовые переводы одним архивом (аналог «delivery package» XTRF/Plunet),
    с манифестом — что внутри и в каком статусе.
    """
    ctx.require_role("admin", "pm")
    project = project_or_404(ctx, project_id)
    jobs = delivery.deliverable_jobs(ctx.db, project)
    if not jobs:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="Нет сданных заданий для отгрузки",
        )
    payload = delivery.build_delivery_zip(jobs)
    audit.audit(ctx.db, ctx.org_id, ctx.user_id, "project.deliver", "project", project.id, {
        "jobs": len(jobs),
    })
    ctx.db.commit()
    safe_name = re.sub(r'["\\/:*?<>|]', "_", project.name)
    return Response(
        content=payload,
        media_type="application/zip",
        headers={"Content-Disposition": content_disposition(f"{safe_name}_delivery.zip")},
    )
