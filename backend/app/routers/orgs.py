from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import models, schemas
from ..auth import hash_password
from ..deps import Context, get_context, require_admin, require_management
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
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Wrong organization")
    ctx.org.mt_allowed = body.mt_allowed
    audit.audit(ctx.db, org_id, ctx.user_id, "org.update", "organization", org_id, body.model_dump())
    ctx.db.commit()
    ctx.db.refresh(ctx.org)
    return ctx.org


@router.get("/orgs/{org_id}/members", response_model=list[schemas.MemberOut])
def list_members(org_id: int, ctx: Context = Depends(require_management)):
    if ctx.org_id != org_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Wrong organization")
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
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Wrong organization")
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
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Already a member")
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
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Wrong organization")
    membership = ctx.db.get(models.Membership, membership_id)
    if membership is None or membership.org_id != org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Membership not found")
    membership.role = body.role
    audit.audit(
        ctx.db, org_id, ctx.user_id, "member.role_change", "membership", membership_id, {"role": body.role}
    )
    ctx.db.commit()
    user = ctx.db.get(models.User, membership.user_id)
    return schemas.MemberOut(id=membership.id, user=schemas.UserOut.model_validate(user), role=membership.role)
