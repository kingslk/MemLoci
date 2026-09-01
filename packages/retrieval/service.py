"""Project 范围的混合检索和 Agent 上下文编译。"""

from __future__ import annotations

import json
import re
import time
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from packages.code_intelligence.service import CodeQueryService
from packages.common.config import get_settings
from packages.common.models import (
    AgentQueryLog,
    AgentSession,
    Evidence,
    EvidenceFile,
    Memory,
    Project,
    Repository,
)
from packages.embeddings.provider import HashEmbeddingProvider, cosine_similarity
from packages.llm.provider import build_llm_provider
from packages.memory.service import MemoryService
from packages.retrieval.firewall import ActionFirewall

DIFF_CHAR_LIMIT = 2_500
CHANGED_FILE_LIMIT = 20
SHA_PREVIEW_LIMIT = 8


def _filter_diff(diff: str, file_path: str) -> str:
    if not file_path or not diff:
        return diff
    needle = file_path.lower()
    hunks = re.split(r"(?=^diff --git )", diff, flags=re.M)
    kept = [hunk for hunk in hunks if needle in hunk.lower()]
    return "".join(kept) if kept else diff[:DIFF_CHAR_LIMIT]


def compact_evidence_payload(
    payload: dict[str, Any] | None, *, file_path: str = ""
) -> dict[str, Any]:
    """月度 cluster 默认截断 diff/文件列表；可按路径只留相关 hunk。"""
    compact = dict(payload or {})
    diff = str(compact.get("diff") or "")
    if file_path:
        diff = _filter_diff(diff, file_path)
    if len(diff) > DIFF_CHAR_LIMIT:
        compact["diff"] = diff[:DIFF_CHAR_LIMIT] + "\n…(diff truncated, pass file_path to evidence_open)"
        compact["diff_truncated"] = True
    else:
        compact["diff"] = diff
    files = compact.get("changed_files")
    if isinstance(files, list):
        compact["changed_files_count"] = len(files)
        if file_path:
            needle = file_path.lower()
            files = [
                item
                for item in files
                if needle in str(item.get("path", "") if isinstance(item, dict) else item).lower()
            ]
        if len(files) > CHANGED_FILE_LIMIT:
            compact["changed_files"] = files[:CHANGED_FILE_LIMIT]
            compact["changed_files_omitted"] = len(files) - CHANGED_FILE_LIMIT
        else:
            compact["changed_files"] = files
    shas = compact.get("shas")
    if isinstance(shas, list) and len(shas) > SHA_PREVIEW_LIMIT:
        compact["sha_count"] = len(shas)
        compact["shas"] = shas[:SHA_PREVIEW_LIMIT]
    return compact


class RetrievalService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.memory_service = MemoryService(db)
        self.embeddings = HashEmbeddingProvider()
        self.firewall = ActionFirewall()
        self.llm = build_llm_provider()

    def memory_context(
        self,
        *,
        project_ref: str | int = "",
        repository_ref: str | int = "",
        task: str,
        files: list[str] | None = None,
        symbols: list[str] | None = None,
        session_id: str = "anonymous",
        token_budget: int = 4_000,
    ) -> dict[str, Any]:
        started = time.monotonic()
        query_text = " ".join([task, *(symbols or [])])
        project, repository, repo_weights, scope_meta = self._infer_scope(
            project_ref, repository_ref, query_text
        )
        positive_query, negative_terms = self._query_polarity(task)
        previous = self._previous_query_log(project.id, session_id)
        previous_top_id = self._previous_top_memory_id(previous) if negative_terms else None
        score_query = " ".join([positive_query, *(symbols or [])])
        query_vector = self.embeddings.embed_query(score_query)
        hinted = self._hinted_repository(project.id, repository_ref)
        gate_terms = self._salient_terms(score_query)
        if negative_terms:
            gate_terms -= negative_terms
        # 在推断出的 Project 内统一排序：按仓亲和度加权，不锁死 Agent 传入的当前工作区。
        memories = self.db.scalars(
            select(Memory).where(
                Memory.project_id == project.id,
                Memory.status.in_(["active", "tentative"]),
            )
        ).all()
        ranked: list[tuple[float, Memory, str, float, dict[str, float]]] = []
        for memory in memories:
            text = self._memory_text(memory)
            cfg = get_settings()
            title_score = self._keyword_score(
                score_query, memory.title, negative=negative_terms
            )
            body_score = self._keyword_score(score_query, text, negative=negative_terms)
            keyword_score = (
                cfg.recall_title_blend * title_score
                + (1.0 - cfg.recall_title_blend) * body_score
            )
            vector = memory.embedding or self.embeddings.embed_query(text)
            vector_score = max(0.0, cosine_similarity(query_vector, vector))
            repo_score = repo_weights.get(memory.repository_id, cfg.recall_repo_weight_min)
            status_score = cfg.recall_active_bonus if memory.status == "active" else 0.0
            score = (
                keyword_score * cfg.recall_keyword_weight
                + vector_score * cfg.recall_vector_weight
                + repo_score
                + status_score
            )
            if previous_top_id is not None and memory.id == previous_top_id:
                score *= cfg.recall_previous_top_penalty
            scores = {
                "keyword": round(keyword_score, 4),
                "vector": round(vector_score, 4),
                "repo_weight": round(repo_score, 4),
                "confidence": round(memory.confidence, 4),
                "fused": round(score, 4),
            }
            reason = (
                f"keyword={keyword_score:.2f}, "
                f"vector={vector_score:.2f}, repo_weight={repo_score:.2f}, "
                f"confidence={memory.confidence:.2f}"
            )
            gate_score = self._gate_score(gate_terms, text)
            scores["gate"] = round(gate_score, 4)
            ranked.append((score, memory, reason, gate_score, scores))
        ranked.sort(key=lambda item: item[0], reverse=True)
        active_pool = [item for item in ranked if item[1].status == "active"]
        tentative_pool = [item for item in ranked if item[1].status == "tentative"]
        active_kept, active_cutoff = self._select_relevant(active_pool)
        tentative_kept, tentative_cutoff = self._select_relevant(tentative_pool)
        pool_size = get_settings().recall_pool_size
        active = self._rerank_shortlist(
            self._deduplicate(active_kept)[:pool_size], score_query, negative_terms
        )
        tentative = self._rerank_shortlist(
            self._deduplicate(tentative_kept)[:pool_size], score_query, negative_terms
        )
        ranked = (active or tentative)[: get_settings().recall_top_k]
        recall_mode = "active" if active else "tentative_fallback" if tentative else "empty"
        cutoff_meta = active_cutoff if active else tentative_cutoff

        session = self._get_or_create_session(project.id, repository.id, session_id)
        repo_names = {
            item["id"]: item["name"]
            for item in (scope_meta.get("repository_weights") or [])
            if isinstance(item, dict) and "id" in item
        }
        searched_repo_count = len(repo_names) or 1
        results: list[dict[str, Any]] = []
        result_scores: list[dict[str, Any]] = []
        estimated_tokens = 0
        token_capped = False
        for _score, memory, reason, _signal, scores in ranked:
            evidence_ids = self.memory_service.evidence_ids(memory.id)
            is_new = memory.id not in session.seen_memories
            repo_name = repo_names.get(memory.repository_id)
            payload = {
                "id": memory.id,
                "title": memory.title,
                "status": memory.status,
                "repository_id": memory.repository_id,
                "repository_name": repo_name,
                "confidence": memory.confidence,
                "problem": memory.problem,
                "pattern": memory.pattern,
                "implementation": memory.implementation,
                "do_not_copy": memory.do_not_copy,
                "apply_when": memory.apply_when,
                "do_not": memory.do_not,
                "evidence_ids": evidence_ids,
                "why_relevant": reason,
                "is_new_in_session": is_new,
                "status_notice": self._status_notice(memory),
            }
            compiled = self.firewall.compile_memory(payload, task=task)
            if not is_new:
                compiled = {
                    "memory_id": memory.id,
                    "title": memory.title,
                    "status": memory.status,
                    "repository_id": memory.repository_id,
                    "repository_name": repo_name,
                    "confidence": memory.confidence,
                    "is_new_in_session": False,
                    "why_relevant": reason,
                    "evidence_ids": evidence_ids,
                    "expand_with": memory.id,
                    "status_notice": self._status_notice(memory),
                    "action_boundary": "本 Session 已见过完整内容，需要详情时使用 memory_expand。",
                }
            item_tokens = self._estimated_tokens(compiled)
            if estimated_tokens + item_tokens > token_budget and results:
                compiled = {
                    "memory_id": memory.id,
                    "title": memory.title,
                    "status": memory.status,
                    "repository_id": memory.repository_id,
                    "repository_name": repo_name,
                    "confidence": memory.confidence,
                    "is_new_in_session": is_new,
                    "why_relevant": reason,
                    "evidence_ids": evidence_ids,
                    "expand_with": memory.id,
                    "status_notice": self._status_notice(memory),
                    "action_boundary": "受 Token Budget 限制，使用 memory_expand 获取详情。",
                }
                item_tokens = self._estimated_tokens(compiled)
            if estimated_tokens + item_tokens > token_budget:
                token_capped = True
                break
            results.append(compiled)
            result_scores.append(
                {
                    "id": memory.id,
                    "title": memory.title,
                    "repository_id": memory.repository_id,
                    "repository_name": repo_name,
                    **scores,
                    "is_new": is_new,
                }
            )
            estimated_tokens += item_tokens
            if memory.id not in session.seen_memories:
                session.seen_memories = [*session.seen_memories, memory.id][-500:]

        hint = None
        if not results:
            hint = "没有足够相关的经验。可以补具体用词，或改走 code_search。"
        elif self._query_is_thin(positive_query):
            titles = [item["title"] for item in result_scores if item.get("title")]
            if titles:
                preview = " / ".join(str(title) for title in titles[:3])
                hint = f"问题比较短，当前更像：{preview}。请补一句更具体的，或改走 memory_compare。"
            else:
                hint = "问题比较短。请补一句更具体的，或改走 memory_compare。"

        empty_reason = None
        if not results:
            if token_capped:
                empty_reason = "token_budget"
            else:
                empty_reason = cutoff_meta.get("empty_reason") or "no_keyword_path"

        by_repo: dict[str, int] = {}
        for item in result_scores:
            name = str(item.get("repository_name") or "项目级")
            by_repo[name] = by_repo.get(name, 0) + 1
        results_by_repository = [
            {"name": name, "count": count} for name, count in by_repo.items()
        ]

        context = {
            "project": {"id": project.id, "name": project.name},
            "repository": {"id": repository.id, "name": repository.name},
            "scope": scope_meta,
            "task": task,
            "results": results,
            "returned_count": len(results),
            "searched_repository_count": searched_repo_count,
            "result_repository_count": len(by_repo),
            "results_by_repository": results_by_repository,
            "recall_mode": recall_mode,
            "token_budget": token_budget,
            "estimated_tokens": estimated_tokens,
            "levels": {
                "level_1": "当前返回压缩后的详细上下文",
                "level_2": "使用 memory_expand 查看单条详情",
                "level_3": "使用 evidence_open 查看原始 Evidence 元数据",
            },
            "scope_boundary": (
                "以上经验可能来自多个仓库。只吸收可迁移的做法，按当前任务汇总，"
                "不要整仓照搬来源架构，也不要新增用户没要求的目标。"
            ),
            "hint": hint,
        }
        self.db.add(
            AgentQueryLog(
                session_id=session_id,
                project_id=project.id,
                repository_id=repository.id,
                tool_name="memory_context",
                input_summary={
                    "task": task[:2000],
                    "files": (files or [])[:20],
                    "symbols": (symbols or [])[:20],
                    "project_ref": str(project_ref or ""),
                    "repo_ref": str(repository_ref or ""),
                    "session_id": session_id,
                    "prev_query_id": previous.id if previous is not None else None,
                },
                output_summary={
                    "memory_ids": [item["memory_id"] for item in results],
                    "recall_mode": recall_mode,
                    "scope": {
                        "hinted": scope_meta.get("hinted_repository"),
                        "primary": scope_meta.get("primary_repository"),
                        "affinities": scope_meta.get("repository_weights"),
                        "primary_switched": scope_meta.get("primary_switched"),
                        "switch_reason": scope_meta.get("switch_reason"),
                    },
                    "cutoff": {**cutoff_meta, "empty_reason": empty_reason},
                    "searched_repository_count": searched_repo_count,
                    "result_repository_count": len(by_repo),
                    "results_by_repository": results_by_repository,
                    "results": result_scores,
                    "hint": hint,
                },
                token_budget=token_budget,
                latency_ms=int((time.monotonic() - started) * 1000),
                recall_mode=recall_mode,
                primary_switched=bool(scope_meta.get("primary_switched")),
                returned_count=len(results),
            )
        )
        self.db.commit()
        return context

    def memory_expand(self, memory_id: int) -> dict[str, Any]:
        detail = self.memory_service.detail(memory_id)
        evidence = []
        for item in detail.get("evidence", []):
            compact = dict(item)
            compact["payload"] = compact_evidence_payload(item.get("payload") or {})
            compact["diff"] = compact["payload"].get("diff", "")
            evidence.append(compact)
        detail["evidence"] = evidence
        return detail

    def memory_compare(self, left_id: int, right_id: int) -> dict[str, Any]:
        left = self.memory_service.get(left_id)
        right = self.memory_service.get(right_id)
        return {
            "left": self._memory_payload(left),
            "right": self._memory_payload(right),
            "comparison": self.llm.compare_memories(
                self._memory_payload(left), self._memory_payload(right)
            ),
        }

    def evidence_open(self, evidence_id: int, file_path: str = "") -> dict[str, Any]:
        evidence = self.db.get(Evidence, evidence_id)
        if not evidence:
            raise ValueError("Evidence 不存在")
        files = self.db.scalars(
            select(EvidenceFile).where(EvidenceFile.evidence_id == evidence.id)
        ).all()
        payload = compact_evidence_payload(evidence.payload or {}, file_path=file_path)
        file_rows = [
            {
                "path": item.path,
                "old_path": item.old_path,
                "change_type": item.change_type,
                "additions": item.additions,
                "deletions": item.deletions,
            }
            for item in files
        ]
        if file_path:
            needle = file_path.lower()
            file_rows = [
                item
                for item in file_rows
                if needle in (item["path"] or "").lower()
                or needle in (item["old_path"] or "").lower()
            ]
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
            "files": file_rows,
        }

    def code_search(
        self, repository_id: int, query: str, limit: int = 10
    ) -> dict[str, object]:
        return CodeQueryService(self.db).search(repository_id, query, limit)

    def list_projects(self) -> list[dict[str, Any]]:
        projects = self.db.scalars(select(Project).order_by(Project.id)).all()
        repositories = self.db.scalars(select(Repository).order_by(Repository.id)).all()
        repos_by_project: dict[int, list[dict[str, Any]]] = {}
        for repository in repositories:
            repos_by_project.setdefault(repository.project_id, []).append(
                {"id": repository.id, "name": repository.name}
            )
        return [
            {
                "id": project.id,
                "name": project.name,
                "repositories": repos_by_project.get(project.id, []),
            }
            for project in projects
        ]

    def _infer_scope(
        self, project_ref: str | int, repository_ref: str | int, query_text: str
    ) -> tuple[Project, Repository, dict[int, float], dict[str, Any]]:
        """先判 Project。传了 repo 时默认就当主仓；没传才按仓名亲和度选。"""

        project = self._infer_project(project_ref, repository_ref, query_text)
        repositories = list(
            self.db.scalars(
                select(Repository).where(Repository.project_id == project.id).order_by(Repository.id)
            ).all()
        )
        if not repositories:
            raise ValueError(f"Project {project.name} 下没有仓库。")

        hinted = self._hinted_repository(project.id, repository_ref)
        affinities = {
            item.id: self._repo_affinity(
                query_text, item, repositories, hinted=hinted is not None and hinted.id == item.id
            )
            for item in repositories
        }
        max_affinity = max(affinities.values()) if affinities else 0.0
        cfg = get_settings()
        keep_hint = hinted is not None and cfg.recall_keep_hint_primary
        if max_affinity <= 0:
            primary = hinted or repositories[0]
            repo_weights = {
                item.id: (
                    cfg.recall_repo_weight_min + cfg.recall_repo_weight_span
                    if item.id == primary.id
                    else cfg.recall_repo_weight_min
                )
                for item in repositories
            }
        elif keep_hint:
            primary = hinted
            repo_weights = {
                item.id: cfg.recall_repo_weight_min
                + cfg.recall_repo_weight_span * (affinities[item.id] / max_affinity)
                for item in repositories
            }
            repo_weights[hinted.id] = max(repo_weights[hinted.id], cfg.recall_hint_weight_floor)
        else:
            primary = max(
                repositories,
                key=lambda item: (
                    affinities[item.id],
                    1 if hinted is not None and item.id == hinted.id else 0,
                    -item.id,
                ),
            )
            repo_weights = {
                item.id: cfg.recall_repo_weight_min
                + cfg.recall_repo_weight_span * (affinities[item.id] / max_affinity)
                for item in repositories
            }

        switched = hinted is not None and primary.id != hinted.id
        scope_meta = {
            "inferred_from": "task" if not str(repository_ref).strip() else "task_and_hint",
            "hinted_repository": (
                {"id": hinted.id, "name": hinted.name} if hinted is not None else None
            ),
            "primary_repository": {"id": primary.id, "name": primary.name},
            "allow_primary_switch": not keep_hint,
            "primary_switched": switched,
            "switch_reason": "affinity" if switched else None,
            "repository_weights": [
                {
                    "id": item.id,
                    "name": item.name,
                    "affinity": round(affinities[item.id], 3),
                    "repo_weight": round(repo_weights[item.id], 3),
                }
                for item in sorted(
                    repositories, key=lambda item: repo_weights[item.id], reverse=True
                )
            ],
        }
        return project, primary, repo_weights, scope_meta

    def _infer_project(
        self, project_ref: str | int, repository_ref: str | int, query_text: str
    ) -> Project:
        raw_project = str(project_ref or "").strip()
        raw_repo = str(repository_ref or "").strip()
        candidates = self._repository_candidates(raw_repo)
        hinted = self._lookup_project(raw_project)

        if len(candidates) == 1:
            return self._required_project(candidates[0].project_id)
        if hinted is not None:
            return hinted
        if len(candidates) > 1:
            options = ", ".join(f"{item.name} (project {item.project_id})" for item in candidates)
            raise ValueError(
                f"仓库名 {raw_repo!r} 对应多个 Project，请传入 project。候选: {options}"
            )

        projects = list(self.db.scalars(select(Project).order_by(Project.id)).all())
        if not projects:
            raise ValueError("没有可用 Project。先调用 list_projects。")
        if len(projects) == 1:
            return projects[0]

        query_terms = self._terms(query_text) | self._identifier_terms(query_text)
        scored: list[tuple[float, Project]] = []
        for project in projects:
            terms = self._identifier_terms(project.name)
            for repository in self.db.scalars(
                select(Repository).where(Repository.project_id == project.id)
            ).all():
                terms |= self._identifier_terms(repository.name)
            score = float(len(query_terms & terms))
            if project.name.lower() and project.name.lower() in query_text.lower():
                score += 3
            scored.append((score, project))
        scored.sort(key=lambda item: item[0], reverse=True)
        if scored[0][0] > 0 and (len(scored) == 1 or scored[0][0] > scored[1][0]):
            return scored[0][1]
        if raw_project:
            raise ValueError(self._missing_project_message(raw_project))
        raise ValueError("无法从问题判断 Project。" + self._missing_project_message(""))

    def _hinted_repository(self, project_id: int, repository_ref: str | int) -> Repository | None:
        raw = str(repository_ref or "").strip()
        if not raw:
            return None
        try:
            return self._resolve_repository(project_id, raw)
        except ValueError:
            return None

    @classmethod
    def _repo_affinity(
        cls,
        query_text: str,
        repository: Repository,
        siblings: list[Repository],
        *,
        hinted: bool,
    ) -> float:
        query_terms = cls._terms(query_text) | cls._identifier_terms(query_text)
        name_terms = cls._identifier_terms(repository.name)
        shared: set[str] = set()
        if len(siblings) > 1:
            shared = set.intersection(*(cls._identifier_terms(item.name) for item in siblings))
        distinctive = name_terms - shared or name_terms
        hits = query_terms & distinctive
        score = 0.0
        cfg = get_settings()
        if hits:
            score = cfg.recall_affinity_hit_base + cfg.recall_affinity_hit_span * (
                len(hits) / len(distinctive)
            )
        if hinted:
            score = max(score, cfg.recall_hint_affinity_floor)
        return score

    @staticmethod
    def _identifier_terms(name: str) -> set[str]:
        """按数字、小写、缩写、词边界切开。相邻段再拼一次，所以 i+OS → ios，不是写死 iOS。"""

        if not name.strip():
            return set()
        parts = [part.lower() for part in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", name)]
        terms = {name.lower()}
        compact = re.sub(r"[^a-z0-9]+", "", name.lower())
        if compact:
            terms.add(compact)
        terms.update(part for part in parts if len(part) > 1)
        terms.update(
            parts[index] + parts[index + 1]
            for index in range(len(parts) - 1)
            if len(parts[index] + parts[index + 1]) > 1
        )
        return terms

    def _resolve_scope(
        self, project_ref: str | int, repository_ref: str | int
    ) -> tuple[Project, Repository]:
        """用仓库名反推 Project。唯一仓库名优先，project 只是提示。"""

        raw_project = str(project_ref).strip()
        raw_repo = str(repository_ref).strip()
        candidates = self._repository_candidates(raw_repo)
        hinted = self._lookup_project(raw_project)

        if len(candidates) == 1:
            repository = candidates[0]
            owner = self._required_project(repository.project_id)
            return owner, repository

        if hinted is not None:
            return hinted, self._resolve_repository(hinted.id, raw_repo)

        if len(candidates) > 1:
            options = ", ".join(f"{item.name} (project {item.project_id})" for item in candidates)
            raise ValueError(
                f"仓库名 {raw_repo!r} 对应多个 Project，请传入 project。候选: {options}"
            )

        if raw_project:
            raise ValueError(self._missing_project_message(raw_project))
        raise ValueError("Repository 不存在，请传入精确仓库名。先调用 list_projects。")

    def _lookup_project(self, raw: str) -> Project | None:
        if not raw:
            return None
        if raw.isdigit():
            project = self.db.get(Project, int(raw))
            if project:
                return project
        return self.db.scalar(select(Project).where(Project.name == raw))

    def _required_project(self, project_id: int) -> Project:
        project = self.db.get(Project, project_id)
        if project is None:
            raise ValueError("Project 不存在")
        return project

    def _repository_candidates(self, raw: str) -> list[Repository]:
        if not raw:
            return []
        found: list[Repository] = []
        seen: set[int] = set()
        if raw.isdigit():
            by_id = self.db.get(Repository, int(raw))
            if by_id:
                found.append(by_id)
                seen.add(by_id.id)
        for item in self.db.scalars(select(Repository).where(Repository.name == raw)).all():
            if item.id not in seen:
                found.append(item)
                seen.add(item.id)
        return found

    def _resolve_project(self, project_ref: str | int) -> Project:
        raw = str(project_ref).strip()
        project = self._lookup_project(raw)
        if project:
            return project
        raise ValueError(self._missing_project_message(raw))

    def _resolve_repository(self, project_id: int, repository_ref: str | int) -> Repository:
        raw = str(repository_ref).strip()
        if raw.isdigit():
            repository = self.db.scalar(
                select(Repository).where(
                    Repository.id == int(raw), Repository.project_id == project_id
                )
            )
            if repository:
                return repository
        repository = self.db.scalar(
            select(Repository).where(Repository.name == raw, Repository.project_id == project_id)
        )
        if repository:
            return repository
        names = ", ".join(
            f"{item.id}={item.name}"
            for item in self.db.scalars(
                select(Repository)
                .where(Repository.project_id == project_id)
                .order_by(Repository.id)
            ).all()
        )
        available = names or "无"
        raise ValueError(
            f"Repository 不存在或不属于 Project: ref={raw!r}。可用 Repo: {available}。"
            "请传 MemLoci Repo id 或精确仓库名。"
        )

    def _missing_project_message(self, raw: str) -> str:
        catalog = []
        for item in self.list_projects():
            repos = ", ".join(repo["name"] for repo in item["repositories"]) or "无仓库"
            catalog.append(f"{item['id']}={item['name']} [{repos}]")
        available = "; ".join(catalog) or "无"
        return (
            f"Project 不存在: ref={raw!r}。可用 Project: {available}。"
            "请传 MemLoci Project 数字 id 或精确名称，不要传 GitLab 编号或仓库名前缀。"
        )

    def _get_or_create_session(
        self, project_id: int, repository_id: int, session_id: str
    ) -> AgentSession:
        key = self._session_key(project_id, repository_id, session_id)
        session = self.db.scalar(select(AgentSession).where(AgentSession.session_id == key))
        if session:
            return session
        session = AgentSession(
            project_id=project_id,
            repository_id=repository_id,
            session_id=key,
            seen_memories=[],
            seen_symbols=[],
            seen_evidence=[],
        )
        try:
            with self.db.begin_nested():
                self.db.add(session)
                self.db.flush()
        except IntegrityError:
            session = self.db.scalar(select(AgentSession).where(AgentSession.session_id == key))
            if session is None:
                raise
        return session

    @staticmethod
    def _session_key(project_id: int, repository_id: int, session_id: str) -> str:
        cleaned = session_id.strip() or "anonymous"
        if cleaned == "anonymous":
            return f"anonymous:{project_id}:{repository_id}"
        return cleaned

    @staticmethod
    def _memory_text(memory: Memory) -> str:
        implementation = (
            json.dumps(memory.implementation, ensure_ascii=False) if memory.implementation else ""
        )
        return " ".join(
            [memory.title, memory.problem, *memory.pattern, *memory.apply_when, implementation]
        )

    # 只忽略文件名噪声：扩展名和 index。过粗的 monorepo 目录不能打满分。
    PATH_SUFFIXES = {
        "css",
        "scss",
        "less",
        "tsx",
        "ts",
        "jsx",
        "js",
        "mjs",
        "cjs",
        "vue",
        "json",
        "md",
        "html",
        "py",
        "go",
        "rs",
        "java",
        "kt",
        "swift",
    }
    GENERIC_PATH_STEMS = {
        "app",
        "apps",
        "packages",
        "package",
        "src",
        "lib",
        "libs",
        "components",
        "component",
        "common",
        "utils",
        "hooks",
        "pages",
        "views",
        "assets",
        "public",
        "shared",
        "core",
        "index",
    }
    _NOT_A_BUT_B = re.compile(
        r"不是(?P<neg>[^，,。；;]+?)(?:，|,|。|是|而是)(?P<pos>.+)"
    )
    _DONT_TOUCH = re.compile(
        r"(?:别动|不要改|先别动|先别|都别动)\s*(?P<neg>[^，,。；;]+)|"
        r"(?P<neg_after>[^，,。；;]{1,40}?)(?:都别动|先别动|别动)"
    )
    _ONLY_CHANGE = re.compile(r"只改\s*(?P<keep>[^，,。；;]+)")

    FUNCTION_CHARS = set("了的吗呢吧着过得地啊呀么")
    STOP_TERMS = {
        "不是",
        "那个",
        "这个",
        "一下",
        "怎么",
        "会不",
        "不会",
        "能不能",
        "是否",
        "还有",
        "以及",
        "或者",
        "然后",
        "但是",
        "如果",
        "因为",
        "所以",
        "什么",
        "哪个",
        "一些",
        "可以",
        "需要",
        "应该",
        "进行",
        "有没",
        "没有",
        "是不",
    }

    @classmethod
    def _keyword_score(
        cls, query: str, text: str, *, negative: set[str] | None = None
    ) -> float:
        query_terms = cls._salient_terms(query)
        if negative:
            query_terms -= negative
        generic = cls.PATH_SUFFIXES | cls.GENERIC_PATH_STEMS
        if get_settings().recall_generic_only_empty and query_terms and query_terms <= generic:
            return 0.0
        if not query_terms:
            return 0.0
        text_terms = cls._terms(text)
        score = len(query_terms & text_terms) / len(query_terms)
        if negative and text_terms & negative:
            score *= get_settings().recall_negative_penalty
        return score

    @classmethod
    def _gate_score(cls, query_terms: set[str], text: str) -> float:
        """进袋用命中覆盖率，分母有上限，避免长口语把金标摊薄到截止以下。"""

        if not query_terms:
            return 0.0
        hits = len(query_terms & cls._terms(text))
        if not hits:
            return 0.0
        denom = max(min(len(query_terms), get_settings().recall_hit_cap), 1)
        return hits / denom

    @classmethod
    def _salient_terms(cls, text: str) -> set[str]:
        """提问侧只留有区分度的词。口语助词和扩展名不进分母，避免 Jaccard 被摊薄。"""

        terms: set[str] = set()
        generic = cls.PATH_SUFFIXES | cls.GENERIC_PATH_STEMS
        for raw in re.findall(r"[a-z0-9_./-]+|[\u4e00-\u9fff]+", text.lower()):
            if re.fullmatch(r"[a-z0-9_./-]+", raw):
                parts = [raw, *re.split(r"[/._-]", raw)]
                for part in parts:
                    if len(part) > 1 and part not in generic and part not in cls.STOP_TERMS:
                        terms.add(part)
                continue
            compact = "".join(char for char in raw if char not in cls.FUNCTION_CHARS)
            source = compact or raw
            if 2 <= len(source) <= 6:
                terms.add(source)
            if len(source) >= 2:
                terms.update(
                    source[index : index + 2]
                    for index in range(len(source) - 1)
                    if source[index : index + 2] not in cls.STOP_TERMS
                )
        return terms

    @classmethod
    def _path_score(cls, files: list[str], symbols: list[str], text: str) -> float:
        needles = [*files, *symbols]
        if not needles:
            return 0.0
        haystack = text.lower()
        hits = 0
        for item in needles:
            if cls._path_needle_hits(item, haystack):
                hits += 1
        return hits / len(needles)

    @classmethod
    def _path_needle_hits(cls, item: str, haystack: str) -> bool:
        return any(stem in haystack for stem in cls._path_needles(item))

    @classmethod
    def _path_needles(cls, item: str) -> list[str]:
        """只用路径末尾 1～2 段有区分度的名字，不把整条目录链拿去匹配。"""

        normalized = item.lower().replace("\\", "/")
        parts = [part for part in normalized.split("/") if part]
        if not parts:
            return []
        needles: list[str] = []
        for part in reversed(parts):
            stem = part.rsplit(".", 1)[0]
            if (
                len(stem) <= 2
                or stem in cls.PATH_SUFFIXES
                or stem in cls.GENERIC_PATH_STEMS
            ):
                continue
            needles.append(stem)
            if len(needles) >= 2:
                break
        return needles

    @staticmethod
    def _terms(text: str) -> set[str]:
        terms: set[str] = set()
        for raw in re.findall(r"[a-z0-9_./-]+|[\u4e00-\u9fff]+", text.lower()):
            if re.fullmatch(r"[a-z0-9_./-]+", raw):
                if len(raw) > 1:
                    terms.add(raw)
                for part in re.split(r"[/._-]", raw):
                    if len(part) > 1:
                        terms.add(part)
                continue
            if len(raw) >= 2:
                terms.add(raw)
            if len(raw) >= 2:
                terms.update(raw[index : index + 2] for index in range(len(raw) - 1))
            if len(raw) >= 3:
                terms.update(raw[index : index + 3] for index in range(len(raw) - 2))
        return terms

    @staticmethod
    def _status_notice(memory: Memory) -> str:
        if memory.status == "tentative":
            return "试用中，未经评测或人工确认，不应作为唯一依据。"
        return "已启用。"

    @staticmethod
    def _select_relevant(
        ranked: list[tuple[float, Memory, str, float, dict[str, float]]],
    ) -> tuple[list[tuple[float, Memory, str, float, dict[str, float]]], dict[str, Any]]:
        signaled = [item for item in ranked if item[3] > 0]
        if not signaled:
            return [], {
                "floor": 0.0,
                "signaled": 0,
                "dropped": len(ranked),
                "empty_reason": "no_keyword_path",
            }
        cfg = get_settings()
        floor = max(cfg.recall_signal_floor, signaled[0][3] * cfg.recall_signal_ratio)
        kept = [item for item in signaled if item[3] >= floor]
        return kept, {
            "floor": round(floor, 4),
            "signaled": len(signaled),
            "dropped": len(signaled) - len(kept),
            "empty_reason": None if kept else "below_floor",
        }

    @classmethod
    def _rerank_shortlist(
        cls,
        items: list[tuple[float, Memory, str, float, dict[str, float]]],
        query: str,
        negative: set[str],
    ) -> list[tuple[float, Memory, str, float, dict[str, float]]]:
        """进袋后只给独有提问词加一点分，不按独有词单独排，避免把金标挤出 TopK。"""

        cfg = get_settings()
        if len(items) < 2 or not cfg.recall_distinctive_rerank:
            return items
        query_terms = cls._salient_terms(query)
        if negative:
            query_terms -= negative
        if not query_terms:
            return items
        overlaps: list[set[str]] = []
        for item in items:
            memory = item[1]
            text = " ".join(
                [memory.title, memory.problem, *list(memory.apply_when or [])]
            )
            overlaps.append(query_terms & cls._salient_terms(text))
        document_freq: dict[str, int] = {}
        for overlap in overlaps:
            for term in overlap:
                document_freq[term] = document_freq.get(term, 0) + 1
        rescored: list[tuple[float, tuple[float, Memory, str, float, dict[str, float]]]] = []
        for item, overlap in zip(items, overlaps, strict=False):
            unique = sum(1 for term in overlap if document_freq.get(term, 0) == 1)
            distinctive = unique / len(query_terms)
            item[4]["distinctive"] = round(distinctive, 4)
            blended = item[0] + cfg.recall_distinctive_bonus * distinctive
            item[4]["fused"] = round(blended, 4)
            rescored.append((blended, item))
        rescored.sort(key=lambda row: row[0], reverse=True)
        return [row[1] for row in rescored]

    @staticmethod
    def _estimated_tokens(payload: dict[str, Any]) -> int:
        return max(1, len(json.dumps(payload, ensure_ascii=False)) // 4)

    @staticmethod
    def _deduplicate(
        ranked: list[tuple[float, Memory, str, float, dict[str, float]]],
    ) -> list[tuple[float, Memory, str, float, dict[str, float]]]:
        selected: list[tuple[float, Memory, str, float, dict[str, float]]] = []
        for candidate in ranked:
            candidate_terms = set(candidate[1].title.lower().split())
            if any(
                len(candidate_terms & set(item[1].title.lower().split())) >= 2 for item in selected
            ):
                continue
            selected.append(candidate)
        return selected

    @classmethod
    def _query_polarity(cls, task: str) -> tuple[str, set[str]]:
        negative: set[str] = set()
        working = task
        match = cls._NOT_A_BUT_B.search(task)
        if match:
            negative |= cls._terms(match.group("neg") or "")
            working = " ".join(
                part for part in (task[: match.start()], match.group("pos") or "", task[match.end() :]) if part
            )
        for blocked in cls._DONT_TOUCH.finditer(task):
            negative |= cls._terms(blocked.group("neg") or blocked.group("neg_after") or "")
        keep = cls._ONLY_CHANGE.search(task)
        if keep and negative:
            working = keep.group("keep") or working
        return working.strip() or task, negative

    @classmethod
    def _query_is_thin(cls, query: str) -> bool:
        """短问法才给「候选像什么」的 hint，不按业务域词表判断。"""

        if re.findall(r"[a-z0-9_]{3,}", query.lower()):
            return False
        runs = re.findall(r"[\u4e00-\u9fff]+", query)
        if not runs:
            return len(query.strip()) < 8
        return sum(len(run) for run in runs) <= 6

    def _previous_query_log(self, project_id: int, session_id: str) -> AgentQueryLog | None:
        cleaned = session_id.strip()
        if not cleaned or cleaned == "anonymous":
            return None
        return self.db.scalar(
            select(AgentQueryLog)
            .where(
                AgentQueryLog.project_id == project_id,
                AgentQueryLog.session_id == cleaned,
                AgentQueryLog.tool_name == "memory_context",
            )
            .order_by(AgentQueryLog.id.desc())
        )

    @staticmethod
    def _previous_top_memory_id(previous: AgentQueryLog | None) -> int | None:
        if previous is None:
            return None
        memory_ids = (previous.output_summary or {}).get("memory_ids") or []
        if not memory_ids:
            return None
        try:
            return int(memory_ids[0])
        except (TypeError, ValueError):
            return None

    def list_query_logs(
        self,
        project_id: int,
        *,
        recall_mode: str | None = None,
        primary_switched: bool | None = None,
        session_id: str | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        statement = select(AgentQueryLog).where(AgentQueryLog.project_id == project_id)
        if recall_mode:
            statement = statement.where(AgentQueryLog.recall_mode == recall_mode)
        if primary_switched is not None:
            statement = statement.where(AgentQueryLog.primary_switched == primary_switched)
        if session_id:
            statement = statement.where(AgentQueryLog.session_id == session_id)
        total = int(
            self.db.scalar(select(func.count()).select_from(statement.subquery())) or 0
        )
        rows = list(
            self.db.scalars(
                statement.order_by(AgentQueryLog.id.desc()).offset(max(offset, 0)).limit(limit)
            ).all()
        )
        repo_ids = {row.repository_id for row in rows if row.repository_id}
        names = {
            item.id: item.name
            for item in self.db.scalars(select(Repository).where(Repository.id.in_(repo_ids))).all()
        } if repo_ids else {}
        items = [
            {
                "id": row.id,
                "session_id": row.session_id,
                "project_id": row.project_id,
                "repository_id": row.repository_id,
                "repository_name": names.get(row.repository_id) if row.repository_id else None,
                "tool_name": row.tool_name,
                "task": (row.input_summary or {}).get("task", ""),
                "recall_mode": row.recall_mode,
                "primary_switched": row.primary_switched,
                "returned_count": row.returned_count,
                "searched_repository_count": (row.output_summary or {}).get(
                    "searched_repository_count"
                ),
                "result_repository_count": (row.output_summary or {}).get(
                    "result_repository_count"
                ),
                "results_by_repository": (row.output_summary or {}).get(
                    "results_by_repository"
                )
                or [],
                "latency_ms": row.latency_ms,
                "token_budget": row.token_budget,
                "input_summary": row.input_summary,
                "output_summary": row.output_summary,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]
        return items, total

    def clear_query_logs(self, project_id: int) -> int:
        result = self.db.execute(delete(AgentQueryLog).where(AgentQueryLog.project_id == project_id))
        return int(result.rowcount or 0)

    def _memory_payload(self, memory: Memory) -> dict[str, Any]:
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
            "evidence_ids": self.memory_service.evidence_ids(memory.id),
        }
