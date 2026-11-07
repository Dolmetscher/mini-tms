"""Журнал аудита (§11): назначения, смена статусов, права, операции с ресурсами."""
from sqlalchemy.orm import Session

from .. import models


def audit(
    db: Session,
    org_id: int,
    user_id: int,
    action: str,
    entity_type: str,
    entity_id: int,
    details: dict | None = None,
) -> None:
    db.add(
        models.AuditLog(
            org_id=org_id,
            user_id=user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            details=details or {},
        )
    )
