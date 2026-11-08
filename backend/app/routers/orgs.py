import json

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from .. import models, schemas
from ..auth import hash_password
from ..deps import Context, get_context, require_admin, require_management
from ..helpers import content_disposition, utcnow
from ..services import audit

router = APIRouter(prefix="/api", tags=["orgs"])


@router.post("/orgs", response_model=schemas.OrgOut, status_code=status.HTTP_201_CREATED)
def create_org(body: schemas.OrgCreate, ctx: Context = Depends(get_context)):
    org = models.Organization(name=body.name)
    ctx.db.add(org)
    ctx.db.flush()
    ctx.db.add(models.Membership(org_id=org.id, user_id=ctx.user_id, role="admin"))
    audit.audit(ctx.db, org.id, ctx.user_id, "org.create", "organization", org.id, {"name": body.name})
    ctx.db.commit()
    ctx.db.refresh(org)
    return org


@router.patch("/orgs/{org_id}", response_model=schemas.OrgOut)
def update_org(
    org_id: int,
    body: schemas.OrgUpdate,
    ctx: Context = Depends(require_admin),
):
    if ctx.org_id != org_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Объект принадлежит другой организации")
    ctx.org.mt_allowed = body.mt_allowed
    audit.audit(ctx.db, org_id, ctx.user_id, "org.update", "organization", org_id, body.model_dump())
    ctx.db.commit()
    ctx.db.refresh(ctx.org)
    return ctx.org


@router.get("/orgs/{org_id}/members", response_model=list[schemas.MemberOut])
def list_members(org_id: int, ctx: Context = Depends(require_management)):
    if ctx.org_id != org_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Объект принадлежит другой организации")
    members = (
        ctx.db.query(models.Membership, models.User)
        .join(models.User, models.Membership.user_id == models.User.id)
        .filter(models.Membership.org_id == org_id)
        .order_by(models.User.username)
        .all()
    )
    return [
        schemas.MemberOut(id=m.id, user=schemas.UserOut.model_validate(u), role=m.role)
        for m, u in members
    ]


@router.post("/orgs/{org_id}/members", response_model=schemas.MemberOut, status_code=status.HTTP_201_CREATED)
def add_member(org_id: int, body: schemas.MemberCreate, ctx: Context = Depends(require_admin)):
    if ctx.org_id != org_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Объект принадлежит другой организации")
    user = ctx.db.query(models.User).filter(models.User.username == body.username).first()
    if user is None:
        user = models.User(username=body.username, password_hash=hash_password(body.password))
        ctx.db.add(user)
        ctx.db.flush()
    existing = (
        ctx.db.query(models.Membership)
        .filter(models.Membership.org_id == org_id, models.Membership.user_id == user.id)
        .first()
    )
    if existing is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Пользователь уже участник")
    membership = models.Membership(org_id=org_id, user_id=user.id, role=body.role)
    ctx.db.add(membership)
    audit.audit(ctx.db, org_id, ctx.user_id, "member.add", "membership", user.id, {"role": body.role})
    ctx.db.commit()
    ctx.db.refresh(membership)
    return schemas.MemberOut(id=membership.id, user=schemas.UserOut.model_validate(user), role=membership.role)


@router.patch("/orgs/{org_id}/members/{membership_id}", response_model=schemas.MemberOut)
def update_member_role(
    org_id: int,
    membership_id: int,
    body: schemas.MemberRoleUpdate,
    ctx: Context = Depends(require_admin),
):
    if ctx.org_id != org_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Объект принадлежит другой организации")
    membership = ctx.db.get(models.Membership, membership_id)
    if membership is None or membership.org_id != org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Участие не найдено")
    membership.role = body.role
    audit.audit(
        ctx.db, org_id, ctx.user_id, "member.role_change", "membership", membership_id, {"role": body.role}
    )
    ctx.db.commit()
    user = ctx.db.get(models.User, membership.user_id)
    return schemas.MemberOut(id=membership.id, user=schemas.UserOut.model_validate(user), role=membership.role)


@router.get("/orgs/{org_id}/export")
def export_org_data(org_id: int, ctx: Context = Depends(require_management)):
    """Резервная копия данных организации (JSON, без файлов документов).

    Практика аварийного восстановления и переносимости данных: PM/admin
    выгружает полную структуру — участники, проекты, задания, сегменты,
    TM/TB, прайс-листы, LQA-разметку и комментарии. Хеши паролей не
    выгружаются; исходные файлы остаются в data/ (путь указан в документе).
    """
    if ctx.org_id != org_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Объект принадлежит другой организации")
    db = ctx.db

    def dump_tm(tm: models.TM) -> dict:
        return {
            "id": tm.id, "name": tm.name, "source_lang": tm.source_lang,
            "target_lang": tm.target_lang,
            "entries": [
                {"source": e.source_text, "target": e.target_text, "updated_at": e.updated_at.isoformat()}
                for e in tm.entries
            ],
        }

    def dump_tb(tb: models.TB) -> dict:
        return {
            "id": tb.id, "name": tb.name, "source_lang": tb.source_lang,
            "target_lang": tb.target_lang,
            "entries": [
                {"term": e.term, "translation": e.translation, "note": e.note, "status": e.status}
                for e in tb.entries
            ],
        }

    def dump_job(job: models.Job) -> dict:
        seg_ids = {s.id: s.position for s in job.segments}
        notes = (
            db.query(models.SegmentNote)
            .filter(models.SegmentNote.segment_id.in_(seg_ids))
            .order_by(models.SegmentNote.id)
            .all()
        )
        lqa_errors = (
            db.query(models.LQAError)
            .filter(models.LQAError.job_id == job.id)
            .order_by(models.LQAError.id)
            .all()
        )
        revisions = (
            db.query(models.SegmentRevision)
            .filter(models.SegmentRevision.segment_id.in_(seg_ids))
            .order_by(models.SegmentRevision.id)
            .all()
        )
        return {
            "id": job.id, "target_lang": job.target_lang, "status": job.status,
            "assignee": job.assignee.username if job.assignee else None,
            "deadline": job.deadline.isoformat() if job.deadline else None,
            "rework_comment": job.rework_comment,
            "segments": [
                {
                    "position": s.position, "source": s.source_text, "target": s.target_text,
                    "confirmed": s.confirmed, "is_mandatory": s.is_mandatory,
                    "source_words": s.source_words,
                }
                for s in job.segments
            ],
            "notes": [
                {
                    "position": seg_ids[n.segment_id], "author": n.author.username,
                    "body": n.body, "created_at": n.created_at.isoformat(),
                }
                for n in notes
            ],
            "lqa_errors": [
                {
                    "position": seg_ids[e.segment_id], "author": e.author.username,
                    "category": e.category, "severity": e.severity, "comment": e.comment,
                }
                for e in lqa_errors
            ],
            "revisions": [
                {
                    "position": seg_ids[r.segment_id], "author": r.author.username,
                    "target": r.target_text, "created_at": r.created_at.isoformat(),
                }
                for r in revisions
            ],
        }

    def dump_project(project: models.Project) -> dict:
        return {
            "id": project.id, "name": project.name,
            "source_lang": project.source_lang, "target_langs": project.target_langs,
            "deadline": project.deadline.isoformat() if project.deadline else None,
            "instructions": project.instructions, "status": project.status,
            "manager": project.manager.username,
            "documents": [
                {
                    "id": d.id, "name": d.name, "format": d.format,
                    "storage_path": d.storage_path, "processing_status": d.processing_status,
                }
                for d in db.query(models.Document).filter(models.Document.project_id == project.id)
            ],
            "resources": [
                {
                    "tm_id": r.tm_id, "tb_id": r.tb_id,
                    "use_for_search": r.use_for_search, "use_for_write": r.use_for_write,
                }
                for r in db.query(models.ProjectResource).filter(models.ProjectResource.project_id == project.id)
            ],
            "jobs": [
                dump_job(j)
                for j in db.query(models.Job).filter(models.Job.project_id == project.id).order_by(models.Job.id)
            ],
        }

    payload = {
        "format": "mini-tms-org-backup",
        "version": 1,
        "exported_at": utcnow().isoformat(),
        "organization": {"id": ctx.org.id, "name": ctx.org.name, "mt_allowed": ctx.org.mt_allowed},
        "members": [
            {"username": m.user.username, "role": m.role}
            for m in db.query(models.Membership).filter(models.Membership.org_id == org_id)
        ],
        "tms": [dump_tm(tm) for tm in db.query(models.TM).filter(models.TM.org_id == org_id)],
        "tbs": [dump_tb(tb) for tb in db.query(models.TB).filter(models.TB.org_id == org_id)],
        "rate_cards": [
            {
                "name": c.name, "source_lang": c.source_lang, "target_lang": c.target_lang,
                "currency": c.currency, "rates": c.rates, "min_charge": c.min_charge,
            }
            for c in db.query(models.RateCard).filter(models.RateCard.org_id == org_id)
        ],
        "projects": [
            dump_project(p)
            for p in db.query(models.Project).filter(models.Project.org_id == org_id).order_by(models.Project.id)
        ],
    }
    audit.audit(db, org_id, ctx.user_id, "org.export_backup", "organization", org_id, {})
    db.commit()
    filename = f"mini-tms-backup-org{org_id}.json"
    return Response(
        content=json.dumps(payload, ensure_ascii=False, indent=2),
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": content_disposition(filename)},
    )
