"""后台任务入口；每个 Handler 都以数据库状态和唯一键保证幂等。"""

from __future__ import annotations

import logging
import time
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import dramatiq
from dramatiq.middleware import TimeLimitExceeded
from sqlalchemy import select

from apps.worker.main import configure_broker
from packages.code_intelligence.service import CodeIndexer
from packages.common.config import get_settings
from packages.common.db import SessionLocal
from packages.common.jobs import JobCancelled, claim_job, raise_if_cancelled
from packages.common.models import (
    CodeFile,
    Job,
    Memory,
    Project,
    ReleaseChange,
    Repository,
    RepositoryIgnoreRule,
)
from packages.dreaming.service import DreamService
from packages.evidence.service import EvidenceService
from packages.gitlab.client import GitLabClient
from packages.gitlab.ignore import IgnoreMatcher
from packages.gitlab.mirror import RepositoryMirror
from packages.gitlab.sync import RepositorySyncService
from packages.initialization.pipeline import InitializationPipeline
from packages.initialization.service import InitializationService

configure_broker()


# 这些任务有持久化进度和协作式取消，总耗时随仓库规模变化，不能设置固定总时限。
NO_TIME_LIMIT = float("inf")
logger = logging.getLogger(__name__)


def enqueue_nightly_dreams(now: datetime | None = None) -> int:
    """每天过配置小时后，为仍有 Candidate 的项目最多创建一个增量 Dream。"""
    settings = get_settings()
    if not settings.auto_dream_enabled:
        return 0
    local_now = (now or datetime.now(UTC)).astimezone(ZoneInfo(settings.auto_dream_timezone))
    if local_now.hour < settings.auto_dream_hour:
        return 0
    day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
    created = 0
    with SessionLocal() as db:
        project_ids = list(
            db.scalars(
                select(Memory.project_id).where(Memory.status == "candidate").distinct()
            ).all()
        )
        for project_id in project_ids:
            db.scalar(select(Project).where(Project.id == project_id).with_for_update())
            existing = db.scalar(
                select(Job)
                .where(
                    Job.project_id == project_id,
                    Job.kind == "dream_incremental",
                    Job.created_at >= day_start,
                )
                .order_by(Job.created_at.desc())
                .limit(1)
            )
            if existing:
                db.rollback()
                continue
            job = Job(
                project_id=project_id,
                kind="dream_incremental",
                status="queued",
                current_stage="等待夜间整理",
                checkpoint={
                    "dream_type": "incremental",
                    "automatic": True,
                    "schedule_date": local_now.date().isoformat(),
                },
            )
            db.add(job)
            db.commit()
            try:
                run_dream.send(job.id)
            except Exception as exc:
                job.status = "failed"
                job.error = f"夜间任务投递失败: {exc.__class__.__name__}"
                job.finished_at = datetime.now(UTC)
                db.commit()
                continue
            created += 1
    return created


def auto_dream_scheduler_loop() -> None:
    """Worker 内轻量定时检查；数据库行锁和当天 Job 负责多进程去重。"""
    while True:
        try:
            enqueue_nightly_dreams()
        except Exception:
            logger.exception("夜间自动 Dream 检查失败")
        time.sleep(60)


@dramatiq.actor(max_retries=3, min_backoff=2_000)
def process_release_change(change_id: int) -> None:
    """处理一次正式分支变更；状态字段和业务唯一键共同保证任务重试不重复写入。"""
    db = SessionLocal()
    try:
        change = db.get(ReleaseChange, change_id)
        if not change or change.processing_status == "succeeded":
            # Dramatiq 至少一次投递；状态和 Evidence 唯一键保证幂等。
            return
        repository = db.get(Repository, change.repository_id)
        if not repository:
            change.processing_status = "failed"
            change.payload = {**(change.payload or {}), "error": "repository_not_found"}
            db.commit()
            return
        change.processing_status = "processing"
        db.commit()
        settings = get_settings()
        mirror = RepositoryMirror(
            settings.mirror_root,
            token=settings.gitlab_token,
            ssl_verify=settings.gitlab_ssl_verify,
        )
        changed_files: list[dict[str, str]] = []
        try:
            mirror.sync(repository.id, repository.clone_url, repository.release_branch)
            changed_files = mirror.changed_files(
                repository.id, change.before_sha or change.after_sha, change.after_sha
            )
            ignore_patterns = list(
                db.scalars(
                    select(RepositoryIgnoreRule.pattern).where(
                        RepositoryIgnoreRule.repository_id == repository.id
                    )
                ).all()
            )
            changed_files = [
                item
                for item in changed_files
                if not IgnoreMatcher(ignore_patterns).matches(item.get("path", ""))
            ]
            if db.scalar(
                select(CodeFile.id).where(CodeFile.repository_id == repository.id).limit(1)
            ):
                CodeIndexer(db, mirror).incremental_update(
                    repository,
                    sha=change.after_sha,
                    changed_files=changed_files,
                    ignore_patterns=ignore_patterns,
                )
            else:
                CodeIndexer(db, mirror).index_snapshot(
                    repository,
                    sha=change.after_sha,
                    ignore_patterns=ignore_patterns,
                )
        except RuntimeError as exc:
            change.payload = {**(change.payload or {}), "mirror_error": str(exc)}
        evidence = EvidenceService(db).create_release_evidence(
            repository,
            change,
            title=str((change.payload or {}).get("title") or change.source_type),
            summary=str((change.payload or {}).get("message") or ""),
            changed_files=changed_files,
        )
        EvidenceService(db).candidate_from_evidence(evidence)
        change.processing_status = "succeeded"
        db.commit()
    except Exception as exc:
        db.rollback()
        change = db.get(ReleaseChange, change_id)
        if change:
            change.processing_status = "retryable"
            change.payload = {
                **(change.payload or {}),
                "error": f"{exc.__class__.__name__}: {str(exc)[:250]}",
            }
            db.commit()
        raise
    finally:
        db.close()


@dramatiq.actor(max_retries=0, time_limit=NO_TIME_LIMIT)
def run_dream(job_id: int) -> None:
    """异步执行 Dream；进度和结果统一写回 Job。"""
    db = SessionLocal()
    try:
        job = claim_job(db, job_id)
        if not job:
            return
        if job.project_id is None:
            raise ValueError("整理任务缺少 project_id")
        dream_type = str((job.checkpoint or {}).get("dream_type") or "manual")

        def update_progress(completed: int, total: int, stage: str) -> None:
            current = db.get(Job, job_id)
            if not current:
                return
            raise_if_cancelled(db, current)
            current.current_stage = stage
            current.progress = max(
                float(current.progress or 0.0), completed / max(total, 1)
            )
            checkpoint = dict(current.checkpoint or {})
            checkpoint.update({"completed": completed, "total": total, "detail": stage})
            current.checkpoint = checkpoint
            db.commit()

        run = DreamService(db).run(
            job.project_id,
            dream_type=dream_type,
            progress=update_progress,
        )
        job = db.get(Job, job_id)
        if job:
            job.status = "succeeded"
            job.current_stage = "整理完成"
            job.progress = 1.0
            job.finished_at = datetime.now(UTC)
            checkpoint = dict(job.checkpoint or {})
            checkpoint["result"] = {"dream_run_id": run.id, **(run.output_summary or {})}
            job.checkpoint = checkpoint
        db.commit()
    except JobCancelled:
        return
    except (Exception, TimeLimitExceeded) as exc:
        db.rollback()
        job = db.get(Job, job_id)
        if job and job.status not in {"cancelled", "cancel_requested"}:
            job.status = "failed"
            job.error = f"{exc.__class__.__name__}: {str(exc)[:250]}"
            job.finished_at = datetime.now(UTC)
            db.commit()
    finally:
        db.close()


@dramatiq.actor(max_retries=0, time_limit=NO_TIME_LIMIT)
def run_initialization(job_id: int) -> None:
    """执行可暂停、可重试的初始化。打磨另开任务，失败留给用户点重试。"""
    db = SessionLocal()
    try:
        if not claim_job(db, job_id):
            return
        job = InitializationPipeline(db).run(job_id)
        child_id = (job.checkpoint or {}).get("child_job_id")
        if job.status == "succeeded" and child_id:
            run_memory_polish.send(int(child_id))
    except JobCancelled:
        return
    except (Exception, TimeLimitExceeded) as exc:
        job = InitializationService(db).get(job_id)
        if job.status in {"cancelled", "cancel_requested"}:
            job.status = "cancelled"
            db.commit()
            return
        job.status = "failed"
        job.error = f"{exc.__class__.__name__}: {str(exc)[:250]}"
        db.commit()
        raise
    finally:
        db.close()


@dramatiq.actor(max_retries=0, time_limit=NO_TIME_LIMIT)
def run_memory_polish(job_id: int) -> None:
    """初始化成功后的近邻打磨，进度写回 Job。"""
    db = SessionLocal()
    try:
        if not claim_job(db, job_id):
            return
        InitializationPipeline(db).run_polish(job_id)
    except JobCancelled:
        return
    except (Exception, TimeLimitExceeded) as exc:
        job = db.get(Job, job_id)
        if job and job.status not in {"cancelled", "cancel_requested"}:
            job.status = "failed"
            job.error = f"{exc.__class__.__name__}: {str(exc)[:250]}"
            db.commit()
        return
    finally:
        db.close()


@dramatiq.actor(
    max_retries=1,
    min_backoff=5_000,
    time_limit=NO_TIME_LIMIT,
)
def run_mirror_sync(job_id: int) -> None:
    """后台同步代码镜像，并把 git 进度写入 Job。"""

    db = SessionLocal()
    try:
        if not claim_job(db, job_id):
            return
        job = db.get(Job, job_id)
        if not job or job.status == "succeeded":
            return
        repository = db.get(Repository, job.repository_id)
        if not repository:
            raise ValueError("Repository 不存在")
        job.status = "running"
        job.current_stage = "正在读取远端分支"
        job.progress = 0.02
        db.commit()

        def update_progress(stage: str, fraction: float) -> None:
            current = db.get(Job, job_id)
            if not current:
                return
            raise_if_cancelled(db, current)
            current.current_stage = stage
            current.progress = max(float(current.progress or 0.0), max(0.0, min(1.0, fraction)))
            current.checkpoint = {"stage": stage, "percent": int(current.progress * 100)}
            db.commit()

        settings = get_settings()
        with GitLabClient(
            settings.gitlab_base_url,
            settings.gitlab_token,
            verify=settings.gitlab_ssl_verify,
        ) as client:
            result = RepositorySyncService(
                db,
                client,
                RepositoryMirror(
                    settings.mirror_root,
                    token=settings.gitlab_token,
                    ssl_verify=settings.gitlab_ssl_verify,
                ),
            ).sync(repository, progress=update_progress)
        job = db.get(Job, job_id)
        if job:
            if job.status in {"cancelled", "cancel_requested"}:
                job.status = "cancelled"
            else:
                job.status = "succeeded"
                job.current_stage = "代码镜像已同步"
                job.progress = 1.0
                job.checkpoint = {
                    "remote_sha": result.remote_sha,
                    "rebuilt": result.rebuilt,
                }
            db.commit()
    except JobCancelled:
        return
    except (Exception, TimeLimitExceeded) as exc:
        db.rollback()
        job = db.get(Job, job_id)
        if job and job.status not in {"cancelled", "cancel_requested"}:
            job.status = "failed"
            job.error = f"{exc.__class__.__name__}: {str(exc)[:500]}"
            db.commit()
        if job and job.status in {"cancelled", "cancel_requested"}:
            return
        raise
    finally:
        db.close()


@dramatiq.actor(
    max_retries=1,
    min_backoff=5_000,
    time_limit=NO_TIME_LIMIT,
)
def run_history_sync(job_id: int, limit: int = 100) -> None:
    """后台导入 GitLab 历史，并把 LLM 处理进度写入现有 Job。"""

    db = SessionLocal()
    try:
        if not claim_job(db, job_id):
            return
        job = db.get(Job, job_id)
        if not job or job.status == "succeeded":
            return
        repository = db.get(Repository, job.repository_id)
        if not repository:
            raise ValueError("Repository 不存在")
        job.status = "running"
        job.current_stage = "正在读取 GitLab 历史"
        job.progress = 0.02
        db.commit()

        def update_progress(completed: int, total: int, stage: str) -> None:
            current = db.get(Job, job_id)
            if not current:
                return
            raise_if_cancelled(db, current)
            current.current_stage = stage
            current.progress = max(
                float(current.progress or 0.0), completed / max(total, 1)
            )
            current.checkpoint = {"completed": completed, "total": total, "detail": stage}
            db.commit()

        settings = get_settings()
        with GitLabClient(
            settings.gitlab_base_url,
            settings.gitlab_token,
            verify=settings.gitlab_ssl_verify,
        ) as client:
            result = RepositorySyncService(
                db,
                client,
                RepositoryMirror(
                    settings.mirror_root,
                    token=settings.gitlab_token,
                    ssl_verify=settings.gitlab_ssl_verify,
                ),
            ).import_history(repository, limit=limit, progress=update_progress)
        job = db.get(Job, job_id)
        if job:
            if job.status in {"cancelled", "cancel_requested"}:
                job.status = "cancelled"
            else:
                job.status = "succeeded"
                job.current_stage = "completed"
                job.progress = 1.0
                job.checkpoint = {"result": result}
            db.commit()
    except JobCancelled:
        return
    except (Exception, TimeLimitExceeded) as exc:
        db.rollback()
        job = db.get(Job, job_id)
        if job and job.status not in {"cancelled", "cancel_requested"}:
            job.status = "failed"
            job.error = f"{exc.__class__.__name__}: {str(exc)[:500]}"
            db.commit()
        if job and job.status in {"cancelled", "cancel_requested"}:
            return
        raise
    finally:
        db.close()
