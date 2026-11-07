from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy.orm import Session

from .. import config, models, schemas
from ..deps import Context, get_context
from ..fuzzy import normalize, similarity_ratio
from ..helpers import can_manage
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
    q: str | None = None,
    limit: int = Query(default=100, le=500),
    ctx: Context = Depends(get_context),
):
    ctx.require_role("admin", "pm")
    tm = _tm_or_404(ctx, tm_id)
    query = ctx.db.query(models.TMEntry).filter(models.TMEntry.tm_id == tm.id)
    if q:
        needle = normalize(q)
        entries = [e for e in query.all() if needle in normalize(e.source_text)]
    else:
        entries = query.all()
    return entries[:limit]


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
        entry = (
            ctx.db.query(models.TMEntry)
            .filter(models.TMEntry.tm_id == tm.id, models.TMEntry.source_text == source_text)
            .first()
        )
        if entry is None:
            ctx.db.add(models.TMEntry(tm_id=tm.id, source_text=source_text, target_text=target_text))
            added += 1
        else:
            entry.target_text = target_text
            updated += 1
    audit.audit(ctx.db, ctx.org_id, ctx.user_id, "tm.import_tmx", "tm", tm.id,
                {"added": added, "updated": updated, "skipped": skipped})
    ctx.db.commit()
    return {"added": added, "updated": updated, "skipped": skipped}


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
        score = similarity_ratio(text, entry.source_text)
        if score >= config.FUZZY_MIN_SCORE:
            fuzzy.append((score, schemas.TMSuggestionOut(
                source_match=entry.source_text, target_suggestion=entry.target_text,
                match_percentage=score, tm_name=tm.name, match_type="fuzzy")))
    exact.sort(key=lambda r: r.match_percentage, reverse=True)
    fuzzy.sort(key=lambda p: p[0], reverse=True)
    results = exact + [r for _, r in fuzzy]
    return results[: config.FUZZY_MAX_SUGGESTIONS]
