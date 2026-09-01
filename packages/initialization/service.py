"""初始化的持久化状态机。打磨近邻不在这里跑，成功后另开任务。"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.common.jobs import JobCancelled, request_cancel
from packages.common.models import Job, JobStep, Repository

INITIALIZATION_STAGES = (
    "current_state_scan",
    "full_history_scan",
    "architecture_epochs",
    "topic_reconstruction",
)
# 顺序固定：先有当前代码和历史 Evidence，才能做 Epoch/Topic。近邻打磨是后续任务。
MAX_INITIALIZATION_RETRIES = 3
DELETABLE_JOB_STATUSES = {"failed", "cancelled"}
POLISH_KIND = "memory_polish"


class InitializationService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, project_id: int, repository_id: int | None = None) -> Job:
        job = Job(
            project_id=project_id,
            repository_id=repository_id,
            kind="full_initialization",
            status="queued",
            checkpoint={"completed_stages": [], "stage_results": {}},
        )
        self.db.add(job)
        self.db.flush()
        self._ensure_steps(job)
        self.db.commit()
        return job

    def get(self, job_id: int) -> Job:
        job = self.db.get(Job, job_id)
        if not job:
            raise ValueError("Job 不存在")
        if job.status == "succeeded":
            return job
        return job

    def run(
        self,
        job_id: int,
        *,
        stage_results: dict[str, Any] | None = None,
        stage_runner: Callable[[Job, str], dict[str, Any]] | None = None,
    ) -> Job:
        job = self.get(job_id)
        if job.status in {"cancelled", "succeeded"}:
            return job
        if job.status == "cancel_requested":
            job.status = "cancelled"
            job.finished_at = datetime.now(UTC)
            self.db.commit()
            return job
        checkpoint = dict(job.checkpoint or {})
        completed = list(checkpoint.get("completed_stages", []))
        results = dict(checkpoint.get("stage_results", {}))
        self._ensure_steps(job)
        job.status = "running"
        job.started_at = job.started_at or datetime.now(UTC)
        self.db.commit()
        self.db.refresh(job)
        if job.status in {"cancelled", "cancel_requested"}:
            job.status = "cancelled"
            job.finished_at = datetime.now(UTC)
            self.db.commit()
            return job

        try:
            for stage in INITIALIZATION_STAGES:
                if stage in completed:
                    # 已完成 Pass 不重跑；中断后从 checkpoint 继续，避免重复打 GitLab/LLM。
                    continue
                self.db.refresh(job)
                if job.status in {"cancelled", "cancel_requested"}:
                    job.status = "cancelled"
                    job.finished_at = datetime.now(UTC)
                    self.db.commit()
                    return job
                if job.status == "paused":
                    return job
                job.current_stage = stage
                job.progress = max(job.progress or 0.0, len(completed) / len(INITIALIZATION_STAGES))
                steps = self._steps(job, stage)
                for step in steps:
                    step.status = "running"
                    step.progress = 0.0
                    step.started_at = step.started_at or datetime.now(UTC)
                self.db.commit()
                if stage_results and stage in stage_results:
                    result = stage_results[stage]
                elif stage_runner:
                    result = stage_runner(job, stage)
                else:
                    result = {"status": "completed"}
                self.db.refresh(job)
                if job.status in {"cancelled", "cancel_requested"}:
                    job.status = "cancelled"
                    job.finished_at = datetime.now(UTC)
                    self.db.commit()
                    return job
                results[stage] = result
                completed.append(stage)
                checkpoint = {
                    "completed_stages": completed,
                    "stage_results": results,
                    "detail": (
                        job.checkpoint.get("detail")
                        if isinstance(job.checkpoint, dict)
                        else None
                    ),
                }
                job.checkpoint = checkpoint
                job.progress = max(job.progress or 0.0, len(completed) / len(INITIALIZATION_STAGES))
                job.updated_at = datetime.now(UTC)
                for step in steps:
                    step.status = "succeeded"
                    step.progress = 1.0
                    step.checkpoint = {"result": result}
                    step.finished_at = datetime.now(UTC)
                self.db.commit()
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
            job.updated_at = datetime.now(UTC)
            for step in self._steps(job, job.current_stage):
                step.status = "failed"
                step.error = job.error
            self.db.commit()
            raise

        self.db.refresh(job)
        if job.status in {"cancelled", "cancel_requested"}:
            job.status = "cancelled"
            job.finished_at = job.finished_at or datetime.now(UTC)
            self.db.commit()
            return job
        job.status = "succeeded"
        job.current_stage = "completed"
        job.progress = 1.0
        job.finished_at = datetime.now(UTC)
        job.checkpoint = {"completed_stages": completed, "stage_results": results}
        self.db.commit()
        if job.kind == "full_initialization":
            self.spawn_polish(job)
            self.db.refresh(job)
        return job

    def pause(self, job_id: int) -> Job:
        job = self.get(job_id)
        if job.status in {"queued", "running"}:
            job.status = "paused"
            self.db.commit()
        return job

    def request_cancel(self, job_id: int) -> Job:
        return request_cancel(self.db, self.get(job_id))

    def delete(self, job_id: int) -> None:
        job = self.get(job_id)
        if job.status not in DELETABLE_JOB_STATUSES:
            raise ValueError("只有已取消或失败的任务可以删除")
        self.db.delete(job)
        self.db.commit()

    def spawn_polish(self, parent: Job) -> Job | None:
        if parent.status != "succeeded" or parent.project_id is None:
            return None
        checkpoint = dict(parent.checkpoint or {})
        existing_id = checkpoint.get("child_job_id")
        if existing_id:
            existing = self.db.get(Job, int(existing_id))
            if existing and existing.status in {"queued", "running", "paused"}:
                return existing
        job = Job(
            project_id=parent.project_id,
            repository_id=parent.repository_id,
            kind=POLISH_KIND,
            status="queued",
            current_stage="等待打磨近邻记忆",
            checkpoint={"parent_job_id": parent.id},
        )
        self.db.add(job)
        self.db.flush()
        checkpoint["child_job_id"] = job.id
        parent.checkpoint = checkpoint
        self.db.commit()
        return job

    def retry(self, job_id: int) -> Job:
        job = self.get(job_id)
        if job.status not in {"failed", "retryable", "cancelled"}:
            raise ValueError("只有失败、可重试或已取消 Job 才能重试")
        if job.retry_count >= MAX_INITIALIZATION_RETRIES:
            raise ValueError(f"Job 已达到最大重试次数 {MAX_INITIALIZATION_RETRIES}")
        job.status = "queued"
        job.retry_count += 1
        job.error = None
        for step in self.steps(job.id):
            if step.status in {"failed", "cancelled", "retryable"}:
                step.status = "queued"
                step.retry_count += 1
                step.error = None
                step.finished_at = None
        self.db.commit()
        return job

    def progress(self, project_id: int) -> list[Job]:
        return list(
            self.db.scalars(
                select(Job)
                .where(Job.project_id == project_id, Job.kind == "full_initialization")
                .order_by(Job.created_at.desc())
            ).all()
        )

    def steps(self, job_id: int) -> list[JobStep]:
        self.get(job_id)
        return list(
            self.db.scalars(
                select(JobStep)
                .where(JobStep.job_id == job_id)
                .order_by(JobStep.repository_id, JobStep.stage)
            ).all()
        )

    def _ensure_steps(self, job: Job) -> None:
        repositories = self.db.scalars(
            select(Repository).where(
                Repository.project_id == job.project_id,
                *([Repository.id == job.repository_id] if job.repository_id is not None else []),
            )
        ).all()
        existing = {
            (step.repository_id, step.stage)
            for step in self.db.scalars(select(JobStep).where(JobStep.job_id == job.id)).all()
        }
        for repository in repositories:
            for stage in INITIALIZATION_STAGES:
                if (repository.id, stage) not in existing:
                    self.db.add(
                        JobStep(
                            job_id=job.id,
                            repository_id=repository.id,
                            stage=stage,
                            checkpoint={},
                        )
                    )
        self.db.flush()

    def _steps(self, job: Job, stage: str) -> list[JobStep]:
        return list(
            self.db.scalars(
                select(JobStep).where(
                    JobStep.job_id == job.id,
                    JobStep.stage == stage,
                )
            ).all()
        )
