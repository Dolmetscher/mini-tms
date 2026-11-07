import csv
import io

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from .. import models, schemas
from ..deps import Context, get_context
from ..helpers import can_manage
from ..services import audit

router = APIRouter(prefix="/api/tb", tags=["tb"])


def _tb_or_404(ctx: Context, tb_id: int) -> models.TB:
    tb = ctx.db.get(models.TB, tb_id)
    if tb is None or tb.org_id != ctx.org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="TB not found")
    return tb


@router.get("/tbs", response_model=list[schemas.TBOut])
def list_tbs(ctx: Context = Depends(get_context)):
    if not can_manage(ctx):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Requires management role")
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
    q: str | None = None,
    limit: int = Query(default=100, le=500),
    ctx: Context = Depends(get_context),
):
    ctx.require_role("admin", "pm")
    tb = _tb_or_404(ctx, tb_id)
    query = ctx.db.query(models.TBEntry).filter(models.TBEntry.tb_id == tb.id)
    entries = query.all()
    if q:
        needle = q.casefold()
        entries = [e for e in entries if needle in e.term.casefold() or needle in e.translation.casefold()]
    return entries[:limit]


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
            detail="CSV template: header row with columns term,translation,note,status",
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


@router.delete("/tbs/{tb_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_tb(tb_id: int, ctx: Context = Depends(get_context)):
    ctx.require_role("admin", "pm")
    tb = _tb_or_404(ctx, tb_id)
    ctx.db.delete(tb)
    audit.audit(ctx.db, ctx.org_id, ctx.user_id, "tb.delete", "tb", tb_id, {"name": tb.name})
    ctx.db.commit()
