"""把不同 GitLab Hook 统一为幂等 ReleaseChange。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.common.models import ReleaseChange, Repository
from packages.common.security import redact_payload


@dataclass(frozen=True)
class NormalizedReleaseChange:
    before_sha: str
    after_sha: str
    branch: str
    source_type: str
    source_event_id: str | None
    occurred_at: datetime
    payload: dict[str, Any]

    @property
    def change_key(self) -> str:
        raw = f"{self.branch}:{self.before_sha}:{self.after_sha}"
        return hashlib.sha256(raw.encode()).hexdigest()


def normalize_event(
    repository: Repository,
    payload: dict[str, Any],
    *,
    event_type: str,
    event_id: str | None = None,
) -> NormalizedReleaseChange | None:
    """只接受 release branch 的正式变化；无法确定 SHA 时留给对账任务处理。"""

    branch = repository.release_branch
    source_type = event_type.lower()
    before_sha = ""
    after_sha = ""
    occurred_at = datetime.now(UTC)

    if event_type.lower() in {"push", "push hook"} or payload.get("object_kind") == "push":
        ref = str(payload.get("ref", ""))
        if ref != f"refs/heads/{branch}":
            return None
        before_sha = str(payload.get("before") or "")
        after_sha = str(payload.get("after") or payload.get("checkout_sha") or "")
        source_type = "direct_push"
        occurred_at = _parse_time(payload.get("checkout_sha_timestamp")) or occurred_at
    elif (
        event_type.lower() in {"merge request", "merge_request", "mr"}
        or payload.get("object_kind") == "merge_request"
    ):
        attrs = payload.get("object_attributes") or {}
        target_branch = str(attrs.get("target_branch") or "")
        if target_branch != branch:
            return None
        state = str(attrs.get("state") or payload.get("state") or "")
        action = str(attrs.get("action") or "")
        if state != "merged" and action not in {"merge", "merged"}:
            return None
        before_sha = str(
            attrs.get("oldrev")
            or payload.get("before_sha")
            or attrs.get("last_commit", {}).get("id")
            or ""
        )
        after_sha = str(attrs.get("merge_commit_sha") or attrs.get("merge_commit_id") or "")
        source_type = "mr_merge"
        occurred_at = _parse_time(attrs.get("merged_at") or attrs.get("updated_at")) or occurred_at
    else:
        return None

    if not after_sha or after_sha == "0" * 40:
        return None
    return NormalizedReleaseChange(
        before_sha=before_sha,
        after_sha=after_sha,
        branch=branch,
        source_type=source_type,
        source_event_id=event_id,
        occurred_at=occurred_at,
        payload=redact_payload(payload),
    )


def persist_release_change(
    db: Session,
    repository: Repository,
    normalized: NormalizedReleaseChange,
) -> tuple[ReleaseChange, bool]:
    """按 before/after/branch 指纹去重，MR Hook 和 Push Hook 共用同一实体。"""

    existing = db.scalar(
        select(ReleaseChange).where(
            ReleaseChange.repository_id == repository.id,
            ReleaseChange.change_key == normalized.change_key,
        )
    )
    if not existing:
        # MR Hook 通常没有目标分支的 before SHA；用相同 after SHA 与 branch
        # 把它和随后到达的 Push Hook 合并，避免重复进入分析管线。
        existing = db.scalar(
            select(ReleaseChange).where(
                ReleaseChange.repository_id == repository.id,
                ReleaseChange.release_branch == normalized.branch,
                ReleaseChange.after_sha == normalized.after_sha,
            )
        )
    if existing:
        payload = dict(existing.payload or {})
        source_types = set(payload.get("source_types") or [existing.source_type])
        source_types.add(normalized.source_type)
        payload.update(normalized.payload)
        payload["source_types"] = sorted(source_types)
        existing.payload = payload
        if normalized.source_event_id and not existing.source_event_id:
            existing.source_event_id = normalized.source_event_id
        db.flush()
        return existing, False

    change = ReleaseChange(
        repository_id=repository.id,
        before_sha=normalized.before_sha,
        after_sha=normalized.after_sha,
        release_branch=normalized.branch,
        source_type=normalized.source_type,
        source_event_id=normalized.source_event_id,
        occurred_at=normalized.occurred_at,
        change_key=normalized.change_key,
        payload={
            **normalized.payload,
            "source_types": [normalized.source_type],
        },
    )
    db.add(change)
    db.flush()
    return change, True


def _parse_time(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
