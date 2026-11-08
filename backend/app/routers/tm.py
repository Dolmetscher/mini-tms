import csv
import io
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from lxml import etree
from sqlalchemy.orm import Session

from .. import config, models, schemas
from ..deps import Context, get_context
from ..formats import segmentation
from ..fuzzy import length_bound_ratio, normalize, similarity_ratio
from ..helpers import can_manage, content_disposition
from ..services import align as align_service
from ..services import audit

router = APIRouter(prefix="/api/tm", tags=["tm"])


def _tm_or_404(ctx: Context, tm_id: int) -> models.TM:
    tm = ctx.db.get(models.TM, tm_id)
    if tm is None or tm.org_id != ctx.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="TM not found")
    return tm


@router.get("/tms", response_model=list[schemas.TMOut])
def list_tms(ctx: Context = Depends(get_context)):
    if not can_manage(ctx):
        # Переводчику TM доступны через проект; администрирование — менеджменту.
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Requires management role")
    tms = ctx.db.query(models.TM).filter(models.TM.org_id == ctx.org_id).order_by(models.TM.id).all()
    return [
        schemas.TMOut(
            id=tm.id, name=tm.name, source_lang=tm.source_lang, target_lang=tm.target_lang,
            entry_count=ctx.db.query(models.TMEntry).filter(models.TMEntry.tm_id == tm.id).count(),
            created_at=tm.created_at,
        )
        for tm in tms
    ]


@router.post("/tms", response_model=schemas.TMOut, status_code=status.HTTP_201_CREATED)
def create_tm(body: schemas.TMCreate, ctx: Context = Depends(get_context)):
    ctx.require_role("admin", "pm")
    tm = models.TM(org_id=ctx.org_id, **body.model_dump())
    ctx.db.add(tm)
    ctx.db.flush()
    audit.audit(ctx.db, ctx.org_id, ctx.user_id, "tm.create", "tm", tm.id, {"name": body.name})
    ctx.db.commit()
    ctx.db.refresh(tm)
    return schemas.TMOut(id=tm.id, name=tm.name, source_lang=tm.source_lang,
                         target_lang=tm.target_lang, entry_count=0, created_at=tm.created_at)


@router.get("/tms/{tm_id}/entries", response_model=list[schemas.TMEntryOut])
def list_entries(
    tm_id: int,
    response: Response,
    q: str | None = None,
    limit: int = Query(default=100, le=500),
    offset: int = Query(default=0, ge=0),
    ctx: Context = Depends(get_context),
):
    ctx.require_role("admin", "pm")
    tm = _tm_or_404(ctx, tm_id)
    query = ctx.db.query(models.TMEntry).filter(models.TMEntry.tm_id == tm.id)
    if q:
        # Поиск по обеим сторонам (паритет с TB): при ведении памяти часто
        # ищут по переводу — «как мы это уже переводили».
        needle = normalize(q)
        entries = [
            e for e in query.all()
            if needle in normalize(e.source_text) or needle in normalize(e.target_text)
        ]
    else:
        entries = query.all()
    response.headers["X-Total-Count"] = str(len(entries))
    return entries[offset : offset + limit]


@router.post("/tms/{tm_id}/entries", response_model=schemas.TMEntryOut, status_code=status.HTTP_201_CREATED)
def upsert_entry(tm_id: int, body: schemas.TMEntryUpsert, ctx: Context = Depends(get_context)):
    ctx.require_role("admin", "pm")
    tm = _tm_or_404(ctx, tm_id)
    entry = (
        ctx.db.query(models.TMEntry)
        .filter(models.TMEntry.tm_id == tm.id, models.TMEntry.source_text == body.source_text)
        .first()
    )
    if entry is None:
        entry = models.TMEntry(tm_id=tm.id, source_text=body.source_text, target_text=body.target_text)
        ctx.db.add(entry)
    else:
        entry.target_text = body.target_text  # политика дубликатов: обновление (документировано)
    ctx.db.commit()
    ctx.db.refresh(entry)
    return entry


@router.post("/tms/{tm_id}/import-tmx", status_code=status.HTTP_200_OK)
def import_tmx(tm_id: int, file: UploadFile = File(...), ctx: Context = Depends(get_context)):
    """Импорт TMX (FR-12): tuv с xml:lang -> записи; совпадения обновляются."""
    ctx.require_role("admin", "pm")
    tm = _tm_or_404(ctx, tm_id)
    from lxml import etree

    try:
        tree = etree.parse(file.file)
    except etree.XMLSyntaxError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Invalid TMX: {exc}")

    def seg_text(tuv) -> str:
        if tuv is None:
            return ""
        seg = tuv.find("seg")
        return "".join(seg.itertext()).strip() if seg is not None else ""

    added = updated = skipped = 0
    # autoflush у сессии выключен: записи, добавленные в этой транзакции,
    # не видны последующим SELECT — дубль source внутри одного файла
    # отслеживаем в памяти, иначе INSERT упадет по UNIQUE (tm_id, source_text).
    seen: dict[str, models.TMEntry] = {}
    for tu in tree.getroot().iter("tu"):
        tuvs = tu.findall("tuv")
        source_tuv = target_tuv = None
        for tuv in tuvs:
            lang = (tuv.get("{http://www.w3.org/XML/1998/namespace}lang") or "").lower()
            if lang.startswith(tm.source_lang.lower()) and source_tuv is None:
                source_tuv = tuv
            if lang.startswith(tm.target_lang.lower()) and target_tuv is None:
                target_tuv = tuv
        source_text, target_text = seg_text(source_tuv), seg_text(target_tuv)
        if not source_text or not target_text:
            skipped += 1
            continue
        entry = seen.get(source_text) or (
            ctx.db.query(models.TMEntry)
            .filter(models.TMEntry.tm_id == tm.id, models.TMEntry.source_text == source_text)
            .first()
        )
        if entry is None:
            entry = models.TMEntry(tm_id=tm.id, source_text=source_text, target_text=target_text)
            ctx.db.add(entry)
            added += 1
        else:
            entry.target_text = target_text
            updated += 1
        seen[source_text] = entry
    audit.audit(ctx.db, ctx.org_id, ctx.user_id, "tm.import_tmx", "tm", tm.id,
                {"added": added, "updated": updated, "skipped": skipped})
    ctx.db.commit()
    return {"added": added, "updated": updated, "skipped": skipped}


@router.post("/tms/{tm_id}/import-csv", status_code=status.HTTP_200_OK)
def import_csv(tm_id: int, file: UploadFile = File(...), ctx: Context = Depends(get_context)):
    """Импорт CSV (FR-12, паритет с TB): заголовок source,target.

    Семантика — как у TMX-импорта и upsert: source — ключ памяти,
    совпадения обновляют перевод.
    """
    ctx.require_role("admin", "pm")
    tm = _tm_or_404(ctx, tm_id)
    try:
        raw = file.file.read().decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"CSV must be UTF-8: {exc}")
    reader = csv.DictReader(io.StringIO(raw))
    # Заголовок в любом регистре (Source/Target/...) — как в import-csv у TB
    field_map = {(f or "").strip().lower(): f for f in (reader.fieldnames or [])}
    if "source" not in field_map or "target" not in field_map:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="CSV template: header row with columns source,target",
        )

    added = updated = skipped = 0
    seen: dict[str, models.TMEntry] = {}  # дубль source внутри файла — см. import-tmx
    for row in reader:
        source_text = ((row.get(field_map["source"]) or "").strip())
        target_text = ((row.get(field_map["target"]) or "").strip())
        if not source_text or not target_text:
            skipped += 1
            continue
        entry = seen.get(source_text) or (
            ctx.db.query(models.TMEntry)
            .filter(models.TMEntry.tm_id == tm.id, models.TMEntry.source_text == source_text)
            .first()
        )
        if entry is None:
            entry = models.TMEntry(tm_id=tm.id, source_text=source_text, target_text=target_text)
            ctx.db.add(entry)
            added += 1
        else:
            entry.target_text = target_text
            updated += 1
        seen[source_text] = entry
    audit.audit(ctx.db, ctx.org_id, ctx.user_id, "tm.import_csv", "tm", tm.id,
                {"added": added, "updated": updated, "skipped": skipped})
    ctx.db.commit()
    return {"added": added, "updated": updated, "skipped": skipped}


_ALIGN_FORMATS = ("txt", "docx")


def _align_units(file: UploadFile) -> list[str]:
    """Единицы параллельного файла: те же сплиттеры, что при импорте документов
    (txt — непустые строки, docx — абзацы)."""
    fmt = segmentation.detect_format(file.filename or "")
    if fmt not in _ALIGN_FORMATS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Alignment files must be .txt or .docx",
        )
    try:
        segments, _ = segmentation.load_segments(fmt, file.file.read())
    except Exception as exc:  # битый docx и т.п.
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Cannot read file: {exc}")
    return [s for s in segments if s.strip()]


@router.post("/tms/{tm_id}/align", response_model=schemas.AlignProposal)
def align_files(
    tm_id: int,
    source_file: UploadFile = File(...),
    target_file: UploadFile = File(...),
    ctx: Context = Depends(get_context),
):
    """Выравнивание параллельных файлов: предложить пары «исходник-перевод»
    для наполнения TM из архива старых переводов.

    Пары НЕ записываются: подтверждение — отдельным запросом (человек
    снимает галочки с неверных пар; эвристика на якорях-числах/именах).
    """
    ctx.require_role("admin", "pm")
    tm = _tm_or_404(ctx, tm_id)
    source_units = _align_units(source_file)
    target_units = _align_units(target_file)
    if not source_units or not target_units:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Both files must contain at least one non-empty unit",
        )
    pairs = align_service.align_units(source_units, target_units)
    return {
        "pairs": pairs,
        "source_units": len(source_units),
        "target_units": len(target_units),
    }


@router.post("/tms/{tm_id}/align/confirm", response_model=schemas.AlignConfirmResult)
def align_confirm(tm_id: int, body: schemas.AlignConfirmRequest, ctx: Context = Depends(get_context)):
    """Записать подтвержденные пары выравнивания в TM (upsert, как при импорте)."""
    ctx.require_role("admin", "pm")
    tm = _tm_or_404(ctx, tm_id)
    added = updated = skipped = 0
    seen: dict[str, models.TMEntry] = {}  # autoflush=False: дубли в памяти
    for pair in body.pairs:
        source_text, target_text = pair.source.strip(), pair.target.strip()
        if not source_text or not target_text:
            skipped += 1
            continue
        entry = seen.get(source_text) or (
            ctx.db.query(models.TMEntry)
            .filter(models.TMEntry.tm_id == tm.id, models.TMEntry.source_text == source_text)
            .first()
        )
        if entry is None:
            entry = models.TMEntry(tm_id=tm.id, source_text=source_text, target_text=target_text)
            ctx.db.add(entry)
            added += 1
        else:
            entry.target_text = target_text
            updated += 1
        seen[source_text] = entry
    audit.audit(ctx.db, ctx.org_id, ctx.user_id, "tm.align_confirm", "tm", tm.id,
                {"added": added, "updated": updated, "skipped": skipped})
    ctx.db.commit()
    return {"added": added, "updated": updated, "skipped": skipped}


def _tmx_timestamp(dt: datetime) -> str:
    """TMX-формат метки времени (UTC): YYYYMMDDTHHMMSSZ."""
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ") if dt.tzinfo \
        else dt.strftime("%Y%m%dT%H%M%SZ")


@router.get("/tms/{tm_id}/export")
def export_tmx(tm_id: int, ctx: Context = Depends(get_context)):
    """Экспорт TM в TMX 1.4b (FR-12, обратимость: файл совместим с нашим же импортом)."""
    ctx.require_role("admin", "pm")
    tm = _tm_or_404(ctx, tm_id)
    entries = (
        ctx.db.query(models.TMEntry)
        .filter(models.TMEntry.tm_id == tm.id)
        .order_by(models.TMEntry.id)
        .all()
    )

    XML_NS = "http://www.w3.org/XML/1998/namespace"
    root = etree.Element("tmx", version="1.4")
    header = etree.SubElement(root, "header", {
        "creationtool": "Mini-TMS",
        "creationtoolversion": "0.2.0",
        "segtype": "sentence",
        "o-tmf": "mini-tms",
        "adminlang": "en",
        "srclang": tm.source_lang,
        "datatype": "plaintext",
        "creationdate": _tmx_timestamp(models.utcnow()),
    })
    body = etree.SubElement(root, "body")
    for entry in entries:
        tu = etree.SubElement(body, "tu", {"creationdate": _tmx_timestamp(entry.updated_at)})
        for lang, text in ((tm.source_lang, entry.source_text), (tm.target_lang, entry.target_text)):
            tuv = etree.SubElement(tu, "tuv", {f"{{{XML_NS}}}lang": lang})
            seg = etree.SubElement(tuv, "seg")
            seg.text = text
    payload = etree.tostring(
        root, xml_declaration=True, encoding="UTF-8", pretty_print=True
    )
    safe_name = tm.name.replace('"', "")
    audit.audit(ctx.db, ctx.org_id, ctx.user_id, "tm.export_tmx", "tm", tm.id, {"entries": len(entries)})
    ctx.db.commit()
    return Response(
        content=payload,
        media_type="application/x-tmx+xml",
        headers={"Content-Disposition": content_disposition(f"{safe_name}.tmx")},
    )


@router.get("/tms/{tm_id}/export-csv")
def export_csv(tm_id: int, ctx: Context = Depends(get_context)):
    """Экспорт TM в CSV — тот же шаблон, что и импорт (source,target).

    Паритет с TB: легковесный обмен табличками (Excel/Sheets) без XML.
    """
    ctx.require_role("admin", "pm")
    tm = _tm_or_404(ctx, tm_id)
    entries = (
        ctx.db.query(models.TMEntry)
        .filter(models.TMEntry.tm_id == tm.id)
        .order_by(models.TMEntry.id)
        .all()
    )
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["source", "target"])
    for entry in entries:
        writer.writerow([entry.source_text, entry.target_text])
    safe_name = tm.name.replace('"', "")
    audit.audit(ctx.db, ctx.org_id, ctx.user_id, "tm.export_csv", "tm", tm.id, {"entries": len(entries)})
    ctx.db.commit()
    # utf-8-sig: BOM нужен Excel'ю; импорт читает utf-8-sig и BOM отбрасывает
    return Response(
        content=buffer.getvalue().encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": content_disposition(f"{safe_name}.csv")},
    )


@router.delete("/tms/{tm_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_tm(tm_id: int, ctx: Context = Depends(get_context)):
    ctx.require_role("admin", "pm")
    tm = _tm_or_404(ctx, tm_id)
    ctx.db.delete(tm)
    audit.audit(ctx.db, ctx.org_id, ctx.user_id, "tm.delete", "tm", tm_id, {"name": tm.name})
    ctx.db.commit()


@router.get("/suggest", response_model=list[schemas.TMSuggestionOut])
def suggest(
    text: str = Query(min_length=1),
    source_lang: str | None = None,
    target_lang: str | None = None,
    ctx: Context = Depends(get_context),
):
    """Глобальный поиск по TM организации (используется редактором как fallback)."""
    wanted = normalize(text)
    query = ctx.db.query(models.TM, models.TMEntry).join(
        models.TMEntry, models.TMEntry.tm_id == models.TM.id
    ).filter(models.TM.org_id == ctx.org_id)
    if source_lang:
        query = query.filter(models.TM.source_lang == source_lang)
    if target_lang:
        query = query.filter(models.TM.target_lang == target_lang)
    results: list[schemas.TMSuggestionOut] = []
    exact: list[schemas.TMSuggestionOut] = []
    fuzzy: list[tuple[int, schemas.TMSuggestionOut]] = []
    for tm, entry in query.all():
        if normalize(entry.source_text) == wanted:
            exact.append(schemas.TMSuggestionOut(
                source_match=entry.source_text, target_suggestion=entry.target_text,
                match_percentage=100, tm_name=tm.name, match_type="exact"))
            continue
        # Дешевый pre-filter: схожесть не выше (max-|Δlen|)/max — Левенштейн не нужен.
        if length_bound_ratio(text, entry.source_text) < config.FUZZY_MIN_SCORE:
            continue
        score = similarity_ratio(text, entry.source_text)
        if score >= config.FUZZY_MIN_SCORE:
            fuzzy.append((score, schemas.TMSuggestionOut(
                source_match=entry.source_text, target_suggestion=entry.target_text,
                match_percentage=score, tm_name=tm.name, match_type="fuzzy")))
    exact.sort(key=lambda r: r.match_percentage, reverse=True)
    fuzzy.sort(key=lambda p: p[0], reverse=True)
    results = exact + [r for _, r in fuzzy]
    return results[: config.FUZZY_MAX_SUGGESTIONS]


@router.patch("/tms/{tm_id}/entries/{entry_id}", response_model=schemas.TMEntryOut)
def update_entry(
    tm_id: int, entry_id: int, body: schemas.TMEntryUpdate, ctx: Context = Depends(get_context)
):
    """Правка перевода записи TM (maintenance, pm/admin).

    Исходник — ключ памяти (уникален в TM) и не меняется: правится только
    перевод. updated_at обновится сам (onupdate) — запись «свежая» для
    сортировки при равном счете совпадения.
    """
    ctx.require_role("admin", "pm")
    tm = _tm_or_404(ctx, tm_id)
    entry = ctx.db.get(models.TMEntry, entry_id)
    if entry is None or entry.tm_id != tm.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="TM entry not found")
    entry.target_text = body.target_text
    audit.audit(ctx.db, ctx.org_id, ctx.user_id, "tm.entry_update", "tm_entry", entry.id, {})
    ctx.db.commit()
    ctx.db.refresh(entry)
    return entry


@router.delete("/tms/{tm_id}/entries/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_entry(tm_id: int, entry_id: int, ctx: Context = Depends(get_context)):
    """Удаление записи TM: устаревший/ошибочный перевод перестает подсказываться."""
    ctx.require_role("admin", "pm")
    tm = _tm_or_404(ctx, tm_id)
    entry = ctx.db.get(models.TMEntry, entry_id)
    if entry is None or entry.tm_id != tm.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="TM entry not found")
    ctx.db.delete(entry)
    audit.audit(ctx.db, ctx.org_id, ctx.user_id, "tm.entry_delete", "tm_entry", entry_id, {
        "tm_id": tm.id,
    })
    ctx.db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
