from packages.common.models import Job, Project, Repository
from packages.initialization.service import INITIALIZATION_STAGES, InitializationService


def test_initialization_checkpoint_is_resumeable(db) -> None:
    project = Project(name="shop")
    db.add(project)
    db.flush()
    db.add(
        Repository(
            project_id=project.id,
            name="backend",
            gitlab_project_id="1",
            clone_url="https://gitlab.example.com/backend.git",
            release_branch="main",
        )
    )
    db.flush()
    service = InitializationService(db)
    job = service.create(project.id)

    job = service.run(job.id, stage_results={"current_state_scan": {"files": 3}})
    assert job.status == "succeeded"
    assert job.progress == 1.0
    assert job.checkpoint["completed_stages"] == list(INITIALIZATION_STAGES)
    assert "genesis_dream" not in job.checkpoint["completed_stages"]
    assert job.checkpoint.get("child_job_id")
    assert len(service.steps(job.id)) == len(INITIALIZATION_STAGES)
    assert all(step.status == "succeeded" for step in service.steps(job.id))

    retryable = service.create(project.id)
    retryable.status = "failed"
    for step in service.steps(retryable.id):
        step.status = "failed"
    db.commit()
    retried = service.retry(retryable.id)
    assert retried.status == "queued"
    assert retried.retry_count == 1
    assert all(step.retry_count == 1 for step in service.steps(retryable.id))

    callback_job = service.create(project.id)
    callback_job = service.run(
        callback_job.id,
        stage_runner=lambda _job, stage: {"stage": stage, "verified": True},
    )
    assert callback_job.status == "succeeded"
    assert all(result["verified"] for result in callback_job.checkpoint["stage_results"].values())

    cancelled = service.create(project.id)
    service.request_cancel(cancelled.id)
    cancelled = service.run(cancelled.id)
    assert cancelled.status == "cancelled"
    assert service.progress(project.id)[0].project_id == project.id

    queued = service.create(project.id)
    stopped = service.request_cancel(queued.id)
    assert stopped.status == "cancelled"

    running_cancel = service.create(project.id)
    running_cancel.status = "running"
    db.commit()
    stopping = service.request_cancel(running_cancel.id)
    assert stopping.status == "cancel_requested"
    try:
        service.retry(stopping.id)
        raise AssertionError("cancel_requested job should not be retryable")
    except ValueError:
        pass
    assert service.run(stopping.id).status == "cancelled"

    failed = service.create(project.id)
    failed.status = "failed"
    db.commit()
    failed_id = failed.id
    service.delete(failed_id)
    assert db.get(Job, failed_id) is None

    running = service.create(project.id)
    try:
        service.delete(running.id)
        raise AssertionError("running job should not be deletable")
    except ValueError:
        pass


def test_reclaim_orphaned_cancel_and_stale_running(db) -> None:
    from datetime import UTC, datetime, timedelta

    from packages.common.jobs import reclaim_orphaned_jobs
    from packages.common.models import Job

    project = Project(name="reclaim")
    db.add(project)
    db.flush()
    stuck = Job(
        project_id=project.id,
        kind="full_initialization",
        status="cancel_requested",
        current_stage="genesis_dream",
    )
    stale = Job(
        project_id=project.id,
        kind="mirror_sync",
        status="running",
        current_stage="正在整理初始记忆",
        updated_at=datetime.now(UTC) - timedelta(minutes=20),
    )
    db.add_all([stuck, stale])
    db.commit()

    result = reclaim_orphaned_jobs(db, interrupt_running=False, stale_running_seconds=600)
    assert result["cancelled"] == 1
    assert result["interrupted"] == 1
    assert db.get(Job, stuck.id).status == "cancelled"
    assert db.get(Job, stale.id).status == "failed"


def test_job_can_only_be_claimed_once(db) -> None:
    from packages.common.jobs import claim_job

    project = Project(name="claim")
    db.add(project)
    db.flush()
    job = Job(project_id=project.id, kind="memory_polish", status="queued")
    db.add(job)
    db.commit()

    assert claim_job(db, job.id).status == "running"
    assert claim_job(db, job.id) is None


def test_progress_does_not_go_backwards_within_a_stage(db) -> None:
    from packages.common.models import Job
    from packages.initialization.pipeline import InitializationPipeline

    project = Project(name="progress")
    db.add(project)
    db.flush()
    job = Job(
        project_id=project.id,
        kind="full_initialization",
        status="running",
        current_stage="full_history_scan",
        progress=0.2,
        checkpoint={"completed_stages": ["current_state_scan"]},
    )
    db.add(job)
    db.commit()
    pipeline = InitializationPipeline(db)
    pipeline._report(job, "整理 diff 86/86", 1.0, completed=86, total=86)
    after_diff = job.progress
    pipeline._report(job, "抽取经验 1/86", 0.5, completed=1, total=86)
    assert job.progress >= after_diff
    assert after_diff > 0.2
