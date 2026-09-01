"""Dreaming：比较、验证、压缩和审计 Memory 演化。"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.common.audit import record_audit
from packages.common.models import (
    DreamChange,
    DreamRun,
    Evidence,
    Memory,
    MemoryEvidence,
)
from packages.llm.provider import CandidateDraft, LLMProvider, build_llm_provider
from packages.memory.service import MemoryService


class DreamService:
    def __init__(self, db: Session, *, provider: LLMProvider | None = None) -> None:
        self.db = db
        self.memory_service = MemoryService(db)
        self.provider = provider or build_llm_provider()

    def run(
        self,
        project_id: int,
        *,
        dream_type: str = "manual",
        memory_ids: list[int] | None = None,
        progress: Callable[[int, int, str], None] | None = None,
    ) -> DreamRun:
        """比较、晋升、压缩 Memory。高置信晋升仍要求 Evidence；冲突只建关系，不自动删除。"""
        started = time.monotonic()

        def report(fraction: float, stage: str) -> None:
            if progress:
                progress(int(max(0.0, min(1.0, fraction)) * 1_000), 1_000, stage)

        run = DreamRun(
            project_id=project_id,
            dream_type=dream_type,
            status="running",
            provider=self.provider.provider,
            model=self.provider.model,
            prompt_version=self.provider.prompt_version,
            input_ids={"memory_ids": memory_ids or []},
            started_at=datetime.now(UTC),
        )
        self.db.add(run)
        self.db.flush()
        try:
            report(0.02, "读取待整理记忆")
            memories = self._load_memories(project_id, memory_ids)
            counts = {
                "promoted": 0,
                "compacted": 0,
                "conflicts": 0,
                "deprecated": 0,
                "archived": 0,
                "checked": 0,
                "synthesized": 0,
                "reconciled": 0,
                "skipped_human": 0,
                "skipped_llm": 0,
            }
            if dream_type in {"incremental", "manual", "genesis"}:
                def synthesis_progress(done: int, total: int, stage: str) -> None:
                    report(0.05 + 0.5 * done / max(total, 1), stage)

                synthesized, reconciled, skipped_human, skipped_llm = self._synthesize_topics(
                    project_id,
                    run,
                    memories,
                    dream_type,
                    progress=synthesis_progress,
                )
                counts["synthesized"] += synthesized
                counts["reconciled"] += reconciled
                counts["skipped_human"] += skipped_human
                counts["skipped_llm"] = skipped_llm
                memories = self._load_memories(project_id, memory_ids)
            memory_total = max(len(memories), 1)
            for index, memory in enumerate(memories, start=1):
                if index == 1 or index % 20 == 0:
                    report(
                        0.55 + 0.1 * index / memory_total,
                        f"校验记忆 {index}/{len(memories)}",
                    )
                counts["checked"] += 1
                if memory.status == "candidate" and self.memory_service.evidence_ids(memory.id):
                    before = self.memory_service._snapshot(memory)
                    self.memory_service.transition(
                        memory.id,
                        "tentative",
                        reason=f"{dream_type} Dream 验证到 Evidence",
                    )
                    action = "validate"
                    # Genesis 只整理初始记忆；后续 Dream 允许高置信 Candidate 自动启用。
                    if dream_type != "genesis" and memory.confidence >= 0.75:
                        self.memory_service.transition(
                            memory.id,
                            "active",
                            reason="Evidence 充分且 Confidence 达到 Active 门槛",
                        )
                        counts["promoted"] += 1
                        action = "promote"
                    self._record_change(run, memory, action, before)

            def compact_progress(done: int, total: int, stage: str) -> None:
                report(0.65 + 0.3 * done / max(total, 1), stage)

            compacted, conflicts = self._compact(
                project_id,
                run,
                memories,
                progress=compact_progress,
            )
            counts["compacted"] += compacted
            counts["conflicts"] += conflicts
            if dream_type == "full_validation":
                report(0.97, "校验已启用记忆")
                deprecated, archived = self._validate_active(memories, run)
                counts["deprecated"] += deprecated
                counts["archived"] += archived
            run.status = "succeeded"
            run.output_summary = counts
            run.finished_at = datetime.now(UTC)
            run.duration_ms = int((time.monotonic() - started) * 1000)
            report(1.0, "整理完成")
            record_audit(
                self.db,
                action="dream_run_completed",
                entity_type="dream_run",
                entity_id=run.id,
                project_id=project_id,
                reason=f"{dream_type} Dream 完成",
                after=counts,
            )
        except Exception as exc:
            run.status = "failed"
            run.error = f"{exc.__class__.__name__}: {str(exc)[:250]}"
            run.finished_at = datetime.now(UTC)
            run.duration_ms = int((time.monotonic() - started) * 1000)
            self.db.flush()
            raise
        self.db.flush()
        return run

    def revert_change(self, change_id: int, *, actor: str = "admin", reason: str) -> DreamChange:
        change = self.db.get(DreamChange, change_id)
        if not change:
            raise ValueError("Dream Change 不存在")
        if change.status == "reverted":
            return change
        if change.memory_id and change.before:
            memory = self.db.get(Memory, change.memory_id)
            if memory:
                before = change.before
                memory.status = str(before.get("status", memory.status))
                memory.confidence = float(before.get("confidence", memory.confidence))
                memory.title = str(before.get("title", memory.title))
                memory.problem = str(before.get("problem", memory.problem))
                memory.pattern = list(before.get("pattern", memory.pattern))
                memory.do_not_copy = list(before.get("do_not_copy", memory.do_not_copy))
                memory.apply_when = list(before.get("apply_when", memory.apply_when))
                memory.do_not = list(before.get("do_not", memory.do_not))
                if isinstance(before.get("implementation"), dict):
                    memory.implementation = before["implementation"]
                memory.version += 1
        change.status = "reverted"
        change.reverted_at = datetime.now(UTC)
        record_audit(
            self.db,
            action="dream_change_reverted",
            entity_type="dream_change",
            entity_id=change.id,
            actor=actor,
            reason=reason,
            before={"status": "applied"},
            after={"status": "reverted"},
        )
        self.db.flush()
        return change

    def detail(self, run_id: int) -> dict[str, Any]:
        run = self.db.get(DreamRun, run_id)
        if not run:
            raise ValueError("整理记录不存在")
        changes = self.db.scalars(
            select(DreamChange)
            .where(DreamChange.dream_run_id == run.id)
            .order_by(DreamChange.id.desc())
        ).all()
        return {
            "id": run.id,
            "project_id": run.project_id,
            "dream_type": run.dream_type,
            "status": run.status,
            "output_summary": run.output_summary,
            "error": run.error,
            "provider": run.provider,
            "model": run.model,
            "prompt_version": run.prompt_version,
            "duration_ms": run.duration_ms,
            "changes": [
                {
                    "id": change.id,
                    "memory_id": change.memory_id,
                    "action": change.action,
                    "reason": change.reason,
                    "status": change.status,
                    "before": change.before,
                    "after": change.after,
                    "evidence_ids": change.evidence_ids,
                }
                for change in changes
            ],
        }

    def _synthesize_topics(
        self,
        project_id: int,
        run: DreamRun,
        memories: list[Memory],
        dream_type: str,
        progress: Callable[[int, int, str], None] | None = None,
    ) -> tuple[int, int, int, int]:
        """按主题合成一条经验，再对账；人工改过的记忆只出记录不覆盖。"""
        grouped: dict[int | None, list[Memory]] = {}
        for memory in memories:
            if dream_type == "incremental" and memory.status not in {"candidate", "tentative"}:
                continue
            grouped.setdefault(memory.topic_id, []).append(memory)
        synthesized = 0
        reconciled = 0
        skipped_human = 0
        skipped_llm = 0
        last_llm_error = ""
        groups = [(topic_id, group) for topic_id, group in grouped.items() if group]
        total = max(len(groups), 1)
        for index, (_topic_id, group) in enumerate(groups, start=1):
            label = group[0].title if group else "主题"
            if progress:
                progress(index - 1, total, f"正在整理初始记忆 {index}/{len(groups)} · {label}")
            cluster = {
                "topic_id": _topic_id,
                "items": [self._cluster_item(item) for item in group],
            }
            try:
                draft = self.provider.synthesize_memory(cluster)
            except Exception as exc:
                skipped_llm += 1
                last_llm_error = f"{exc.__class__.__name__}: {str(exc)[:250]}"
                self._record_change(
                    run,
                    group[0],
                    "skip_llm",
                    self.memory_service._snapshot(group[0]),
                )
                continue
            if progress:
                progress(index - 1, total, f"已合成主题 {index}/{len(groups)} · {label}")
            synthesized += 1
            existing = [
                {
                    **self._memory_payload(item),
                    "id": item.id,
                    "status": item.status,
                    "human_corrected": self.memory_service.was_human_corrected(item.id),
                }
                for item in group
                if item.status in {"candidate", "tentative", "active"}
            ]
            try:
                decision = self.provider.reconcile_memories(self._draft_payload(draft), existing)
            except Exception:
                skipped_llm += 1
                continue
            if progress:
                progress(index - 1, total, f"已对账主题 {index}/{len(groups)} · {label}")
            if decision.action == "NOOP":
                if any(item.get("human_corrected") for item in existing):
                    skipped_human += 1
                continue
            keeper = self._apply_reconcile(run, group, draft, decision)
            if keeper:
                reconciled += 1
        if progress:
            progress(
                len(groups),
                total,
                f"初始记忆整理完成 · 合成 {synthesized} · 跳过 {skipped_llm}",
            )
        if skipped_llm and not synthesized:
            raise RuntimeError(last_llm_error or "整理初始记忆时 LLM 全部失败")
        return synthesized, reconciled, skipped_human, skipped_llm

    def _apply_reconcile(
        self,
        run: DreamRun,
        group: list[Memory],
        draft: CandidateDraft,
        decision: Any,
    ) -> Memory | None:
        target = None
        if decision.target_memory_id:
            target = next((item for item in group if item.id == decision.target_memory_id), None)
        if target is None:
            target = max(group, key=lambda item: (item.confidence, item.id))
        if self.memory_service.was_human_corrected(target.id):
            self._record_change(
                run,
                target,
                "propose",
                self.memory_service._snapshot(target),
            )
            return None
        before = self.memory_service._snapshot(target)
        target.title = draft.title
        target.problem = draft.problem
        target.pattern = draft.pattern
        target.implementation = draft.implementation
        target.do_not_copy = draft.do_not_copy
        target.apply_when = draft.apply_when
        target.do_not = draft.do_not
        target.confidence = draft.confidence
        target.version += 1
        if decision.action == "SUPERSEDE":
            for item in group:
                if item.id != target.id and item.status in {"candidate", "tentative", "active"}:
                    if item.status == "active":
                        self.memory_service.transition(
                            item.id,
                            "deprecated",
                            reason=f"被 Memory {target.id} 替代",
                        )
                    elif item.status == "tentative":
                        self.memory_service.transition(
                            item.id,
                            "candidate",
                            reason=f"被 Memory {target.id} 替代，退回待审",
                        )
        self._record_change(
            run,
            target,
            decision.action.lower(),
            before,
        )
        return target

    def _cluster_item(self, memory: Memory) -> dict[str, Any]:
        evidence_titles = list(
            self.db.scalars(
                select(Evidence.title)
                .join(MemoryEvidence, MemoryEvidence.evidence_id == Evidence.id)
                .where(MemoryEvidence.memory_id == memory.id)
            ).all()
        )
        return {
            "id": memory.id,
            "title": memory.title,
            "problem": memory.problem,
            "pattern": memory.pattern,
            "do_not_copy": memory.do_not_copy,
            "apply_when": memory.apply_when,
            "implementation": memory.implementation,
            "confidence": memory.confidence,
            "evidence_titles": evidence_titles,
        }

    @staticmethod
    def _draft_payload(draft: CandidateDraft) -> dict[str, Any]:
        return {
            "title": draft.title,
            "problem": draft.problem,
            "pattern": draft.pattern,
            "do_not_copy": draft.do_not_copy,
        }

    def _load_memories(self, project_id: int, memory_ids: list[int] | None) -> list[Memory]:
        statement = select(Memory).where(Memory.project_id == project_id)
        if memory_ids:
            statement = statement.where(Memory.id.in_(memory_ids))
        else:
            statement = statement.where(Memory.status.in_(["candidate", "tentative", "active"]))
        return list(self.db.scalars(statement.order_by(Memory.updated_at.asc())).all())

    def _compare_with_conflict_confirmation(
        self, left: dict[str, Any], right: dict[str, Any]
    ) -> dict[str, Any]:
        comparison = self.provider.compare_memories(left, right)
        if not comparison.get("conflict"):
            return comparison
        # 只有 medium 明确判定约束冲突时，再用 high 复核一次。
        return self.provider.compare_memories(left, right, reasoning_effort="high")

    def _compare_pairs_with_conflict_confirmation(
        self, pairs: list[tuple[dict[str, Any], dict[str, Any]]]
    ) -> list[dict[str, Any]]:
        compare_many = getattr(self.provider, "compare_memory_pairs", None)
        comparisons = (
            compare_many(pairs)
            if callable(compare_many)
            else [self.provider.compare_memories(left, right) for left, right in pairs]
        )
        if len(comparisons) != len(pairs):
            raise RuntimeError("LLM 批量比较结果数量不匹配")
        for index, comparison in enumerate(comparisons):
            if comparison.get("conflict"):
                left, right = pairs[index]
                comparisons[index] = self.provider.compare_memories(
                    left, right, reasoning_effort="high"
                )
        return comparisons

    def _compact(
        self,
        project_id: int,
        run: DreamRun,
        memories: list[Memory],
        *,
        progress: Callable[[int, int, str], None] | None = None,
    ) -> tuple[int, int]:
        """标题词重叠只是候选；是否同一 pattern 交给 LLM，避免仅凭关键词误合并。"""
        compacted = 0
        conflicts = 0
        active = [item for item in memories if item.status in {"active", "tentative"}]
        candidates: list[tuple[Memory, Memory]] = []
        for index, left in enumerate(active):
            left_terms = set(left.title.lower().split())
            for right in active[index + 1 :]:
                if right.status != left.status:
                    continue
                right_terms = set(right.title.lower().split())
                overlap = left_terms & right_terms
                if len(overlap) < 2:
                    continue
                candidates.append((left, right))
        total = max(len(candidates), 1)
        try:
            comparisons = self._compare_pairs_with_conflict_confirmation(
                [
                    (self._memory_payload(left), self._memory_payload(right))
                    for left, right in candidates
                ]
            )
        except Exception:
            comparisons = []
        for index, (left, right) in enumerate(candidates, start=1):
            if progress:
                progress(
                    index - 1,
                    total,
                    f"比较相似记忆 {index}/{len(candidates)} · {left.title} ↔ {right.title}",
                )
            if right.status != left.status:
                continue
            if index > len(comparisons):
                continue
            comparison = comparisons[index - 1]
            if progress:
                progress(index - 1, total, f"已比较相似记忆 {index}/{len(candidates)}")
            if comparison.get("conflict"):
                self._record_change(
                    run,
                    left,
                    "conflict",
                    self.memory_service._snapshot(left),
                )
                conflicts += 1
                continue
            if not comparison["same_pattern"]:
                continue
            keep, remove = (
                (left, right) if left.confidence >= right.confidence else (right, left)
            )
            before = self.memory_service._snapshot(remove)
            if remove.status == "active":
                self.memory_service.transition(
                    remove.id,
                    "deprecated",
                    reason=f"与 Memory {keep.id} 重复，Dreaming 合并",
                )
            elif remove.status == "tentative":
                self.memory_service.transition(
                    remove.id,
                    "candidate",
                    reason=f"与 Memory {keep.id} 重复，保留为候选",
                )
            self._record_change(run, remove, "compact", before, evidence_ids=[])
            compacted += 1
        if progress:
            progress(len(candidates), total, f"相似记忆比较完成 · {len(candidates)} 对")
        return compacted, conflicts

    def _validate_active(self, memories: list[Memory], run: DreamRun) -> tuple[int, int]:
        deprecated = 0
        archived = 0
        for memory in memories:
            if memory.status != "active":
                continue
            if not self.memory_service.evidence_ids(memory.id):
                before = self.memory_service._snapshot(memory)
                self.memory_service.transition(
                    memory.id,
                    "deprecated",
                    reason="Full Validation 发现 Active Memory 缺少 Evidence",
                )
                self._record_change(run, memory, "deprecate", before, evidence_ids=[])
                deprecated += 1
                continue
            if memory.confidence < 0.3:
                before = self.memory_service._snapshot(memory)
                self.memory_service.transition(
                    memory.id,
                    "deprecated",
                    reason="Full Validation 发现 Confidence 过低",
                )
                self.memory_service.transition(
                    memory.id,
                    "archived",
                    reason="低 Confidence Memory 归档",
                )
                self._record_change(run, memory, "archive", before, evidence_ids=[])
                deprecated += 1
                archived += 1
        return deprecated, archived

    @staticmethod
    def _memory_payload(memory: Memory) -> dict[str, Any]:
        return {
            "id": memory.id,
            "title": memory.title,
            "pattern": memory.pattern,
            "problem": memory.problem,
        }

    def _record_change(
        self,
        run: DreamRun,
        memory: Memory,
        action: str,
        before: dict[str, Any],
        *,
        evidence_ids: list[int] | None = None,
    ) -> DreamChange:
        change = DreamChange(
            dream_run_id=run.id,
            memory_id=memory.id,
            action=action,
            before=before,
            after=self.memory_service._snapshot(memory),
            reason=f"{run.dream_type} Dream: {action}",
            evidence_ids=evidence_ids or self.memory_service.evidence_ids(memory.id),
        )
        self.db.add(change)
        self.db.flush()
        return change
