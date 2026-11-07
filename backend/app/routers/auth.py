from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from ..auth import create_access_token, get_current_user, verify_password
from ..database import get_db
from ..models import Membership, Organization, User
from ..schemas import LoginRequest, MembershipOut, TokenResponse, UserOut

router = APIRouter(prefix="/api", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.username == body.username).first()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid username or password")
    memberships = (
        db.query(Membership, Organization)
        .join(Organization, Membership.org_id == Organization.id)
        .filter(Membership.user_id == user.id)
        .all()
    )
    return TokenResponse(
        access_token=create_access_token(user.username),
        user=UserOut.model_validate(user),
        memberships=[
            MembershipOut(org_id=m.org_id, org_name=org.name, role=m.role)
            for m, org in memberships
        ],
    )


@router.get("/me", response_model=TokenResponse)
def me(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    memberships = (
        db.query(Membership, Organization)
        .join(Organization, Membership.org_id == Organization.id)
        .filter(Membership.user_id == user.id)
        .all()
    )
    return TokenResponse(
        access_token="",  # /me не выдает новый токен
        user=UserOut.model_validate(user),
        memberships=[
            MembershipOut(org_id=m.org_id, org_name=org.name, role=m.role)
            for m, org in memberships
        ],
    )
