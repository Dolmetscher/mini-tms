"""Контекст запроса: пользователь + активная организация + роль в ней.

Активная организация передается заголовком X-Org-Id; доступ к данным
проверяется на сервере через membership (§3 ТЗ).
"""
from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from .auth import get_current_user
from .database import get_db
from .models import Membership, Organization, User

ROLE_ADMIN = "admin"
ROLE_PM = "pm"
ROLE_TRANSLATOR = "translator"
MANAGEMENT_ROLES = (ROLE_ADMIN, ROLE_PM)


@dataclass
class Context:
    db: Session
    user: User
    org: Organization
    membership: Membership

    @property
    def role(self) -> str:
        return self.membership.role

    @property
    def org_id(self) -> int:
        return self.org.id

    @property
    def user_id(self) -> int:
        return self.user.id

    def require_role(self, *roles: str) -> None:
        if self.role not in roles:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                detail=f"Требуется роль: {' или '.join(roles)}",
            )


def get_context(
    x_org_id: int | None = Header(default=None, alias="X-Org-Id"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Context:
    if x_org_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Требуется заголовок X-Org-Id")
    membership = (
        db.query(Membership)
        .filter(Membership.org_id == x_org_id, Membership.user_id == user.id)
        .first()
    )
    org = db.get(Organization, x_org_id) if membership is not None else None
    if membership is None or org is None:
        # Не раскрываем существование организации.
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Нет доступа к этой организации")
    return Context(db=db, user=user, org=org, membership=membership)


def require_management(ctx: Context = Depends(get_context)) -> Context:
    ctx.require_role(ROLE_ADMIN, ROLE_PM)
    return ctx


def require_admin(ctx: Context = Depends(get_context)) -> Context:
    ctx.require_role(ROLE_ADMIN)
    return ctx
