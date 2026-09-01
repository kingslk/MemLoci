"""GitLab 历史到 Evidence/Candidate 的转换。"""

from __future__ import annotations

import re
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.common.audit import record_audit
from packages.common.models import (
    Evidence,
    EvidenceFile,
    Memory,
    MemoryEvidence,
    ReleaseChange,
    Repository,
    Topic,
)
from packages.embeddings.provider import HashEmbeddingProvider
from packages.llm.provider import (
    EXTRACT_BATCH_SIZE,
    EXTRACT_CONCURRENCY,
    CandidateDraft,
    ExtractDraft,
    LLMProvider,
    build_llm_provider,
)


class EvidenceService:
    def __init__(
        self,
        db: Session,
        *,
        llm: LLMProvider | None = None,
        embeddings: HashEmbeddingProvider | None = None,
    ) -> None:
        self.db = db
        self.llm = llm or build_llm_provider()
        self.embeddings = embeddings or HashEmbeddingProvider()

    def create_release_evidence(
        self,
        repository: Repository,
        change: ReleaseChange,
        *,
        title: str | None = None,
        summary: str = "",
        changed_files: list[dict[str, Any]] | None = None,
        importance_score: float | None = None,
    ) -> Evidence:
        """把正式分支变化落成可追溯事实。先存 Evidence，再允许 LLM 抽 Candidate。"""
        source_id = f"release-change:{change.id}"
        existing = self.db.scalar(
            select(Evidence).where(
                Evidence.repository_id == repository.id,
                Evidence.source_type == "release_change",
                Evidence.source_id == source_id,
            )
        )
        if existing:
            # 同一 ReleaseChange 只生成一条 Evidence；重复 Webhook 不得再抽 Candidate。
            return existing
        files = changed_files or []
        score = (
            importance_score if importance_score is not None else self._importance(summary, files)
        )
        evidence = Evidence(
            repository_id=repository.id,
            release_change_id=change.id,
            source_type="release_change",
            source_id=source_id,
            title=title or f"{change.source_type}: {change.after_sha[:12]}",
            summary=summary,
            url=None,
            importance_score=score,
            payload={
                "before_sha": change.before_sha,
                "after_sha": change.after_sha,
                "source_type": change.source_type,
                "changed_files": files,
            },
            embedding=self.embeddings.embed_query(
                f"{title or ''} {summary} {' '.join(str(item.get('path', '')) for item in files)}"
            ),
            embedding_provider=self.embeddings.metadata.provider,
            embedding_model=self.embeddings.metadata.model,
            embedding_dimensions=self.embeddings.metadata.dimensions,
            embedding_version=self.embeddings.metadata.version,
        )
        self.db.add(evidence)
        self.db.flush()
        for item in files:
            self.db.add(
                EvidenceFile(
                    evidence_id=evidence.id,
                    path=str(item.get("path") or ""),
                    old_path=item.get("old_path"),
                    change_type=str(item.get("status") or "modified"),
                    additions=int(item.get("additions") or 0),
                    deletions=int(item.get("deletions") or 0),
                )
            )
        return evidence

    def create_external_evidence(
        self,
        repository: Repository,
        *,
        source_type: str,
        source_id: str,
        title: str,
        summary: str,
        payload: dict[str, Any] | None = None,
        importance_score: float = 0.5,
    ) -> Evidence:
        existing = self.db.scalar(
            select(Evidence).where(
                Evidence.repository_id == repository.id,
                Evidence.source_type == source_type,
                Evidence.source_id == source_id,
            )
        )
        if existing:
            return existing
        evidence = Evidence(
            repository_id=repository.id,
            source_type=source_type,
            source_id=source_id,
            title=title,
            summary=summary,
            importance_score=importance_score,
            payload=payload or {},
            embedding=self.embeddings.embed_query(f"{title} {summary}"),
            embedding_provider=self.embeddings.metadata.provider,
            embedding_model=self.embeddings.metadata.model,
            embedding_dimensions=self.embeddings.metadata.dimensions,
            embedding_version=self.embeddings.metadata.version,
        )
        self.db.add(evidence)
        self.db.flush()
        return evidence

    def candidate_from_evidence(self, evidence: Evidence) -> Memory | None:
        """低价值变更只留 Evidence，不直接制造长期 Memory。"""

        if evidence.importance_score < 0.35 or self._has_memory(evidence):
            return None
        draft = self.llm.extract_candidate(self._evidence_input(evidence))
        if draft.implementation.get("skipped") or (
            draft.confidence <= 0 and not draft.pattern
        ):
            return None
        return self._persist_candidate(evidence, draft)

    def candidates_from_evidence(
        self,
        items: list[Evidence],
        *,
        progress: Callable[[int, int, str], None] | None = None,
        status: str = "tentative",
        batch_size: int = EXTRACT_BATCH_SIZE,
    ) -> int:
        """全量历史用：本地先滤，再按批并发抽取；结果先进入试用状态。"""

        pending = [
            item
            for item in items
            if item.importance_score >= 0.35
            and not self._has_memory(item)
            and self._has_change(item)
        ]
        if not pending:
            return 0
        size = max(batch_size, 1)
        batches = [pending[index : index + size] for index in range(0, len(pending), size)]
        drafts_by_id: dict[int, ExtractDraft] = {}
        workers = min(EXTRACT_CONCURRENCY, len(batches))

        def run_batch(batch: list[Evidence]) -> list[tuple[int, ExtractDraft]]:
            try:
                signals = self.llm.extract_signals(
                    [self._evidence_input(item) for item in batch]
                )
                return list(zip((item.id for item in batch), signals, strict=False))
            except Exception:
                recovered: list[tuple[int, ExtractDraft]] = []
                for item in batch:
                    try:
                        recovered.append(
                            (item.id, self.llm.extract_signal(self._evidence_input(item)))
                        )
                    except Exception:
                        continue
                return recovered

        finished = 0

        def mark_batch(label: str = "") -> None:
            nonlocal finished
            finished += 1
            if progress:
                suffix = f" · {label}" if label else ""
                progress(
                    min(finished * size, len(pending)),
                    len(pending),
                    f"抽取经验 {min(finished * size, len(pending))}/{len(pending)}{suffix}",
                )

        if workers <= 1:
            for batch in batches:
                drafts_by_id.update(run_batch(batch))
                mark_batch(batch[-1].title)
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(run_batch, batch) for batch in batches]
                for future in as_completed(futures):
                    try:
                        drafts_by_id.update(future.result())
                    except Exception:
                        pass
                    mark_batch()

        created = 0
        for evidence in pending:
            signal = drafts_by_id.get(evidence.id)
            if signal is None:
                try:
                    signal = self.llm.extract_signal(self._evidence_input(evidence))
                except Exception:
                    continue
            if not signal.skip and (signal.confidence > 0 or signal.signals):
                if self._persist_signal(evidence, signal, status=status):
                    created += 1
        return created

    def reconstruct_topic(self, project_id: int, evidence: Evidence) -> Topic:
        repository = self.db.get(Repository, evidence.repository_id)
        if not repository or repository.project_id != project_id:
            raise ValueError("Evidence 不属于指定 Project")
        return self._get_or_create_topic(project_id, evidence.title)

    def list_project(
        self,
        project_id: int,
        *,
        repository_id: int | None = None,
        offset: int = 0,
        limit: int = 500,
    ) -> list[Evidence]:
        statement = (
            select(Evidence)
            .join(Repository, Repository.id == Evidence.repository_id)
            .where(Repository.project_id == project_id)
            .order_by(Evidence.importance_score.desc(), Evidence.created_at.desc())
            .offset(max(offset, 0))
            .limit(limit)
        )
        if repository_id is not None:
            statement = statement.where(Evidence.repository_id == repository_id)
        return list(self.db.scalars(statement).all())

    def count_project(self, project_id: int, *, repository_id: int | None = None) -> int:
        from sqlalchemy import func

        statement = (
            select(func.count())
            .select_from(Evidence)
            .join(Repository, Repository.id == Evidence.repository_id)
            .where(Repository.project_id == project_id)
        )
        if repository_id is not None:
            statement = statement.where(Evidence.repository_id == repository_id)
        return int(self.db.scalar(statement) or 0)

    def detail(self, evidence_id: int) -> dict[str, Any]:
        evidence = self.db.get(Evidence, evidence_id)
        if not evidence:
            raise ValueError("Evidence 不存在")
        files = list(
            self.db.scalars(
                select(EvidenceFile).where(EvidenceFile.evidence_id == evidence.id)
            ).all()
        )
        memories = list(
            self.db.scalars(
                select(Memory)
                .join(MemoryEvidence, MemoryEvidence.memory_id == Memory.id)
                .where(MemoryEvidence.evidence_id == evidence.id)
            ).all()
        )
        payload = evidence.payload or {}
        return {
            "id": evidence.id,
            "repository_id": evidence.repository_id,
            "release_change_id": evidence.release_change_id,
            "source_type": evidence.source_type,
            "source_id": evidence.source_id,
            "title": evidence.title,
            "summary": evidence.summary,
            "importance_score": evidence.importance_score,
            "payload": payload,
            "diff": str(payload.get("diff") or ""),
            "files": [
                {
                    "path": item.path,
                    "old_path": item.old_path,
                    "change_type": item.change_type,
                    "additions": item.additions,
                    "deletions": item.deletions,
                }
                for item in files
            ]
            or [
                {
                    "path": str(item.get("path")),
                    "old_path": None,
                    "change_type": "modified",
                    "additions": 0,
                    "deletions": 0,
                }
                for item in payload.get("changed_files", [])
                if item.get("path")
            ],
            "memories": [
                {"id": item.id, "title": item.title, "status": item.status}
                for item in memories
            ],
        }

    def change_stories(self, project_id: int, *, limit: int = 500) -> list[dict[str, Any]]:
        """按主题词和文件重叠生成可审阅的 Change Story 候选组。"""

        evidence = self.list_project(project_id, limit=limit)
        groups: list[dict[str, Any]] = []
        for item in evidence:
            payload = item.payload or {}
            paths = {
                str(file_item.get("path"))
                for file_item in payload.get("changed_files", [])
                if file_item.get("path")
            }
            terms = set(re.findall(r"[\w-]+", f"{item.title} {item.summary}".lower()))
            matching: list[dict[str, Any]] = []
            for group in groups:
                path_overlap = paths & set(group["changed_paths"])
                term_overlap = terms & set(group["terms"])
                if path_overlap or len(term_overlap) >= 2:
                    matching.append(group)
            if not matching:
                groups.append(
                    {
                        "title": item.title,
                        "evidence_ids": [item.id],
                        "repository_ids": [item.repository_id],
                        "changed_paths": sorted(paths),
                        "terms": sorted(terms),
                        "importance_score": item.importance_score,
                    }
                )
                continue
            group = matching[0]
            group["evidence_ids"].append(item.id)
            if item.repository_id not in group["repository_ids"]:
                group["repository_ids"].append(item.repository_id)
            group["changed_paths"] = sorted(set(group["changed_paths"]) | paths)
            group["terms"] = sorted(set(group["terms"]) | terms)
            group["importance_score"] = max(group["importance_score"], item.importance_score)
        for group in groups:
            group.pop("terms", None)
            group["confidence"] = min(0.95, 0.4 + 0.1 * len(group["evidence_ids"]))
        return groups

    def _has_memory(self, evidence: Evidence) -> bool:
        return (
            self.db.scalar(
                select(Memory.id)
                .join(MemoryEvidence, MemoryEvidence.memory_id == Memory.id)
                .where(MemoryEvidence.evidence_id == evidence.id)
            )
            is not None
        )

    @staticmethod
    def _has_change(evidence: Evidence) -> bool:
        payload = evidence.payload or {}
        return bool(payload.get("diff") or payload.get("changed_files"))

    @staticmethod
    def _evidence_input(evidence: Evidence) -> dict[str, Any]:
        payload = evidence.payload or {}
        return {
            "title": evidence.title,
            "summary": evidence.summary,
            "source_type": evidence.source_type,
            "repository_id": evidence.repository_id,
            "changed_files": payload.get("changed_files", []),
            "diff": payload.get("diff") or "",
            "importance_score": evidence.importance_score,
        }

    def _persist_signal(
        self, evidence: Evidence, signal: ExtractDraft, *, status: str = "candidate"
    ) -> Memory | None:
        return self._persist_candidate(
            evidence,
            CandidateDraft(
                title=signal.title,
                problem=signal.problem,
                pattern=signal.signals,
                implementation={"summary": "", "steps": [], "validation": []},
                do_not_copy=[],
                apply_when=[],
                do_not=[],
                confidence=signal.confidence,
            ),
            status=status,
        )

    def _persist_candidate(
        self, evidence: Evidence, draft: CandidateDraft, *, status: str = "candidate"
    ) -> Memory:
        repository = self.db.get(Repository, evidence.repository_id)
        if not repository:
            raise ValueError("Evidence 所属 Repository 不存在")
        topic = self._get_or_create_topic(repository.project_id, evidence.title)
        memory = Memory(
            project_id=repository.project_id,
            repository_id=repository.id,
            topic_id=topic.id,
            title=draft.title,
            type="procedural",
            status=status,
            problem=draft.problem,
            pattern=draft.pattern,
            implementation=draft.implementation,
            do_not_copy=draft.do_not_copy,
            apply_when=draft.apply_when,
            do_not=draft.do_not,
            scope={"project_id": repository.project_id, "repositories": [repository.id]},
            confidence=draft.confidence,
            origin_repositories=[repository.id],
            embedding=self.embeddings.embed_query(
                f"{draft.title} {' '.join(draft.pattern)} {draft.problem}"
            ),
            embedding_provider=self.embeddings.metadata.provider,
            embedding_model=self.embeddings.metadata.model,
            embedding_dimensions=self.embeddings.metadata.dimensions,
            embedding_version=self.embeddings.metadata.version,
        )
        self.db.add(memory)
        self.db.flush()
        self.db.add(
            MemoryEvidence(memory_id=memory.id, evidence_id=evidence.id, role="derived_from")
        )
        record_audit(
            self.db,
            action="memory_candidate_created",
            entity_type="memory",
            entity_id=memory.id,
            project_id=repository.project_id,
            reason=(
                "由全量历史抽取并直接启用"
                if status == "active"
                else "由有知识含量的 Evidence 生成，仍需评测或人工确认"
            ),
            after={"status": memory.status, "evidence_id": evidence.id},
        )
        self.db.flush()
        return memory

    def _get_or_create_topic(self, project_id: int, title: str) -> Topic:
        key = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:200] or "general"
        topic = self.db.scalar(
            select(Topic).where(Topic.project_id == project_id, Topic.key == key)
        )
        if topic:
            return topic
        topic = Topic(project_id=project_id, key=key, name=title)
        self.db.add(topic)
        self.db.flush()
        return topic

    @staticmethod
    def _importance(summary: str, files: list[dict[str, Any]]) -> float:
        score = 0.25
        text = f"{summary} {' '.join(str(item.get('path', '')) for item in files)}".lower()
        score += min(0.35, len(files) * 0.04)
        if any(
            word in text for word in ("fix", "security", "auth", "token", "migration", "upload")
        ):
            score += 0.25
        if any(word in text for word in ("readme", "typo", "format", "lint")):
            score -= 0.2
        return min(1.0, max(0.0, score))
