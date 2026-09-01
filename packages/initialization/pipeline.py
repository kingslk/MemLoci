"""初始化管线。

状态机和 checkpoint 在 InitializationService；本模块只执行 GitLab/Code 副作用。
近邻打磨不在初始化里跑，成功后由 memory_polish 任务接手。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.code_intelligence.service import CodeIndexer
from packages.common.config import get_settings
from packages.common.jobs import JobCancelled, raise_if_cancelled
from packages.common.models import ArchitectureEpoch, CodeFile, Job, Repository
from packages.dreaming.polish import MemoryPolishService
from packages.evidence.service import EvidenceService
from packages.gitlab.client import GitLabClient
from packages.gitlab.mirror import RepositoryMirror
from packages.gitlab.sync import RepositorySyncService
from packages.initialization.service import INITIALIZATION_STAGES, InitializationService


class InitializationPipeline:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.settings = get_settings()

    def run(self, job_id: int) -> Job:
        return InitializationService(self.db).run(job_id, stage_runner=self.run_stage)

    def run_stage(self, job: Job, stage: str) -> dict[str, Any]:
        repositories = self._repositories(job)
        if stage == "current_state_scan":
            return self._current_state_scan(job, repositories)
        if stage == "full_history_scan":
            return self._full_history_scan(job, repositories)
        if stage == "architecture_epochs":
            return self._architecture_epochs(job.project_id, repositories)
        if stage == "topic_reconstruction":
            return self._topic_reconstruction(job.project_id)
        raise ValueError(f"未知初始化阶段: {stage}")

    def run_polish(self, job_id: int) -> Job:
        job = self.db.get(Job, job_id)
        if not job:
            raise ValueError("Job 不存在")
        if job.status in {"cancelled", "succeeded"}:
            return job
        if job.status == "cancel_requested":
            from packages.common.jobs import request_cancel

            return request_cancel(self.db, job)
        if job.project_id is None:
            raise ValueError("打磨任务缺少 project_id")
        job.status = "running"
        job.started_at = job.started_at or datetime.now(UTC)
        job.current_stage = "扫描近邻记忆"
        self.db.commit()

        def on_progress(done: int, count: int, stage: str) -> None:
            current = self.db.get(Job, job_id)
            if not current:
                return
            raise_if_cancelled(self.db, current)
            current.current_stage = stage
            current.progress = max(float(current.progress or 0.0), done / max(count, 1))
            checkpoint = dict(current.checkpoint or {})
            checkpoint["detail"] = stage
            checkpoint["completed"] = done
            checkpoint["total"] = count
            current.checkpoint = checkpoint
            self.db.commit()

        try:
            summary = MemoryPolishService(self.db).run(job.project_id, progress=on_progress)
            self.db.refresh(job)
            if job.status in {"cancelled", "cancel_requested"}:
                job.status = "cancelled"
                job.finished_at = datetime.now(UTC)
                self.db.commit()
                return job
            job.status = "succeeded"
            job.current_stage = "近邻打磨完成"
            job.progress = 1.0
            job.finished_at = datetime.now(UTC)
            checkpoint = dict(job.checkpoint or {})
            checkpoint["result"] = summary
            job.checkpoint = checkpoint
            self.db.commit()
            return job
        except JobCancelled:
            self.db.refresh(job)
            job.status = "cancelled"
            job.finished_at = job.finished_at or datetime.now(UTC)
            self.db.commit()
            return job
        except Exception as exc:
            self.db.refresh(job)
            if job.status in {"cancelled", "cancel_requested"}:
                job.status = "cancelled"
                job.finished_at = job.finished_at or datetime.now(UTC)
                self.db.commit()
                return job
            job.status = "failed"
            job.error = f"{exc.__class__.__name__}: {str(exc)[:250]}"
            self.db.commit()
            raise

    def _current_state_scan(
        self, job: Job, repositories: list[Repository]
    ) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        total = max(len(repositories), 1)
        for index, repository in enumerate(repositories):
            self._report(job, f"同步代码仓库 {repository.name}", index / total)
            with GitLabClient(
                self.settings.gitlab_base_url,
                self.settings.gitlab_token,
                verify=self.settings.gitlab_ssl_verify,
            ) as client:
                mirror = RepositoryMirror(
                    self.settings.mirror_root,
                    token=self.settings.gitlab_token,
                    ssl_verify=self.settings.gitlab_ssl_verify,
                )
                sync_result = RepositorySyncService(self.db, client, mirror).sync(
                    repository,
                    progress=lambda stage, fraction, current=repository, offset=index: self._report(
                        job,
                        f"{stage} · {current.name}",
                        (offset + fraction * 0.8) / total,
                    ),
                )
                self._report(job, f"索引代码 {repository.name}", (index + 0.85) / total)
                code_result = CodeIndexer(self.db, mirror).index_snapshot(
                    repository,
                    sha=sync_result.remote_sha or "",
                    ignore_patterns=self._ignore_patterns(repository.id),
                )
            results.append(
                {
                    "repository_id": repository.id,
                    "sha": sync_result.remote_sha,
                    "code": code_result,
                }
            )
            self._report(job, f"已同步 {repository.name}", (index + 1) / total)
        return {"repositories": results}

    def _full_history_scan(
        self, job: Job, repositories: list[Repository]
    ) -> dict[str, Any]:
        results: list[dict[str, Any]] = []
        total = max(len(repositories), 1)
        for index, repository in enumerate(repositories):
            with GitLabClient(
                self.settings.gitlab_base_url,
                self.settings.gitlab_token,
                verify=self.settings.gitlab_ssl_verify,
            ) as client:
                mirror = RepositoryMirror(
                    self.settings.mirror_root,
                    token=self.settings.gitlab_token,
                    ssl_verify=self.settings.gitlab_ssl_verify,
                )
                def on_history(
                    done: int,
                    count: int,
                    stage: str,
                    current=repository,
                    offset=index,
                ) -> None:
                    self._report(
                        job,
                        f"{stage} · {current.name}",
                        (offset + done / max(count, 1)) / total,
                        completed=done,
                        total=count,
                    )

                result = RepositorySyncService(self.db, client, mirror).import_history(
                    repository,
                    progress=on_history,
                )
            results.append({"repository_id": repository.id, **result})
        return {"repositories": results}

    def _report(
        self,
        job: Job,
        stage: str,
        stage_fraction: float,
        *,
        completed: int | None = None,
        total: int | None = None,
    ) -> None:
        raise_if_cancelled(self.db, job)
        done_stages = len((job.checkpoint or {}).get("completed_stages", []))
        checkpoint = dict(job.checkpoint or {})
        checkpoint["detail"] = stage
        if completed is not None:
            checkpoint["completed"] = completed
        if total is not None:
            checkpoint["total"] = total
        job.checkpoint = checkpoint
        job.current_stage = stage
        computed = (done_stages + max(0.0, min(1.0, stage_fraction))) / len(
            INITIALIZATION_STAGES
        )
        # 同一阶段里子步骤换计数器时不得回退，否则会出现「分析完 diff 再抽取」进度变小。
        job.progress = max(float(job.progress or 0.0), computed)
        self.db.commit()

    def _architecture_epochs(
        self, project_id: int | None, repositories: list[Repository]
    ) -> dict[str, Any]:
        if project_id is None:
            raise ValueError("初始化 Job 缺少 project_id")
        created: list[int] = []
        for repository in repositories:
            languages = sorted(
                set(
                    self.db.scalars(
                        select(CodeFile.language).where(CodeFile.repository_id == repository.id)
                    ).all()
                )
                - {"unknown"}
            )
            existing = self.db.scalar(
                select(ArchitectureEpoch).where(
                    ArchitectureEpoch.project_id == project_id,
                    ArchitectureEpoch.repository_id == repository.id,
                    ArchitectureEpoch.end_date.is_(None),
                )
            )
            if existing:
                existing.technologies = languages
                existing.updated_at = datetime.now(UTC)
                continue
            epoch = ArchitectureEpoch(
                project_id=project_id,
                repository_id=repository.id,
                name=f"current-{repository.name}",
                start_date=datetime.now(UTC),
                technologies=languages,
                confidence=0.5,
            )
            self.db.add(epoch)
            self.db.flush()
            created.append(epoch.id)
        self.db.commit()
        return {"created_epoch_ids": created}

    def _topic_reconstruction(self, project_id: int | None) -> dict[str, Any]:
        if project_id is None:
            raise ValueError("初始化 Job 缺少 project_id")
        service = EvidenceService(self.db)
        stories = service.change_stories(project_id)
        topics = []
        for story in stories:
            topic = service._get_or_create_topic(project_id, story["title"])
            topics.append({"topic_id": topic.id, "evidence_ids": story["evidence_ids"]})
        self.db.commit()
        return {"stories": len(stories), "topics": topics}

    def _repositories(self, job: Job) -> list[Repository]:
        statement = select(Repository).where(Repository.project_id == job.project_id)
        if job.repository_id is not None:
            statement = statement.where(Repository.id == job.repository_id)
        return list(self.db.scalars(statement.order_by(Repository.id)).all())

    def _ignore_patterns(self, repository_id: int) -> list[str]:
        from packages.common.models import RepositoryIgnoreRule

        return list(
            self.db.scalars(
                select(RepositoryIgnoreRule.pattern).where(
                    RepositoryIgnoreRule.repository_id == repository_id
                )
            ).all()
        )
