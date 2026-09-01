"""统一审计入口。"""

from typing import Any

from sqlalchemy.orm import Session

from packages.common.models import AuditLog


def record_audit(
    db: Session,
    *,
    action: str,
    entity_type: str,
    entity_id: int | str,
    project_id: int | None = None,
    actor: str = "system",
    reason: str = "",
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> AuditLog:
    """在同一事务中记录状态变化原因，避免业务代码遗漏审计。"""

    audit = AuditLog(
        project_id=project_id,
        actor=actor,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id),
        reason=reason,
        before=before or {},
        after=after or {},
    )
    db.add(audit)
    return audit
