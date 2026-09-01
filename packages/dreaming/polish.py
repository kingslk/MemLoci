"""近邻打磨：只对文件和用词都撞车的记忆打模型，不预设主题。"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.common.models import (
    DreamRun,
    Evidence,
    EvidenceFile,
    Memory,
    MemoryEvidence,
)
from packages.dreaming.service import DreamService
from packages.memory.service import MemoryService

# 宁可漏合，也不因共同改过 src/common 就把无关经验送进 LLM。
MIN_FILE_SIMILARITY = 0.1
MIN_TERM_SIMILARITY = 0.1


def token_set(*parts: str) -> set[str]:
    blob = " ".join(part for part in parts if part).lower()
    tokens = {token for token in re.findall(r"[a-z0-9]{2,}", blob)}
    for run in re.findall(r"[\u4e00-\u9fff]+", blob):
        if len(run) <= 4:
            tokens.add(run)
        tokens.update(run[index : index + 2] for index in range(max(len(run) - 1, 0)))
    return tokens


def is_neighbor(
    left_files: set[str],
    left_terms: set[str],
    right_files: set[str],
    right_terms: set[str],
) -> bool:
    file_overlap = len(left_files & right_files)
    term_overlap = len(left_terms & right_terms)
    if not file_overlap or not term_overlap:
        return False
    file_similarity = file_overlap / max(len(left_files | right_files), 1)
    term_similarity = term_overlap / max(len(left_terms | right_terms), 1)
    return (
        file_similarity >= MIN_FILE_SIMILARITY
        and term_similarity >= MIN_TERM_SIMILARITY
    )


def neighbor_score(file_overlap: int, term_overlap: int) -> int:
    return file_overlap * 3 + term_overlap


class MemoryPolishService:
    def __init__(self, db: Session, *, dreams: DreamService | None = None) -> None:
        self.db = db
        self.dreams = dreams or DreamService(db)
        self.memory_service = MemoryService(db)

    def run(
        self,
        project_id: int,
        *,
        progress: Callable[[int, int, str], None] | None = None,
    ) -> dict[str, Any]:
        memories = [
            item
            for item in self.dreams._load_memories(project_id, None)
            if item.status in {"active", "tentative"}
        ]
        fingerprints = {item.id: self._fingerprint(item) for item in memories}
        pairs: list[tuple[int, Memory, Memory]] = []
        for index, left in enumerate(memories):
            left_files, left_terms = fingerprints[left.id]
            for right in memories[index + 1 :]:
                right_files, right_terms = fingerprints[right.id]
                if not is_neighbor(left_files, left_terms, right_files, right_terms):
                    continue
                score = neighbor_score(
                    len(left_files & right_files), len(left_terms & right_terms)
                )
                pairs.append((score, left, right))
        pairs.sort(key=lambda item: (-item[0], item[1].id, item[2].id))

        dream = DreamRun(
            project_id=project_id,
            dream_type="genesis",
            status="running",
            provider=self.dreams.provider.provider,
            model=self.dreams.provider.model,
            prompt_version=self.dreams.provider.prompt_version,
            started_at=datetime.now(UTC),
        )
        self.db.add(dream)
        self.db.flush()

        merged = 0
        conflicts = 0
        skipped = 0
        used: set[int] = set()
        total = max(len(pairs), 1)
        if progress:
            progress(0, total, f"扫描近邻 · {len(memories)} 条记忆 · {len(pairs)} 对候选")

        if not pairs:
            dream.status = "succeeded"
            dream.output_summary = {
                "merged": 0,
                "conflicts": 0,
                "skipped": len(memories),
                "pairs": 0,
            }
            dream.finished_at = datetime.now(UTC)
            self.db.flush()
            if progress:
                progress(1, 1, "没有需要打磨的近邻")
            return dream.output_summary

        comparable = [
            (left, right)
            for _score, left, right in pairs
            if left.status in {"active", "tentative"}
            and right.status in {"active", "tentative"}
            and not self.memory_service.was_human_corrected(left.id)
            and not self.memory_service.was_human_corrected(right.id)
        ]
        if progress:
            progress(0, total, f"等待 LLM 批量比较 · {len(comparable)} 对")
        try:
            comparison_items = self.dreams._compare_pairs_with_conflict_confirmation(
                [
                    (
                        self.dreams._memory_payload(left),
                        self.dreams._memory_payload(right),
                    )
                    for left, right in comparable
                ]
            )
        except Exception:
            comparison_items = []
        comparisons = {
            (left.id, right.id): comparison
            for (left, right), comparison in zip(
                comparable, comparison_items, strict=False
            )
        }

        for index, (_score, left, right) in enumerate(pairs, start=1):
            label = f"{left.title} ↔ {right.title}"
            if (
                left.id in used
                or right.id in used
                or left.status not in {"active", "tentative"}
                or right.status not in {"active", "tentative"}
            ):
                skipped += 1
                continue
            if self.memory_service.was_human_corrected(left.id) or (
                self.memory_service.was_human_corrected(right.id)
            ):
                skipped += 1
                continue
            comparison = comparisons.get((left.id, right.id))
            if comparison is None:
                skipped += 1
                continue
            if progress:
                progress(index - 1, total, f"已比较近邻 {index}/{len(pairs)} · {label}")
            if comparison.get("conflict"):
                self._mark_conflict(dream, left, right)
                conflicts += 1
                continue
            if not comparison.get("same_pattern"):
                skipped += 1
                continue
            keep, remove = (
                (left, right) if left.confidence >= right.confidence else (right, left)
            )
            if progress:
                progress(index - 1, total, f"等待 LLM 整理 {index}/{len(pairs)} · {label}")
            try:
                draft = self.dreams.provider.synthesize_memory(
                    {
                        "topic_id": keep.topic_id,
                        "items": [
                            self.dreams._cluster_item(keep),
                            self.dreams._cluster_item(remove),
                        ],
                    }
                )
            except Exception:
                skipped += 1
                continue
            if progress:
                progress(index - 1, total, f"已整理近邻 {index}/{len(pairs)} · {label}")
            before = self.memory_service._snapshot(keep)
            keep.title = draft.title
            keep.problem = draft.problem
            keep.pattern = draft.pattern
            keep.implementation = draft.implementation
            keep.do_not_copy = draft.do_not_copy
            keep.apply_when = draft.apply_when
            keep.do_not = draft.do_not
            keep.confidence = max(keep.confidence, draft.confidence)
            keep.version += 1
            self.dreams._record_change(dream, keep, "update", before)
            remove_before = self.memory_service._snapshot(remove)
            if remove.status == "active":
                self.memory_service.transition(
                    remove.id,
                    "deprecated",
                    reason=f"与 Memory {keep.id} 近邻重复，打磨合并",
                )
            elif remove.status == "tentative":
                self.memory_service.transition(
                    remove.id,
                    "candidate",
                    reason=f"与 Memory {keep.id} 近邻重复，退回待审",
                )
            self.dreams._record_change(dream, remove, "compact", remove_before)
            used.add(left.id)
            used.add(right.id)
            merged += 1

        summary = {
            "merged": merged,
            "conflicts": conflicts,
            "skipped": skipped,
            "pairs": len(pairs),
            "memories": len(memories),
        }
        dream.status = "succeeded"
        dream.output_summary = summary
        dream.finished_at = datetime.now(UTC)
        self.db.flush()
        if progress:
            progress(len(pairs), total, f"近邻打磨完成 · 合并 {merged} · 冲突 {conflicts}")
        return summary

    def _fingerprint(self, memory: Memory) -> tuple[set[str], set[str]]:
        files = set(
            self.db.scalars(
                select(EvidenceFile.path)
                .join(MemoryEvidence, MemoryEvidence.evidence_id == EvidenceFile.evidence_id)
                .where(MemoryEvidence.memory_id == memory.id)
            ).all()
        )
        payloads = self.db.scalars(
            select(Evidence.payload)
            .join(MemoryEvidence, MemoryEvidence.evidence_id == Evidence.id)
            .where(MemoryEvidence.memory_id == memory.id)
        ).all()
        for payload in payloads:
            for item in (payload or {}).get("changed_files", []):
                path = item.get("path") if isinstance(item, dict) else None
                if path:
                    files.add(str(path))
        terms = token_set(memory.title, memory.problem, *list(memory.pattern or []))
        return files, terms

    def _mark_conflict(self, dream: Any, left: Memory, right: Memory) -> None:
        self.dreams._record_change(
            dream,
            left,
            "conflict",
            self.memory_service._snapshot(left),
        )
