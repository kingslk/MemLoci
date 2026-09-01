"""Memory 状态机和人工纠错。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from packages.common.audit import record_audit
from packages.common.models import (
    AuditLog,
    Evidence,
    EvidenceFile,
    Memory,
    MemoryEvidence,
    Repository,
    Topic,
)
from packages.embeddings.provider import HashEmbeddingProvider

ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    # 没有 Evidence 的 candidate 不能直接 active；deprecated 可回到 active，archived 不可逆。
    "candidate": {"tentative", "rejected", "active"},
    "tentative": {"active", "rejected", "candidate"},
    "active": {"deprecated"},
    "deprecated": {"archived", "active"},
    "archived": set(),
    "rejected": {"candidate"},
}


class MemoryService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.embeddings = HashEmbeddingProvider()

    def _filtered(
        self,
        project_id: int,
        *,
        repository_id: int | None = None,
        status: str | None = None,
        topic_id: int | None = None,
        q: str | None = None,
    ):
        statement = select(Memory).where(Memory.project_id == project_id)
        if repository_id is not None:
            statement = statement.where(
                (Memory.repository_id == repository_id) | (Memory.repository_id.is_(None))
            )
        if status == "review":
            statement = statement.where(Memory.status.in_(["candidate", "tentative"]))
        elif status == "library":
            statement = statement.where(Memory.status.in_(["active", "deprecated", "archived"]))
        elif status:
            statement = statement.where(Memory.status == status)
        if topic_id is not None:
            statement = statement.where(Memory.topic_id == topic_id)
        if q:
            like = f"%{q.strip()}%"
            statement = statement.where(or_(Memory.title.ilike(like), Memory.problem.ilike(like)))
        return statement

    def list(
        self,
        project_id: int,
        *,
        repository_id: int | None = None,
        status: str | None = None,
        topic_id: int | None = None,
        q: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> list[Memory]:
        statement = self._filtered(
            project_id,
            repository_id=repository_id,
            status=status,
            topic_id=topic_id,
            q=q,
        )
        return list(
            self.db.scalars(
                statement.order_by(Memory.updated_at.desc()).offset(max(offset, 0)).limit(limit)
            ).all()
        )

    def count(
        self,
        project_id: int,
        *,
        repository_id: int | None = None,
        status: str | None = None,
        topic_id: int | None = None,
        q: str | None = None,
    ) -> int:
        statement = self._filtered(
            project_id,
            repository_id=repository_id,
            status=status,
            topic_id=topic_id,
            q=q,
        )
        return int(self.db.scalar(select(func.count()).select_from(statement.subquery())) or 0)

    def list_items(
        self,
        project_id: int,
        *,
        repository_id: int | None = None,
        status: str | None = None,
        topic_id: int | None = None,
        q: str | None = None,
        offset: int = 0,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        memories = self.list(
            project_id,
            repository_id=repository_id,
            status=status,
            topic_id=topic_id,
            q=q,
            offset=offset,
            limit=limit,
        )
        if not memories:
            return []
        memory_ids = [memory.id for memory in memories]
        counts = dict(
            self.db.execute(
                select(MemoryEvidence.memory_id, func.count())
                .where(MemoryEvidence.memory_id.in_(memory_ids))
                .group_by(MemoryEvidence.memory_id)
            ).all()
        )
        repositories = {
            item.id: item.name
            for item in self.db.scalars(
                select(Repository).where(
                    Repository.id.in_(
                        {memory.repository_id for memory in memories if memory.repository_id}
                    )
                )
            ).all()
        }
        topics = {
            item.id: item.name
            for item in self.db.scalars(
                select(Topic).where(
                    Topic.id.in_({memory.topic_id for memory in memories if memory.topic_id})
                )
            ).all()
        }
        return [
            {
                **self._snapshot(memory),
                "project_id": memory.project_id,
                "repository_id": memory.repository_id,
                "topic_id": memory.topic_id,
                "type": memory.type,
                "origin_repositories": memory.origin_repositories,
                "evidence_count": int(counts.get(memory.id, 0)),
                "repository_name": (
                    repositories.get(memory.repository_id) if memory.repository_id else None
                ),
                "topic_name": topics.get(memory.topic_id) if memory.topic_id else None,
                "updated_at": memory.updated_at.isoformat() if memory.updated_at else None,
            }
            for memory in memories
        ]

    def list_topics(self, project_id: int) -> list[dict[str, Any]]:
        topics = self.db.scalars(
            select(Topic).where(Topic.project_id == project_id).order_by(Topic.name)
        ).all()
        return [{"id": topic.id, "name": topic.name, "key": topic.key} for topic in topics]

    def was_human_corrected(self, memory_id: int) -> bool:
        return (
            self.db.scalar(
                select(AuditLog.id).where(
                    AuditLog.entity_type == "memory",
                    AuditLog.entity_id == str(memory_id),
                    AuditLog.action == "memory_corrected",
                )
            )
            is not None
        )

    def get(self, memory_id: int) -> Memory:
        memory = self.db.get(Memory, memory_id)
        if not memory:
            raise ValueError("Memory 不存在")
        return memory

    def evidence_ids(self, memory_id: int) -> list[int]:
        return list(
            self.db.scalars(
                select(MemoryEvidence.evidence_id).where(MemoryEvidence.memory_id == memory_id)
            ).all()
        )

    def transition(
        self,
        memory_id: int,
        target_status: str,
        *,
        actor: str = "system",
        reason: str,
    ) -> Memory:
        memory = self.get(memory_id)
        target_status = target_status.lower()
        if target_status not in ALLOWED_TRANSITIONS.get(memory.status, set()):
            raise ValueError(f"不允许 Memory 状态从 {memory.status} 转为 {target_status}")
        if target_status == "active" and not self.evidence_ids(memory.id):
            raise ValueError("没有 Evidence 的 Memory 不能晋升 Active")
        before = {"status": memory.status, "version": memory.version}
        memory.status = target_status
        memory.version += 1
        record_audit(
            self.db,
            action="memory_status_changed",
            entity_type="memory",
            entity_id=memory.id,
            project_id=memory.project_id,
            actor=actor,
            reason=reason,
            before=before,
            after={"status": memory.status, "version": memory.version},
        )
        self.db.flush()
        return memory

    def correct(
        self,
        memory_id: int,
        changes: dict[str, Any],
        *,
        actor: str = "admin",
        reason: str,
    ) -> Memory:
        """人工纠错必须写审计。AI 可以提议，人改的版本才覆盖对外召回。"""
        memory = self.get(memory_id)
        before = self._snapshot(memory)
        retrieval_fields = {"problem", "pattern", "apply_when"}
        refresh_embedding = bool(retrieval_fields.intersection(changes))
        if "status" in changes and changes["status"] != memory.status:
            target_status = str(changes.pop("status"))
            self.transition(memory.id, target_status, actor=actor, reason=reason)
        for field in (
            "confidence",
            "pattern",
            "do_not_copy",
            "apply_when",
            "do_not",
            "problem",
            "scope",
            "implementation",
            "title",
        ):
            if field in changes and changes[field] is not None:
                setattr(memory, field, changes[field])
        if refresh_embedding:
            memory.embedding = self.embeddings.embed_query(self._retrieval_text(memory))
            memory.embedding_provider = self.embeddings.metadata.provider
            memory.embedding_model = self.embeddings.metadata.model
            memory.embedding_dimensions = self.embeddings.metadata.dimensions
            memory.embedding_version = self.embeddings.metadata.version
        memory.version += 1
        record_audit(
            self.db,
            action="memory_corrected",
            entity_type="memory",
            entity_id=memory.id,
            project_id=memory.project_id,
            actor=actor,
            reason=reason,
            before=before,
            after=self._snapshot(memory),
        )
        self.db.flush()
        return memory

    def batch_correct(
        self,
        memory_ids: list[int],
        changes: dict[str, Any],
        *,
        reason: str,
        actor: str = "admin",
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for memory_id in memory_ids:
            try:
                memory = self.correct(memory_id, dict(changes), actor=actor, reason=reason)
                results.append({"id": memory.id, "ok": True, "status": memory.status})
            except ValueError as exc:
                results.append({"id": memory_id, "ok": False, "error": str(exc)})
        return results

    def detail(self, memory_id: int) -> dict[str, Any]:
        memory = self.get(memory_id)
        evidence = self.db.scalars(
            select(Evidence)
            .join(MemoryEvidence, MemoryEvidence.evidence_id == Evidence.id)
            .where(MemoryEvidence.memory_id == memory_id)
        ).all()
        audits = self.db.scalars(
            select(AuditLog)
            .where(
                AuditLog.entity_type == "memory",
                AuditLog.entity_id == str(memory_id),
            )
            .order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
            .limit(10)
        ).all()
        return {
            "memory": self._snapshot(memory),
            "evidence": [
                {
                    "id": item.id,
                    "source_type": item.source_type,
                    "source_id": item.source_id,
                    "title": item.title,
                    "summary": item.summary,
                    "importance_score": item.importance_score,
                    "payload": item.payload or {},
                    "diff": str((item.payload or {}).get("diff") or ""),
                    "files": list(
                        self.db.scalars(
                            select(EvidenceFile.path).where(EvidenceFile.evidence_id == item.id)
                        ).all()
                    )
                    or [
                        str(file_item.get("path"))
                        for file_item in (item.payload or {}).get("changed_files", [])
                        if file_item.get("path")
                    ],
                }
                for item in evidence
            ],
            "audits": [
                {
                    "id": item.id,
                    "action": item.action,
                    "actor": item.actor,
                    "reason": item.reason,
                    "created_at": item.created_at.isoformat(),
                    "before": item.before,
                    "after": item.after,
                }
                for item in audits
            ],
        }

    @staticmethod
    def _retrieval_text(memory: Memory) -> str:
        return " ".join([memory.title, memory.problem, *memory.pattern, *memory.apply_when])

    @staticmethod
    def _snapshot(memory: Memory) -> dict[str, Any]:
        return {
            "id": memory.id,
            "title": memory.title,
            "status": memory.status,
            "confidence": memory.confidence,
            "problem": memory.problem,
            "pattern": memory.pattern,
            "implementation": memory.implementation,
            "do_not_copy": memory.do_not_copy,
            "apply_when": memory.apply_when,
            "do_not": memory.do_not,
            "scope": memory.scope,
            "version": memory.version,
        }
