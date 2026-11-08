import csv
import io

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import Response
from sqlalchemy.orm import Session

from .. import models, schemas
from ..deps import Context, get_context
from ..formats import tbx_io
from ..helpers import can_manage, content_disposition
from ..services import audit

router = APIRouter(prefix="/api/tb", tags=["tb"])


def _tb_or_404(ctx: Context, tb_id: int) -> models.TB:
    tb = ctx.db.get(models.TB, tb_id)
    if tb is None or tb.org_id != ctx.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Термбаза (TB) не найдена")
    return tb


@router.get("/tbs", response_model=list[schemas.TBOut])
def list_tbs(ctx: Context = Depends(get_context)):
    if not can_manage(ctx):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Требуется роль менеджмента")
    tbs = ctx.db.query(models.TB).filter(models.TB.org_id == ctx.org_id).order_by(models.TB.id).all()
    return [
        schemas.TBOut(
            id=tb.id, name=tb.name, source_lang=tb.source_lang, target_lang=tb.target_lang,
            entry_count=ctx.db.query(models.TBEntry).filter(models.TBEntry.tb_id == tb.id).count(),
            created_at=tb.created_at,
        )
        for tb in tbs
    ]


@router.post("/tbs", response_model=schemas.TBOut, status_code=status.HTTP_201_CREATED)
def create_tb(body: schemas.TBCreate, ctx: Context = Depends(get_context)):
    ctx.require_role("admin", "pm")
    tb = models.TB(org_id=ctx.org_id, **body.model_dump())
    ctx.db.add(tb)
    ctx.db.flush()
    audit.audit(ctx.db, ctx.org_id, ctx.user_id, "tb.create", "tb", tb.id, {"name": body.name})
    ctx.db.commit()
    ctx.db.refresh(tb)
    return schemas.TBOut(id=tb.id, name=tb.name, source_lang=tb.source_lang,
                         target_lang=tb.target_lang, entry_count=0, created_at=tb.created_at)


@router.get("/tbs/{tb_id}/entries", response_model=list[schemas.TBEntryOut])
def list_entries(
    tb_id: int,
    response: Response,
    q: str | None = None,
    limit: int = Query(default=100, le=500),
    offset: int = Query(default=0, ge=0),
    ctx: Context = Depends(get_context),
):
    ctx.require_role("admin", "pm")
    tb = _tb_or_404(ctx, tb_id)
    query = ctx.db.query(models.TBEntry).filter(models.TBEntry.tb_id == tb.id)
    entries = query.all()
    if q:
        needle = q.casefold()
        entries = [e for e in entries if needle in e.term.casefold() or needle in e.translation.casefold()]
    response.headers["X-Total-Count"] = str(len(entries))
    return entries[offset : offset + limit]


@router.post("/tbs/{tb_id}/entries", response_model=schemas.TBEntryOut, status_code=status.HTTP_201_CREATED)
def add_entry(tb_id: int, body: schemas.TBEntryUpsert, ctx: Context = Depends(get_context)):
    ctx.require_role("admin", "pm")
    tb = _tb_or_404(ctx, tb_id)
    entry = models.TBEntry(tb_id=tb.id, **body.model_dump())
    ctx.db.add(entry)
    ctx.db.commit()
    ctx.db.refresh(entry)
    return entry


@router.post("/tbs/{tb_id}/import-csv", status_code=status.HTTP_200_OK)
def import_csv(tb_id: int, file: UploadFile = File(...), ctx: Context = Depends(get_context)):
    """Импорт CSV (FR-12). Шаблон: заголовок term,translation,note,status.

    status: preferred | admitted | forbidden (по умолчанию preferred).
    """
    ctx.require_role("admin", "pm")
    tb = _tb_or_404(ctx, tb_id)
    raw = file.file.read().decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(raw))
    expected = {"term", "translation"}
    # Заголовок принимается в любом регистре (Term/Translation/...): строим карту
    # "каноническое имя -> реальный ключ в DictReader", иначе строки молча skip-аются.
    field_map = {(f or "").strip().lower(): f for f in (reader.fieldnames or [])}
    if not expected.issubset(field_map):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Шаблон CSV: строка заголовка с колонками term,translation,note,status",
        )
    added = skipped = 0
    for row in reader:
        def column(name: str) -> str:
            return (row.get(field_map[name]) or "").strip()

        term = column("term")
        translation = column("translation")
        note = column("note") if "note" in field_map else ""
        entry_status = (column("status") if "status" in field_map else "preferred").lower()
        if not term or not translation:
            skipped += 1
            continue
        if entry_status not in ("preferred", "admitted", "forbidden"):
            entry_status = "preferred"
        ctx.db.add(models.TBEntry(tb_id=tb.id, term=term, translation=translation, note=note, status=entry_status))
        added += 1
    audit.audit(ctx.db, ctx.org_id, ctx.user_id, "tb.import_csv", "tb", tb.id, {"added": added, "skipped": skipped})
    ctx.db.commit()
    return {"added": added, "skipped": skipped}


@router.get("/tbs/{tb_id}/export")
def export_csv(tb_id: int, ctx: Context = Depends(get_context)):
    """Экспорт TB в CSV — тот же шаблон, что и импорт (term,translation,note,status)."""
    ctx.require_role("admin", "pm")
    tb = _tb_or_404(ctx, tb_id)
    entries = (
        ctx.db.query(models.TBEntry)
        .filter(models.TBEntry.tb_id == tb.id)
        .order_by(models.TBEntry.id)
        .all()
    )
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["term", "translation", "note", "status"])
    for entry in entries:
        writer.writerow([entry.term, entry.translation, entry.note, entry.status])
    safe_name = tb.name.replace('"', "")
    audit.audit(ctx.db, ctx.org_id, ctx.user_id, "tb.export_csv", "tb", tb.id, {"entries": len(entries)})
    ctx.db.commit()
    # utf-8-sig: BOM нужен Excel'ю для корректного отображения кириллицы;
    # наш импорт читает utf-8-sig и BOM отбрасывает — полный roundtrip.
    return Response(
        content=buffer.getvalue().encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": content_disposition(f"{safe_name}.csv")},
    )


@router.get("/tbs/{tb_id}/export-tbx")
def export_tbx(tb_id: int, ctx: Context = Depends(get_context)):
    """Экспорт TB в TBX-Basic (LISA TBX 1.0) — индустриальный стандарт обмена
    терминологией: matif/langSet/tig, статус — normativeAuthorization.
    """
    ctx.require_role("admin", "pm")
    tb = _tb_or_404(ctx, tb_id)
    entries = (
        ctx.db.query(models.TBEntry)
        .filter(models.TBEntry.tb_id == tb.id)
        .order_by(models.TBEntry.id)
        .all()
    )
    payload = tbx_io.export_tbx(tb.name, tb.source_lang, tb.target_lang, entries)
    safe_name = tb.name.replace('"', "")
    audit.audit(ctx.db, ctx.org_id, ctx.user_id, "tb.export_tbx", "tb", tb.id, {"entries": len(entries)})
    ctx.db.commit()
    return Response(
        content=payload,
        media_type="application/x-tbx+xml",
        headers={"Content-Disposition": content_disposition(f"{safe_name}.tbx")},
    )


@router.post("/tbs/{tb_id}/import-tbx", status_code=status.HTTP_200_OK)
def import_tbx(tb_id: int, file: UploadFile = File(...), ctx: Context = Depends(get_context)):
    """Импорт TBX (termEntry → langSet/xml:lang → tig/term). Языки матчатся по
    префиксу (en ↔ en-US); статус — termNote normativeAuthorization, примечание —
    descrip type=note. Дубликаты не контролируем (как и в CSV-импорте).
    """
    ctx.require_role("admin", "pm")
    tb = _tb_or_404(ctx, tb_id)
    raw = file.file.read()
    try:
        rows = tbx_io.parse_tbx(raw, tb.source_lang, tb.target_lang)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
    for row in rows:
        ctx.db.add(models.TBEntry(tb_id=tb.id, **row))
    audit.audit(ctx.db, ctx.org_id, ctx.user_id, "tb.import_tbx", "tb", tb.id, {"added": len(rows)})
    ctx.db.commit()
    return {"added": len(rows)}


@router.delete("/tbs/{tb_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_tb(tb_id: int, ctx: Context = Depends(get_context)):
    ctx.require_role("admin", "pm")
    tb = _tb_or_404(ctx, tb_id)
    ctx.db.delete(tb)
    audit.audit(ctx.db, ctx.org_id, ctx.user_id, "tb.delete", "tb", tb_id, {"name": tb.name})
    ctx.db.commit()


@router.patch("/tbs/{tb_id}/entries/{entry_id}", response_model=schemas.TBEntryOut)
def update_entry(
    tb_id: int, entry_id: int, body: schemas.TBEntryUpdate, ctx: Context = Depends(get_context)
):
    """Правка термина (maintenance, pm/admin): перевод, примечание, статус.

    Смена термина допускается, но не должна создавать дубль: term в базе —
    содержательный ключ поиска (QA и подсветка ищут по нему).
    """
    ctx.require_role("admin", "pm")
    tb = _tb_or_404(ctx, tb_id)
    entry = ctx.db.get(models.TBEntry, entry_id)
    if entry is None or entry.tb_id != tb.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Термин не найден")
    if body.term is not None and body.term != entry.term:
        duplicate = (
            ctx.db.query(models.TBEntry)
            .filter(models.TBEntry.tb_id == tb.id, models.TBEntry.term == body.term)
            .first()
        )
        if duplicate is not None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail=f"Термин «{body.term}» уже есть в этой термбазе",
            )
        entry.term = body.term
    if body.translation is not None:
        entry.translation = body.translation
    if body.note is not None:
        entry.note = body.note
    if body.status is not None:
        entry.status = body.status
    audit.audit(ctx.db, ctx.org_id, ctx.user_id, "tb.entry_update", "tb_entry", entry.id,
                body.model_dump(exclude_unset=True))
    ctx.db.commit()
    ctx.db.refresh(entry)
    return entry


@router.delete("/tbs/{tb_id}/entries/{entry_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_entry(tb_id: int, entry_id: int, ctx: Context = Depends(get_context)):
    """Удаление термина: например, термин выведен из употребления."""
    ctx.require_role("admin", "pm")
    tb = _tb_or_404(ctx, tb_id)
    entry = ctx.db.get(models.TBEntry, entry_id)
    if entry is None or entry.tb_id != tb.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Термин не найден")
    ctx.db.delete(entry)
    audit.audit(ctx.db, ctx.org_id, ctx.user_id, "tb.entry_delete", "tb_entry", entry_id, {
        "tb_id": tb.id,
    })
    ctx.db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
