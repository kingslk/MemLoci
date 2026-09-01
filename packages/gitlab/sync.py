"""GitLab 分支对账、Mirror 同步和历史 Evidence 导入。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.common.models import Repository, RepositoryIgnoreRule, RepositorySyncState
from packages.evidence.service import EvidenceService
from packages.gitlab.client import GitLabClient
from packages.gitlab.clusters import build_clusters
from packages.gitlab.ignore import IgnoreMatcher
from packages.gitlab.mirror import MirrorSyncResult, ProgressCallback, RepositoryMirror
from packages.gitlab.release_changes import (
    NormalizedReleaseChange,
    persist_release_change,
)
from packages.llm.provider import SKIP_MARKERS


class RepositorySyncService:
    def __init__(
        self,
        db: Session,
        client: GitLabClient,
        mirror: RepositoryMirror,
    ) -> None:
        self.db = db
        self.client = client
        self.mirror = mirror

    def sync(
        self,
        repository: Repository,
        *,
        progress: ProgressCallback | None = None,
    ) -> MirrorSyncResult:
        state = self._state(repository.id)
        state.status = "syncing"
        state.error = None
        self.db.commit()
        last = {"stage": "", "pct": -1}

        def report(stage: str, fraction: float) -> None:
            percent = int(max(0.0, min(1.0, fraction)) * 100)
            if stage == last["stage"] and percent == last["pct"]:
                return
            last["stage"] = stage
            last["pct"] = percent
            if progress:
                progress(stage[:64], fraction)

        try:
            report("正在读取远端分支", 0.04)
            remote_sha = self.client.get_branch_sha(
                repository.gitlab_project_id, repository.release_branch
            )
            result = self.mirror.sync(
                repository.id,
                repository.clone_url,
                repository.release_branch,
                progress=lambda stage, fraction: report(stage, 0.08 + fraction * 0.84),
            )
            report("正在核对分支 SHA", 0.96)
            if result.remote_sha and result.remote_sha != remote_sha:
                # GitLab API 与 Mirror 对不上时宁可不索引，避免用过期对象生成 Code Graph。
                raise RuntimeError("GitLab branch SHA 与 Mirror Fetch 结果不一致")
            state.status = "succeeded"
            state.last_remote_sha = remote_sha
            state.last_synced_sha = result.remote_sha or remote_sha
            state.last_success_at = datetime.now(UTC)
            self.db.commit()
            report("代码镜像已同步", 1.0)
            return result
        except Exception as exc:
            state.status = "failed"
            state.error = f"{exc.__class__.__name__}: {str(exc)[:250]}"
            self.db.commit()
            raise

    def reconcile(self, repository: Repository) -> tuple[int | None, bool]:
        """Webhook 丢失时按远端 branch SHA 补出一个 Direct Push ReleaseChange。"""

        state = self._state(repository.id)
        remote_sha = self.client.get_branch_sha(
            repository.gitlab_project_id, repository.release_branch
        )
        if remote_sha == state.last_remote_sha or remote_sha == state.last_synced_sha:
            return None, False
        before_sha = state.last_synced_sha or ""
        normalized = NormalizedReleaseChange(
            before_sha=before_sha,
            after_sha=remote_sha,
            branch=repository.release_branch,
            source_type="branch_reconciliation",
            source_event_id=f"reconcile:{remote_sha}",
            occurred_at=datetime.now(UTC),
            payload={"source": "release_branch_reconciliation"},
        )
        change, created = persist_release_change(self.db, repository, normalized)
        state.last_remote_sha = remote_sha
        self.db.commit()
        return change.id, created

    def import_history(
        self,
        repository: Repository,
        *,
        limit: int | None = None,
        progress: Callable[[int, int, str], None] | None = None,
    ) -> dict[str, int]:
        """本地按模块+月份整合 diff，再对每个变更团抽一次经验。"""

        if progress:
            progress(0, 1, f"读取提交历史 · {repository.name}")
        try:
            commits = self.mirror.history(repository.id, repository.release_branch)
        except RuntimeError:
            self.sync(repository)
            commits = self.mirror.history(repository.id, repository.release_branch)
        if limit is not None:
            commits = commits[:limit]

        matcher = IgnoreMatcher(self._ignore_patterns(repository.id))
        if progress:
            progress(0, 2, f"按模块合变更 · {repository.name} · {len(commits)} 条提交")
        clusters = build_clusters(commits, matcher=matcher)
        cluster_count = max(len(clusters), 1)
        planned = cluster_count * 2
        if progress:
            progress(
                0,
                planned,
                f"已合成 {len(clusters)} 个变更团 · {repository.name}",
            )
        evidence_service = EvidenceService(self.db)
        collected = []
        for index, cluster in enumerate(clusters, start=1):
            before = self.mirror.first_parent(repository.id, cluster.shas[0])
            files = [
                item["path"]
                for item in self.mirror.changed_files(
                    repository.id, before or cluster.after_sha, cluster.after_sha
                )
                if item.get("path") and not matcher.matches(str(item["path"]))
            ]
            if cluster.files:
                allowed = set(cluster.files)
                files = [path for path in files if path in allowed] or list(cluster.files)
            if not files:
                continue
            diff = self.mirror.unified_diff(
                repository.id,
                before,
                cluster.after_sha,
                paths=files,
            )
            if not diff.strip():
                continue
            collected.append(
                evidence_service.create_external_evidence(
                    repository,
                    source_type=cluster.source_type,
                    source_id=cluster.source_id,
                    title=f"{cluster.period} {cluster.lane}",
                    summary=cluster.summary,
                    payload={
                        "changed_files": [{"path": path} for path in files],
                        "shas": list(cluster.shas),
                        "lane": cluster.lane,
                        "period": cluster.period,
                        "diff": diff,
                        "titles": cluster.summary,
                    },
                    importance_score=0.7,
                )
            )
            if index % 20 == 0:
                self.db.commit()
            if progress:
                progress(
                    index,
                    planned,
                    (
                        f"整理 diff {index}/{len(clusters)} · "
                        f"{cluster.period} {cluster.lane} · {len(cluster.shas)} 提交"
                    ),
                )
        self.db.commit()

        def extract_progress(done: int, count: int, stage: str) -> None:
            if not progress:
                return
            portion = done / max(count, 1)
            progress(cluster_count + max(1, int(portion * cluster_count)), planned, stage)

        extracted = evidence_service.candidates_from_evidence(
            collected,
            progress=extract_progress if progress else None,
            status="tentative",
        )
        self.db.commit()
        return {
            "commits": len(commits),
            "clusters": len(clusters),
            "extracted": extracted,
        }

    def _ignore_patterns(self, repository_id: int) -> list[str]:
        return list(
            self.db.scalars(
                select(RepositoryIgnoreRule.pattern)
                .where(RepositoryIgnoreRule.repository_id == repository_id)
                .order_by(RepositoryIgnoreRule.position)
            ).all()
        )

    def _state(self, repository_id: int) -> RepositorySyncState:
        state = self.db.scalar(
            select(RepositorySyncState).where(RepositorySyncState.repository_id == repository_id)
        )
        if state:
            return state
        state = RepositorySyncState(repository_id=repository_id)
        self.db.add(state)
        self.db.flush()
        return state


def _importance_from_text(text: str) -> float:
    lowered = text.lower()
    if any(marker in lowered for marker in SKIP_MARKERS):
        return 0.2
    if any(word in lowered for word in ("fix", "security", "auth", "token", "migration")):
        return 0.8
    return 0.45
