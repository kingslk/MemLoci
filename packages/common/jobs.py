"""Job 取消与孤儿回收。Redis 只负责投递，状态以 PostgreSQL 为准。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.common.models import Job, JobStep

CANCELABLE = {"queued", "running", "paused", "retryable", "cancel_requested"}
TERMINAL = {"succeeded", "cancelled"}


class JobCancelled(RuntimeError):
    """协作式取消：调用方应停止副作用，不要再把 Job 写成 succeeded/failed。"""

    def __init__(self, job_id: int) -> None:
        super().__init__(f"Job {job_id} 已取消")
        self.job_id = job_id


def claim_job(db: Session, job_id: int) -> Job | None:
    """原子认领 queued Job；重复 Redis 消息只能有一个进入业务逻辑。"""

    job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
    if not job or job.status != "queued":
        db.rollback()
        return None
    job.status = "running"
    job.started_at = job.started_at or datetime.now(UTC)
    job.finished_at = None
    db.commit()
    return job


def request_cancel(db: Session, job: Job) -> Job:
    """排队任务直接取消；运行中任务等 Worker 确认，禁止旧执行未停就重试。"""

    if job.status in TERMINAL:
        return job
    if job.status == "running":
        job.status = "cancel_requested"
        db.commit()
        return job
    if job.status == "cancel_requested":
        return job
    job.status = "cancelled"
    job.finished_at = job.finished_at or datetime.now(UTC)
    if not job.current_stage:
        job.current_stage = "已取消"
    db.commit()
    return job


def raise_if_cancelled(db: Session, job: Job) -> None:
    db.refresh(job)
    if job.status in {"cancelled", "cancel_requested"}:
        if job.status == "cancel_requested":
            job.status = "cancelled"
            job.finished_at = job.finished_at or datetime.now(UTC)
            db.commit()
        raise JobCancelled(job.id)


def reclaim_orphaned_jobs(
    db: Session,
    *,
    interrupt_running: bool = False,
    stale_running_seconds: int | None = 600,
) -> dict[str, int]:
    """清掉没有 Worker 认领的中间态。

    - cancel_requested：任何进程启动都应收成 cancelled。重启时必现卡死就是这条。
    - running：只在 Worker 启动时整批中断；API 启动只收超过 stale 阈值的，避免误杀还在跑的任务。
    """

    now = datetime.now(UTC)
    jobs = list(
        db.scalars(
            select(Job).where(Job.status.in_(["cancel_requested", "running", "paused"]))
        ).all()
    )
    cancelled = 0
    interrupted = 0
    for job in jobs:
        if job.status == "cancel_requested":
            job.status = "cancelled"
            job.finished_at = now
            job.error = job.error or "取消已确认（工作进程已退出）"
            cancelled += 1
            _fail_running_steps(db, job, job.error)
            continue
        if job.status != "running":
            continue
        stale = False
        if stale_running_seconds is not None and job.updated_at is not None:
            updated = job.updated_at
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=UTC)
            stale = now - updated >= timedelta(seconds=stale_running_seconds)
        if interrupt_running or stale:
            job.status = "failed"
            job.finished_at = now
            job.error = "工作进程中断，可重试"
            interrupted += 1
            _fail_running_steps(db, job, job.error)
    db.commit()
    return {"cancelled": cancelled, "interrupted": interrupted}


def _fail_running_steps(db: Session, job: Job, error: str) -> None:
    for step in db.scalars(select(JobStep).where(JobStep.job_id == job.id)).all():
        if step.status in {"running", "queued", "cancel_requested"}:
            if step.status == "running":
                step.status = "failed"
                step.error = error
                step.finished_at = datetime.now(UTC)
